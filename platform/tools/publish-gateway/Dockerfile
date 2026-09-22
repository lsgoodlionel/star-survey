# 发布网关服务镜像：纯标准库，不装任何额外包。
#   docker build -t survey-publish-gateway platform/tools/publish-gateway
# 运行所需的环境变量与引擎配置格式见 README.md「作为服务运行」。
FROM python:3.12-alpine

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
COPY pubgw /app/pubgw

USER 10001:10001
VOLUME ["/var/lib/pubgw"]
EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"]

# SIGTERM 时网关会等在途发布做完再退出；编排里把 stop_grace_period 设得比单次发布长。
STOPSIGNAL SIGTERM
CMD ["python", "-m", "pubgw.server"]
