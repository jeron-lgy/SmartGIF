FROM node:22-alpine AS web-build
WORKDIR /app/webui
COPY webui/package.json webui/package-lock.json ./
RUN npm ci
COPY webui/index.html webui/vite.config.js ./
COPY webui/src ./src
RUN npm run build

FROM python:3.13-slim
LABEL org.opencontainers.image.source="https://github.com/jeron-lgy/SmartGIF" \
    org.opencontainers.image.description="Web-based animated image converter for GIF, WebP, APNG and AVIF"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ANIMATION_DATA_DIR=/data

RUN DEBIAN_FRONTEND=noninteractive apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ffmpeg tk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY animation_converter.py animation_server.py ./
COPY --from=web-build /app/webui/dist ./webui/dist

RUN mkdir -p /data/uploads /data/outputs

EXPOSE 8765
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=3).read()"]

CMD ["python", "animation_server.py", "--host", "0.0.0.0", "--port", "8765", "--no-open"]
