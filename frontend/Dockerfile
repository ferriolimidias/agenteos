FROM node:22-alpine AS panel_build
WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

FROM node:22-alpine AS anamnese_build
WORKDIR /app/anamnese

COPY apps/anamnese-leonardo/package.json apps/anamnese-leonardo/package-lock.json ./
RUN npm ci

COPY apps/anamnese-leonardo/ ./
RUN npm run build

FROM nginx:stable-alpine
COPY --from=panel_build /app/frontend/dist /usr/share/nginx/html
COPY --from=anamnese_build /app/anamnese/dist /usr/share/nginx/html/anamnese/leoft
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
