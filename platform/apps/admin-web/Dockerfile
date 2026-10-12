FROM node:22.23.3-alpine3.24 AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .

ARG VITE_E2E=false
ENV VITE_E2E=${VITE_E2E}
RUN npm run build \
    && if [ "$VITE_E2E" = "true" ]; then npm run assert:e2e-bundle; else npm run assert:production-bundle; fi

FROM nginx:1.30.5-alpine3.24 AS runtime

ARG VERSION=0.0.0-dev
ARG REVISION=unknown
ARG CREATED=unknown
LABEL org.opencontainers.image.title="Survey Admin Web" \
      org.opencontainers.image.description="Survey platform production administration UI" \
      org.opencontainers.image.source="https://github.com/lsgoodlionel/star-survey" \
      org.opencontainers.image.version="$VERSION" \
      org.opencontainers.image.revision="$REVISION" \
      org.opencontainers.image.created="$CREATED"

ENV PLATFORM_UPSTREAM=http://platform:8080
RUN addgroup -S -g 10001 survey \
    && adduser -S -D -H -u 10001 -G survey survey \
    && chown -R 10001:10001 /var/cache/nginx /run /etc/nginx/conf.d

COPY --from=build /app/nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html

USER 10001:10001
EXPOSE 80

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1
