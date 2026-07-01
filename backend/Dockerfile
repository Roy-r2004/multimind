FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends curl \
  && rm -rf /var/lib/apt/lists/* \
  && pip install --no-cache-dir uv

COPY pyproject.toml README.md alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY scripts ./scripts

RUN uv pip install --system -e . \
  && sed -i 's/\r$//' /app/scripts/docker-entrypoint.sh \
  && chmod +x /app/scripts/docker-entrypoint.sh

ENV PYTHONUNBUFFERED=1
ENV PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/api/v1/health" || exit 1

ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
