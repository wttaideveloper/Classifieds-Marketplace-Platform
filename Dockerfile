FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UPLOAD_DIR=/app/uploads

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

COPY . .

RUN addgroup --system app && adduser --system --ingroup app app \
    && chmod +x /app/scripts/*.sh \
    && mkdir -p /app/uploads \
    && chown -R app:app /app

USER app

EXPOSE 8000

CMD ["/app/scripts/entrypoint.sh"]
