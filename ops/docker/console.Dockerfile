# Console: static build served by nginx. No CDN, bundled assets only (reviewers may be in mainland China).
FROM node:22.23.3-alpine AS build
RUN corepack enable
WORKDIR /repo
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY console/package.json console/
COPY console/packages/api-client/package.json console/packages/api-client/
RUN pnpm install --frozen-lockfile
COPY spec/ spec/
COPY console/ console/
RUN pnpm --filter @safepay/api-client run generate && pnpm --filter @safepay/console run build

FROM nginx:1.30.6-alpine
COPY ops/docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /repo/console/dist /usr/share/nginx/html
EXPOSE 8080
