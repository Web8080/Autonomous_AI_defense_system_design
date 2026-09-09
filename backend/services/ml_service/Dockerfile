FROM python:3.11-slim
WORKDIR /app
COPY services/ml_service/requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt
# Pure modules first (no service deps); tests and eval tooling import them too.
COPY services/ml_service/eval_gate.py services/ml_service/data_versioning.py /app/
COPY services/ml_service/main.py /app/
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]