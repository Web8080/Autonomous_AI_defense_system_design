FROM python:3.11-slim
WORKDIR /app
ENV PYTHONPATH=/app/shared
COPY shared/ /app/shared/
COPY services/mission_service/requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY services/mission_service/main.py /app/
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]