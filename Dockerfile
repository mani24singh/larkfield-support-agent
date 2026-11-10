# syntax=docker/dockerfile:1

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000 \
    RUNS_DIR=/app/runs

WORKDIR /app

# Dedicated unprivileged runtime user.
RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app --no-create-home app

# Dependencies first for layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code and read-only data pack. No secrets are copied; everything
# sensitive is read from the environment at runtime.
COPY src ./src
COPY schema ./schema
COPY data ./data
COPY policies ./policies

RUN mkdir -p /app/runs && chown -R app:app /app

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8000') + '/healthz', timeout=4).read()"

# Platforms inject $PORT; 8000 is only a local default.
CMD ["sh", "-c", "exec uvicorn src.solution:app --host 0.0.0.0 --port ${PORT:-8000}"]
