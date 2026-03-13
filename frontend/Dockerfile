FROM node:20-alpine AS build

WORKDIR /app

COPY package.json yarn.lock ./
RUN corepack enable

COPY . .
RUN rm -rf node_modules package-lock.json /root/.npm /root/.cache /tmp/* \
  && yarn install --frozen-lockfile --ignore-optional --network-timeout 600000 \
  && yarn cache clean \
  && yarn build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
