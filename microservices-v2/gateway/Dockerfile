# docker build -f microservices-v2/gateway/Dockerfile -t marketplace-v2-gateway .   (from the repository root)
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PYTHONPATH=/srv/gateway
WORKDIR /srv/gateway
COPY microservices-v2/gateway/requirements.txt /srv/gateway-requirements.txt
RUN pip install --upgrade pip && pip install -r /srv/gateway-requirements.txt
COPY microservices-v2/kit /srv/kit
RUN pip install --no-deps /srv/kit
COPY microservices-v2/gateway/gateway_app /srv/gateway/gateway_app
RUN addgroup --system app && adduser --system --ingroup app app
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --retries=5 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/_gateway/health', timeout=4).status == 200 else 1)"
CMD ["uvicorn", "gateway_app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
