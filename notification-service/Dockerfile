FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt firebase-admin
COPY app ./app
# One process runs the API and the delivery worker. Run more containers for more throughput: deliveries are claimed per batch.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
