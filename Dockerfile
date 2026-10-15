FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl ffmpeg \
 && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --no-cache-dir --upgrade pip internetarchive yt-dlp

COPY worker.py /app/worker.py

WORKDIR /app

EXPOSE 8080

CMD ["python3", "/app/worker.py"]
