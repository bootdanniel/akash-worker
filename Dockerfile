FROM debian:bookworm-slim

# CACHE BUST: força o rebuild sempre que este valor mudar
ARG CACHEBUST=1

RUN apt-get update && apt-get install -y \
    python3 python3-pip curl git ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y gh \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --break-system-packages requests

WORKDIR /app
RUN python3 -m pip install --break-system-packages --no-cache-dir internetarchive
RUN curl -fsSL https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/local/bin/yt-dlp && chmod +x /usr/local/bin/yt-dlp
COPY worker.py /app/worker.py

CMD ["python3", "-u", "/app/worker.py"]
