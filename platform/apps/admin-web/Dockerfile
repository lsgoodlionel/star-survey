FROM node:22-alpine AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .

ARG VITE_E2E=false
ENV VITE_E2E=${VITE_E2E}
RUN npm run build \
    && if [ "$VITE_E2E" = "true" ]; then npm run assert:e2e-bundle; else npm run assert:production-bundle; fi

FROM nginx:1.27-alpine AS runtime

ENV PLATFORM_UPSTREAM=http://platform:8080
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
