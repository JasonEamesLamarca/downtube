# syntax=docker/dockerfile:1.7
# DownTube container image: SPA built in a Node stage, served by one uvicorn worker.
#
# Build:   docker build -t downtube .
# Upgrade yt-dlp (YouTube breaks extractors often): docker build --build-arg YTDLP_VERSION=<ver> .

ARG NODE_IMAGE=node:24.19.0-alpine3.23
ARG BUILDPLATFORM=linux/amd64
ARG PYTHON_IMAGE=python:3.12.16-slim-bookworm
ARG DENO_VERSION=2.9.8
ARG YTDLP_VERSION=2026.10.26

# ---------------------------------------------------------------------------
# Stage: frontend. Output is platform-independent static files, so run on the
# build host's platform (avoids slow emulated npm installs on arm64 builds).
# ---------------------------------------------------------------------------
FROM --platform=$BUILDPLATFORM ${NODE_IMAGE} AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------------------
# Stage: deno. yt-dlp needs a JavaScript runtime to solve YouTube's JS
# challenges (EJS); without one the web/tv clients are dropped.
# ---------------------------------------------------------------------------
FROM denoland/deno:bin-${DENO_VERSION} AS deno

# ---------------------------------------------------------------------------
# Stage: runtime
# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime
ARG YTDLP_VERSION
ARG PUID=1000
ARG PGID=1000

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates tini \
 && rm -rf /var/lib/apt/lists/*

COPY --from=deno /deno /usr/local/bin/deno

# Non-editable install of the backend, plus yt-dlp with its default extras
# (yt-dlp-ejs). The source tree is removed in the same layer.
# NOTE: setuptools rejects paths outside the project dir, so backend/pyproject.toml
# must not reference ../README.md (currently does; must be fixed in backend/).
COPY backend /tmp/src/backend
RUN pip install --no-cache-dir --root-user-action=ignore --upgrade pip && pip install --no-cache-dir --root-user-action=ignore /tmp/src/backend "yt-dlp[default]==${YTDLP_VERSION}" \
 && rm -rf /tmp/src

COPY --from=frontend /build/dist /app/static

RUN groupadd --system --gid "${PGID}" downtube \
 && useradd --system --uid "${PUID}" --gid "${PGID}" --home-dir /nonexistent --no-create-home \
        --shell /usr/sbin/nologin downtube \
 && mkdir -p /data /temp /logs /archive \
 && chown -R downtube:downtube /data /temp /logs /archive

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=UTC \
    HOME=/tmp \
    DENO_DIR=/tmp/deno \
    DOWNTUBE_DATA_DIR=/data \
    DOWNTUBE_TEMP_DIR=/temp \
    DOWNTUBE_LOG_DIR=/logs \
    DOWNTUBE_STORAGE_MOUNTS=/archive \
    DOWNTUBE_ARCHIVE_ROOT=/archive \
    DOWNTUBE_BASE_URL=http://localhost:8092 \
    DOWNTUBE_LOG_LEVEL=INFO \
    DOWNTUBE_YTDLP_BIN=yt-dlp \
    DOWNTUBE_FFPROBE_BIN=ffprobe \
    DOWNTUBE_STATIC_DIR=/app/static \
    PUID=${PUID} \
    PGID=${PGID}

LABEL org.opencontainers.image.title="DownTube" \
      org.opencontainers.image.description="Self-hosted YouTube archiver for liked videos and subscriptions (original quality up to 4K, MP4)" \
      org.opencontainers.image.source="https://github.com/JasonEamesLamarca/downtube" \
      org.opencontainers.image.url="https://github.com/JasonEamesLamarca/downtube" \
      org.opencontainers.image.licenses="MIT"

WORKDIR /app
USER downtube
EXPOSE 8092

# No curl in the image: probe the health endpoint with the Python stdlib.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8092/api/health', timeout=4).status == 200 else 1)"]

# tini reaps yt-dlp/ffmpeg child processes and forwards signals to uvicorn.
ENTRYPOINT ["/usr/bin/tini", "--"]
# Single worker by design: the download manager and scheduler live in-process.
# X-Forwarded-* headers are trusted only from FORWARDED_ALLOW_IPS (read by uvicorn).
# Set it to your reverse proxy's address (e.g. the proxy container's IP) when using one.
ENV FORWARDED_ALLOW_IPS=127.0.0.1
CMD ["uvicorn", "downtube.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8092", \
     "--workers", "1", "--proxy-headers"]
