# Stage 1: Build
FROM node:18-alpine as build

WORKDIR /app

COPY package.json yarn.lock ./
RUN npm install -g yarn

# Limpa qualquer resíduo de cache/artefato que possa contaminar o build Linux
RUN rm -rf node_modules package-lock.json /root/.npm /root/.cache /tmp/*

COPY . .
RUN rm -rf node_modules package-lock.json /root/.npm /root/.cache /tmp/* \
  && yarn install --frozen-lockfile --network-timeout 600000 \
  && yarn cache clean \
  && yarn build

# Stage 2: Serve
FROM nginx:alpine

# Copiar o build do Vite (geralmente vai para a pasta 'dist')
COPY --from=build /app/dist /usr/share/nginx/html

# Expor porta 80
EXPOSE 80

# Iniciar o Nginx
CMD ["nginx", "-g", "daemon off;"]
