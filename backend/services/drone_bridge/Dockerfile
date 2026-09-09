FROM python:3.11-slim
WORKDIR /app
ENV PYTHONPATH=/app/shared:/app/services
COPY shared/ /app/shared/
COPY services/drone_bridge/requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY services/drone_bridge/ /app/services/drone_bridge/
EXPOSE 8000
CMD ["uvicorn", "drone_bridge.main:app", "--host", "0.0.0.0", "--port", "8000"]