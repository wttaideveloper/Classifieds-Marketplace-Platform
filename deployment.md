# Production Deployment Guide

Deploy the Classifieds Marketplace API on **AWS EC2 (Ubuntu)** with **Docker**, **RDS PostgreSQL**, **Nginx**, and **Let's Encrypt SSL**.

## Architecture

```
Internet → Nginx (:443/:80) → Gunicorn/Uvicorn (:8000) → RDS PostgreSQL (:5432)
```

## Prerequisites

- AWS account with EC2 and RDS access
- Domain name pointed at your EC2 Elastic IP
- Docker installed on EC2
- PostgreSQL RDS instance in the same VPC as EC2

## 1. Environment variables

Copy `.env.example` to `.env` on the server (never commit `.env`):

```bash
cp .env.example .env
```

Production values:

| Variable | Description |
|----------|-------------|
| `ENVIRONMENT` | Set to `production` |
| `DATABASE_URL` | `postgresql+psycopg2://user:pass@rds-endpoint:5432/dbname?sslmode=require` |
| `SECRET_KEY` | Long random string (`openssl rand -hex 32`) |
| `FRONTEND_URL` | Public frontend URL, e.g. `https://app.example.com` |
| `CORS_ORIGINS` | Comma-separated allowed origins, e.g. `https://app.example.com,http://localhost:3000` |
| `CORS_ALLOW_LOCALHOST` | Set to `true` to allow browser requests from `localhost` / `127.0.0.1` on any port |
| `GOOGLE_*` | Google OAuth credentials |
| `email_user` / `email_pass` | SMTP credentials |

In production:

- `ENVIRONMENT=production` disables `create_all` on startup and hides `/docs`.
- Schema changes must be applied with Alembic.

## 2. AWS RDS setup

1. Create a PostgreSQL RDS instance in a **private subnet**.
2. Create database and application user with least privilege.
3. Security group: allow **inbound 5432 only from the EC2 security group**.
4. Enable encryption, automated backups, and `sslmode=require` in `DATABASE_URL`.

## 3. Build Docker image

From the project root:

```bash
docker build -t marketplace-api .
```

## 4. Run database migrations

Before starting the app in production:

```bash
docker run --rm --env-file .env marketplace-api alembic upgrade head
```

For local development:

```bash
alembic upgrade head
```

Generate a new migration after model changes:

```bash
alembic revision --autogenerate -m "describe change"
alembic upgrade head
```

## 5. Run the application container

```bash
docker run -d \
  --name marketplace-api \
  --restart unless-stopped \
  --env-file .env \
  -p 127.0.0.1:8000:8000 \
  -v /data/uploads:/app/uploads \
  marketplace-api
```

Bind port `8000` to localhost only; Nginx handles public traffic.

## 6. EC2 setup

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y docker.io nginx certbot python3-certbot-nginx
sudo usermod -aG docker $USER
```

Create upload directory (host bind mount for `/app/uploads` in the container):

```bash
sudo mkdir -p /data/uploads
sudo chown -R 1000:1000 /data/uploads
```

Set in `.env` (never use a host home path such as `/home/ubuntu/...` inside the container):

```env
UPLOAD_DIR=/app/uploads
```

## 7. Nginx reverse proxy

See `deploy/nginx-marketplace.conf.example` for a combined frontend + API + Socket.IO setup.

**Important:** If only `/api/*` is proxied to the backend (common with Next.js on the same domain),
set in production `.env`:

```env
SOCKETIO_PATH=/api/socket.io
PUBLIC_API_BASE_URL=http://13.207.85.164
```

Frontend Socket.IO client:

```javascript
io("http://13.207.85.164", {
  path: "/api/socket.io",
  auth: { token: "<JWT>" },
});
```

Verify deployment:

```bash
curl "http://13.207.85.164/api/socket.io?EIO=4&transport=polling"
# Expected: Engine.IO open packet (starts with 0{...}), NOT a Next.js 404 page
```

`/etc/nginx/sites-available/marketplace-api` (API-only subdomain example):

```nginx
server {
    listen 80;
    server_name api.example.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 86400;
    }
}
```

With API-only nginx, keep `SOCKETIO_PATH=/socket.io`.

Ensure Gunicorn serves **`app.main:socket_app`** (not `app.main:app`). The Docker entrypoint already does this.

**Socket.IO + Gunicorn workers:** Engine.IO stores polling sessions **in memory per worker**. The entrypoint defaults to `WEB_CONCURRENCY=1`. If you run multiple workers (`-w 4`), polling POST returns **400 `Invalid session`** when the POST hits a different worker than the GET handshake — even though auth and nginx are fine.

Fix options:

| Option | Config |
|--------|--------|
| Single worker (recommended for small deploys) | `WEB_CONCURRENCY=1` |
| Multi-worker | Set `SOCKETIO_REDIS_URL=redis://host:6379/0` **and** `WEB_CONCURRENCY=4` |

Verify polling (GET then POST with same `sid` must both succeed):

```bash
SID=$(curl -s 'http://13.207.85.164/api/socket.io/?EIO=4&transport=polling' | sed -n 's/.*"sid":"\([^"]*\)".*/\1/p')
curl -s -w "\nHTTP:%{http_code}\n" -X POST \
  "http://13.207.85.164/api/socket.io/?EIO=4&transport=polling&sid=$SID" \
  -H 'Content-Type: text/plain;charset=UTF-8' \
  -d '40'
# Expected: HTTP:200 and body OK — not "Invalid session"
```

**Frontend client (socket.io-client v4):**

```javascript
import { io } from "socket.io-client";

const token = "<JWT from GET /api/v1/auth/dev-token>";

const socket = io("http://13.207.85.164", {
  path: "/api/socket.io",
  transports: ["polling", "websocket"],
  auth: { token },
  reconnection: true,
});

socket.on("connect", () => console.log("connected", socket.id));
socket.on("connect_error", (err) => console.error("connect_error", err.message));
socket.on("error", (payload) => console.error("socket error", payload));
```

Auth format `auth: { token: "<JWT>" }` is correct — the backend reads `auth.token` on connect. Auth is **not** re-sent on each polling POST; session stickiness is the issue, not token expiry.

Enable and test:

```bash
sudo ln -s /etc/nginx/sites-available/marketplace-api /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

## 8. SSL with Let's Encrypt

```bash
sudo certbot --nginx -d api.example.com
sudo certbot renew --dry-run
```

## 9. Security groups

| Resource | Inbound |
|----------|---------|
| EC2 | 22 (your IP), 80, 443 |
| RDS | 5432 from EC2 security group only |

Do **not** expose port 8000 publicly.

## 10. Seed admin (first deploy only)

```bash
docker run --rm --env-file .env marketplace-api python scripts/seed_admin.py
```

Update `scripts/seed_admin.py` credentials before running in production.

## 11. Health check

```bash
curl https://api.example.com/health
```

Expected response:

```json
{"status":"ok"}
```

## 12. Deploy updates

```bash
git pull
docker build -t marketplace-api .
docker run --rm --env-file .env marketplace-api alembic upgrade head
docker stop marketplace-api && docker rm marketplace-api
docker run -d --name marketplace-api --restart unless-stopped \
  --env-file .env -p 127.0.0.1:8000:8000 \
  -v /data/uploads:/app/uploads marketplace-api
```

## Local development

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
alembic upgrade head
uvicorn app.main:socket_app --reload
```

With `ENVIRONMENT=development`, tables are auto-created on startup if migrations have not been applied yet.

## Troubleshooting

| Issue | Check |
|-------|-------|
| **500 Internal Server Error on APIs** | Run `alembic upgrade head` — the latest migration adds `is_deleted`, `tenant_id`, `status` (string), and `enterprise_locations`. Without it, list/search endpoints fail. |
| DB connection refused | RDS security group, `DATABASE_URL`, VPC/subnet |
| `psycopg2` errors | Use `postgresql+psycopg2://` in `DATABASE_URL` |
| CORS errors | `CORS_ORIGINS` must include exact frontend origin; set `CORS_ALLOW_LOCALHOST=true` for local dev against production API |
| Missing tables | Run `alembic upgrade head` |
| Uploads lost on restart | Mount `/data/uploads` volume |
| View error details | `docker logs marketplace-api` — unhandled errors are logged server-side |

### Fix 500 after deploying new code

```bash
docker build -t marketplace-api .
docker run --rm --env-file .env marketplace-api alembic upgrade head
docker stop marketplace-api && docker rm marketplace-api
docker run -d --name marketplace-api --restart unless-stopped \
  --env-file .env -p 127.0.0.1:8000:8000 \
  -v /data/uploads:/app/uploads marketplace-api
```

Verify migration applied:

```bash
docker run --rm --env-file .env marketplace-api alembic current
```

Expected head: `c3d4e5f6a7b8`
