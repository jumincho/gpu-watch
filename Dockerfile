FROM python:3.12.14-alpine3.24@sha256:d09d15e60962ca365d1cd544a48773bac9d33f2fb1b00f2aa0deec78ade7dc31

ARG APP_UID=1000
ARG APP_GID=1000

RUN apk upgrade --no-cache \
    && apk add --no-cache openssh-client tzdata \
    && python -m pip uninstall --yes pip setuptools wheel

WORKDIR /app
ARG BUILD_VERSION=3
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV GPU_WATCH_BUILD_VERSION=$BUILD_VERSION
LABEL org.opencontainers.image.title="GPU Watch" \
      org.opencontainers.image.version="$BUILD_VERSION" \
      org.opencontainers.image.source="local"

COPY VERSION server.py hosts.json ./
COPY gpu_watch ./gpu_watch
COPY static ./static
COPY scripts/docker-entrypoint.sh scripts/check_local.py scripts/ssh-askpass.sh ./scripts/
RUN addgroup -S -g "$APP_GID" gpuwatch \
    && adduser -S -D -u "$APP_UID" -G gpuwatch -h /home/gpuwatch -s /sbin/nologin gpuwatch \
    && chmod +x /app/scripts/ssh-askpass.sh /app/scripts/docker-entrypoint.sh \
    && chown -R "$APP_UID:$APP_GID" /app /home/gpuwatch

USER gpuwatch

EXPOSE 8787
HEALTHCHECK --interval=20s --timeout=10s --start-period=30s --retries=3 \
    CMD ["python3", "/app/scripts/check_local.py", "--health-only", "--timeout", "5"]
ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
CMD ["python3", "server.py", "--host", "0.0.0.0", "--port", "8787"]
