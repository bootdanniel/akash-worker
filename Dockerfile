FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir requests urllib3
WORKDIR /app
COPY worker_watermark.py /app/
CMD ["python3", "-u", "/app/worker_watermark.py"]
