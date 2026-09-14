FROM node:22-alpine AS build
WORKDIR /web
COPY apps/web/package*.json ./
RUN npm ci
COPY apps/web ./
RUN npm run build
FROM caddy:2-alpine
COPY --from=build /web/out /srv
COPY docker/Caddyfile /etc/caddy/Caddyfile
