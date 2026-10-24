FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Run the service as an unprivileged user. The evaluation-log directory is the
# only path the application writes to, so it is created and owned here. When a
# host directory is bind-mounted over it, that directory must be writable by
# this UID on the host (see docker-compose.prod.yml).
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /app/logs \
    && chown -R app:app /app/logs

COPY app ./app

USER app

EXPOSE 8000

# Liveness probe; it does not call any LLM provider and never needs a key.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"]

# No --reload in production. The backend is only reachable through the Nginx
# reverse proxy on the Compose network, so its X-Forwarded-* headers are trusted
# for the client IP and scheme in access logs. Do not publish this port.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
