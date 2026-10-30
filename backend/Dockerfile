# syntax=docker/dockerfile:1

FROM python:3.12-slim

WORKDIR /app

RUN success=0; \
  for attempt in 1 2 3 4 5; do \
    if apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*; then success=1; break; fi; \
    echo "apt-get attempt ${attempt} failed — retrying..." >&2; \
    sleep 5; \
  done; \
  if [ "$success" != "1" ]; then \
    echo "apt-get failed after 5 attempts — see the last attempt's error above." >&2; \
    exit 1; \
  fi
# Same proxy unreliability as the dependency-install step below, observed
# here too (a corrupted partial download of the uv wheel itself) — same
# bounded retry treatment, before uv even exists to install anything else.
RUN --mount=type=cache,target=/root/.cache/pip \
  success=0; \
  for attempt in 1 2 3 4 5; do \
    if pip install uv; then success=1; break; fi; \
    echo "pip install uv attempt ${attempt} failed — retrying..." >&2; \
    sleep 5; \
  done; \
  if [ "$success" != "1" ]; then \
    echo "pip install uv failed after 5 attempts — see the last attempt's error above." >&2; \
    exit 1; \
  fi

COPY pyproject.toml README.md alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY scripts ./scripts

# This environment's build-network proxy has been observed to interrupt
# large dependency downloads mid-transfer (e.g. av==18.1.0's ~33MB wheel)
# with TLS/connection-reset errors, not a real dependency problem. Three
# mitigations, none of which touch application dependencies or disable
# TLS verification:
#   - UV_HTTP_TIMEOUT raised well above uv's 30s default so a slow/retried
#     transfer isn't abandoned prematurely.
#   - UV_CONCURRENT_DOWNLOADS lowered so fewer simultaneous connections
#     compete for whatever bandwidth/reliability the proxy actually has.
#   - A BuildKit cache mount for uv's own download cache (~/.cache/uv) so
#     a package that already downloaded successfully in an earlier,
#     overall-failed build attempt is never re-fetched on retry.
# uv has no built-in "max retries" knob beyond its own fixed internal
# retry count, so the loop below is this Dockerfile's retry layer: each
# pass only has to make progress on whatever didn't complete last time,
# since everything already cached is instant.
ENV UV_HTTP_TIMEOUT=300
ENV UV_CONCURRENT_DOWNLOADS=4
RUN --mount=type=cache,target=/root/.cache/uv \
  success=0; \
  for attempt in 1 2 3 4 5; do \
    if uv pip install --system -e .; then success=1; break; fi; \
    echo "uv pip install attempt ${attempt} failed — retrying (cache retains whatever already downloaded)..." >&2; \
    sleep 5; \
  done; \
  if [ "$success" != "1" ]; then \
    echo "uv pip install failed after 5 attempts — see the last attempt's error above." >&2; \
    exit 1; \
  fi \
  && sed -i 's/\r$//' /app/scripts/docker-entrypoint.sh \
  && chmod +x /app/scripts/docker-entrypoint.sh

ENV PYTHONUNBUFFERED=1
ENV PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/api/v1/health" || exit 1

ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
