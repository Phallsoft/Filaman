FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN useradd -m -u 1000 filaman \
    && mkdir -p /data \
    && chown -R filaman:filaman /data /app
USER filaman

ENV DB_PATH=/data/filaman.db
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
