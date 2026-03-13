FROM node:20-bookworm AS build

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    libc6 \
    libstdc++6 \
  && rm -rf /var/lib/apt/lists/*

COPY package.json ./
RUN corepack enable

COPY . .
RUN rm -rf node_modules yarn.lock package-lock.json
RUN yarn config set supportedArchitectures --all \
  && yarn install \
  && yarn cache clean \
  && yarn build

FROM nginx:stable-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
