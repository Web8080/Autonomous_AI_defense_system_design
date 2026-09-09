FROM python:3.11-slim
WORKDIR /app
COPY services/detections_service/requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY services/detections_service/kafka_consumer.py /app/
CMD ["python", "kafka_consumer.py"]