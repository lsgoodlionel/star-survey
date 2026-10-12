# 发布网关服务镜像：纯标准库，不装任何额外包。
#   docker build -t survey-publish-gateway platform/tools/publish-gateway
# 运行所需的环境变量与引擎配置格式见 README.md「作为服务运行」。
FROM python:3.12.15-alpine3.24

ARG VERSION=0.0.0-dev
ARG REVISION=unknown
ARG CREATED=unknown
LABEL org.opencontainers.image.title="Survey Publish Gateway" \
      org.opencontainers.image.description="Survey platform production publish gateway" \
      org.opencontainers.image.source="https://github.com/lsgoodlionel/star-survey" \
      org.opencontainers.image.version="$VERSION" \
      org.opencontainers.image.revision="$REVISION" \
      org.opencontainers.image.created="$CREATED"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PUBGW_HOST=0.0.0.0 \
    PUBGW_PORT=8080 \
    PUBGW_STATE_DIR=/var/lib/pubgw

RUN addgroup -S -g 10001 pubgw \
 && adduser -S -D -H -u 10001 -G pubgw pubgw \
 && mkdir -p /var/lib/pubgw \
 && chown pubgw:pubgw /var/lib/pubgw

WORKDIR /app
COPY --chown=10001:10001 pubgw /app/pubgw

USER 10001:10001
VOLUME ["/var/lib/pubgw"]
EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"]

# SIGTERM 时网关会等在途发布做完再退出；编排里把 stop_grace_period 设得比单次发布长。
STOPSIGNAL SIGTERM
CMD ["python", "-m", "pubgw.server"]
