# syntax=docker/dockerfile:1.7
FROM --platform=$BUILDPLATFORM maven:3.9.16-eclipse-temurin-21-alpine AS build

WORKDIR /workspace
COPY pom.xml ./
COPY src ./src
RUN --mount=type=cache,target=/root/.m2 mvn -B -Dmaven.artifact.threads=1 -DskipTests package \
    && cp "$(find target -maxdepth 1 -type f -name '*.jar' ! -name '*.original' -print -quit)" /tmp/business.jar

FROM eclipse-temurin:21.0.12.1_1-jre-alpine-3.23 AS runtime

ARG VERSION=0.0.0-dev
ARG REVISION=unknown
ARG CREATED=unknown
LABEL org.opencontainers.image.title="Survey Platform Service" \
      org.opencontainers.image.description="Survey platform production business API" \
      org.opencontainers.image.source="https://github.com/lsgoodlionel/star-survey" \
      org.opencontainers.image.version="$VERSION" \
      org.opencontainers.image.revision="$REVISION" \
      org.opencontainers.image.created="$CREATED"

ENV PLATFORM_RESPONSE_EXPORT_DIR=/var/lib/survey/exports \
    PLATFORM_ASSET_DIR=/var/lib/survey/assets

RUN addgroup -S -g 10001 survey \
    && adduser -S -D -H -u 10001 -G survey survey \
    && mkdir -p /var/lib/survey/exports /var/lib/survey/assets \
    && chown -R 10001:10001 /var/lib/survey

WORKDIR /app
COPY --from=build /tmp/business.jar /app/business.jar

USER 10001:10001
VOLUME ["/var/lib/survey"]
EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=6 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/actuator/health || exit 1

ENTRYPOINT ["java", "-jar", "/app/business.jar"]
