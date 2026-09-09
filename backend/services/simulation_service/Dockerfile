FROM python:3.11-slim
WORKDIR /app
COPY services/simulation_service/requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY services/simulation_service/ /app/
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8014"]