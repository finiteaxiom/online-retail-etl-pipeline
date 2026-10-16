FROM python:3.13-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY run_etl.py .
COPY retail_etl/ ./retail_etl/

ENTRYPOINT ["python", "run_etl.py"]