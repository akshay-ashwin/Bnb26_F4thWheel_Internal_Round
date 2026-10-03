# Dev image (default target): Vite dev server. Node 24 LTS, pnpm via corepack (pinned in package.json).
FROM node:24-bookworm-slim AS dev

# CI=true keeps pnpm non-interactive. The pnpm store lives inside the node_modules named volume
# so installs can hard-link instead of copy.
ENV CI=true \
    npm_config_store_dir=/app/node_modules/.pnpm-store

RUN corepack enable
WORKDIR /app
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .
EXPOSE 5173
CMD ["pnpm", "dev", "--host", "0.0.0.0"]

FROM dev AS build
RUN pnpm build

# Prod profile: static files + same-origin /api reverse proxy (cookies work without CORS).
FROM nginx:stable-alpine AS prod
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
