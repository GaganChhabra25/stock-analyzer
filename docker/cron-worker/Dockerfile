FROM python:3.9-slim

ENV SUPERCRONIC_VERSION=0.2.29
ENV TZ=Europe/Berlin

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl tzdata && \
    curl -fsSL "https://github.com/aptible/supercronic/releases/download/v${SUPERCRONIC_VERSION}/supercronic-linux-amd64" \
         -o /usr/local/bin/supercronic && \
    chmod +x /usr/local/bin/supercronic && \
    apt-get purge -y curl && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/logs

CMD ["supercronic", "/app/deploy/contabo/crontab.docker"]
