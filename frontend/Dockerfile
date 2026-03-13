FROM node:20-slim AS build

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    ca-certificates \
    libc6 \
    libstdc++6 \
  && rm -rf /var/lib/apt/lists/*

COPY package.json yarn.lock ./
RUN corepack enable \
  && if grep -q "yarn lockfile v1" yarn.lock; then corepack prepare yarn@1.22.22 --activate; else corepack prepare yarn@4.9.2 --activate; fi \
  && yarn --version

COPY . .
RUN rm -rf node_modules package-lock.json /root/.npm /root/.cache /tmp/* \
  && (yarn config set supportedArchitectures --all || yarn config set supportedArchitectures all || true) \
  && yarn install --frozen-lockfile --network-timeout 600000 \
  && yarn cache clean \
  && yarn build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
