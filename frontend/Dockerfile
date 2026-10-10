ARG PANEL_RUNTIME_BASE=nginx:stable-alpine
FROM node:22-alpine AS panel_build
WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build && npm run validate:artifacts

FROM node:22-alpine AS anamnese_build
WORKDIR /app/anamnese

COPY apps/anamnese-leonardo/package.json apps/anamnese-leonardo/package-lock.json ./
RUN npm ci

COPY apps/anamnese-leonardo/ ./
RUN npm run build

# Frontend-only release: requires an approved existing image so nginx and
# Anamnese remain byte-identical. Full builds continue to use the final stage.
FROM ${PANEL_RUNTIME_BASE} AS panel_release
ARG SOURCE_COMMIT=unknown
LABEL org.opencontainers.image.revision=$SOURCE_COMMIT org.agenteos.change="issue16-panel-single-root-20261010"
RUN test -f /usr/share/nginx/html/anamnese/leoft/index.html
COPY --from=panel_build /app/frontend/dist /usr/share/nginx/html

FROM ${PANEL_RUNTIME_BASE} AS full_release
ARG SOURCE_COMMIT=unknown
LABEL org.opencontainers.image.revision=$SOURCE_COMMIT org.agenteos.change="issue16-panel-single-root-20261010"
COPY --from=panel_build /app/frontend/dist /usr/share/nginx/html
COPY --from=anamnese_build /app/anamnese/dist /usr/share/nginx/html/anamnese/leoft
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
