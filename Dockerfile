FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Europe/Paris \
    DB_PATH=/app/data/oddium.db \
    BACKUP_DIR=/app/data/backups \
    LOG_DIR=/app/logs \
    API_CACHE_DIR=/app/data/api_cache_propline \
    LIVE_WS_HOST=127.0.0.1 \
    LIVE_WS_PORT=8765 \
    LIVE_WS_PATH=/live

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY . .

RUN mkdir -p /app/data/backups /app/data/api_cache_propline /app/logs

# Le websocket Oddium reste uniquement interne au conteneur.
# Coolify peut quand même contrôler l'état du bot grâce à /health.
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=3).read()" || exit 1

STOPSIGNAL SIGTERM
CMD ["python", "-u", "main.py"]
