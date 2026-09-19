FROM python:3.11-slim
WORKDIR /app
COPY services/billing_service/main.py /app/
RUN pip install --no-cache-dir fastapi uvicorn pydantic
EXPOSE 8015
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8015"]
