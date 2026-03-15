FROM node:20-bookworm AS build
WORKDIR /app

COPY package.json .
RUN corepack enable && yarn install

COPY . .
RUN yarn build

FROM nginx:stable-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
