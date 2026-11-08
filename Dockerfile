# CicaNest (nest) in one image: the backend, the built console, and the docker command line, so the console
# can run the QQ (NapCat) containers next to it. How to run it: deploy/docker/README.md.

FROM node:24-slim AS console
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM docker:29-cli AS docker-cli

FROM python:3.12-slim
# Time zones, so TZ sets the times in the logs and when daily jobs run.
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /usr/local/bin/uv
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable && rm -rf /root/.cache
COPY --from=console /src/web/dist /app/web/dist
# NEST_HOME (the data: database, logs, .env) is a mounted folder; the console stays in the image.
ENV PATH=/app/.venv/bin:$PATH \
    NEST_WEB_DIST_DIR=/app/web/dist \
    NEST_HOME=/data
WORKDIR /data
EXPOSE 18800
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('NEST_PORT', '18800') + '/api/health', timeout=4)"
CMD ["nest", "serve"]
