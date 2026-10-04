FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y \
    python3 python3-pip curl git ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Instala gh CLI
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y gh \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --break-system-packages requests

WORKDIR /app
COPY worker.py /app/worker.py

CMD ["python3", "-u", "/app/worker.py"]
