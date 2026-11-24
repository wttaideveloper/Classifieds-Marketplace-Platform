# docker build -f microservices-v2/services/moderation/Dockerfile -t marketplace-v2-moderation .
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PYTHONPATH=/srv/service UPLOAD_DIR=/app/uploads
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 && rm -rf /var/lib/apt/lists/*
WORKDIR /srv/service
COPY microservices-v2/requirements-base.txt /srv/requirements-base.txt
RUN pip install --upgrade pip && pip install -r /srv/requirements-base.txt
COPY microservices-v2/kit /srv/kit
RUN pip install /srv/kit
COPY microservices-v2/services/moderation/moderation_service /srv/service/moderation_service
RUN addgroup --system app && adduser --system --ingroup app app && mkdir -p /app/uploads && chown -R app:app /app/uploads
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --retries=5 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/_service/health', timeout=4).status == 200 else 1)"
CMD ["sh", "-c", "exec gunicorn moderation_service.main:app -k uvicorn.workers.UvicornWorker -w ${WEB_CONCURRENCY:-2} -b 0.0.0.0:8000 --timeout ${GUNICORN_TIMEOUT:-300} --forwarded-allow-ips='*'"]
