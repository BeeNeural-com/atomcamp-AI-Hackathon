# Multi-target backend image. Targets share every layer except the default command.
#   docker build --target api    -t beeproof-api .     # FastAPI public API      (Cloud Run beeproof-api)
#   docker build --target worker -t beeproof-worker .  # private pipeline worker (Cloud Run beeproof-worker)
# Migrations run from the api image: `python -m beeproof.migrate` (Cloud Run Job beeproof-migrate).

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

COPY pyproject.toml alembic.ini ./
COPY beeproof ./beeproof
COPY bench ./bench
RUN pip install ".[gcp,postgres]" \
 && useradd --create-home --uid 10001 app \
 && mkdir -p /app/data && chown app /app/data

USER app
ENV PORT=8080 STORE=local LOCAL_STORE_DIR=/app/data/store LOG_FORMAT=json
EXPOSE 8080

FROM base AS worker
CMD exec uvicorn beeproof.worker_http:app --host 0.0.0.0 --port ${PORT} --timeout-keep-alive 75

FROM base AS api
CMD exec uvicorn beeproof.api.app:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips="*"
