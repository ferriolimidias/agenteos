#!/usr/bin/env bash
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a
export UCF_FORCE_CONFOLD=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${SCRIPT_DIR}"
DOMAIN="${DOMAIN:-}"
EMAIL="${EMAIL:-${SSL_EMAIL:-}}"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.yml"
ENV_FILE="${PROJECT_DIR}/.env"

if [ "$(id -u)" -eq 0 ]; then
  SUDO=""
else
  SUDO="sudo"
fi

log() {
  echo
  echo "==> $1"
}

fail() {
  echo "ERRO: $1" >&2
  exit 1
}

on_error() {
  local line="$1"
  fail "Falha na linha ${line}. Abortando."
}
trap 'on_error $LINENO' ERR

disable_unattended_upgrades() {
  log "Desativando unattended-upgrades e timers de update"
  ${SUDO} systemctl stop unattended-upgrades.service apt-daily.service apt-daily.timer apt-daily-upgrade.service apt-daily-upgrade.timer || true
  ${SUDO} systemctl disable unattended-upgrades.service apt-daily.timer apt-daily-upgrade.timer || true
  ${SUDO} systemctl mask unattended-upgrades.service || true
  ${SUDO} pkill -f unattended-upgrade || true
  ${SUDO} rm -f /var/lib/dpkg/lock /var/lib/dpkg/lock-frontend /var/cache/apt/archives/lock /var/lib/apt/lists/lock || true
  ${SUDO} dpkg --configure -a || true
}

mask_unattended_upgrades() {
  log "Aplicando mask em unattended-upgrades"
  ${SUDO} systemctl mask unattended-upgrades || true
}

apt_update_and_upgrade() {
  log "Atualizando cache e pacotes do Ubuntu"
  ${SUDO} apt-get -yq update
  ${SUDO} apt-get -yq upgrade
}

install_base_dependencies() {
  log "Instalando dependencias base do host"
  ${SUDO} apt-get install -y \
    git \
    curl \
    ca-certificates \
    gnupg \
    build-essential \
    nginx \
    certbot \
    python3-certbot-nginx
}

install_docker_if_needed() {
  if command -v docker >/dev/null 2>&1 && ${SUDO} docker compose version >/dev/null 2>&1; then
    log "Docker e Docker Compose V2 ja instalados"
    ${SUDO} systemctl enable --now docker
    return
  fi

  log "Instalando Docker oficial (canal estavel)"
  ${SUDO} apt-get remove -y docker docker-engine docker.io containerd runc docker-compose docker-compose-v2 || true
  ${SUDO} install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | ${SUDO} gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  ${SUDO} chmod a+r /etc/apt/keyrings/docker.gpg

  local arch codename
  arch="$(dpkg --print-architecture)"
  codename="$(. /etc/os-release && echo "${VERSION_CODENAME}")"
  echo \
    "deb [arch=${arch} signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${codename} stable" \
    | ${SUDO} tee /etc/apt/sources.list.d/docker.list >/dev/null

  ${SUDO} apt-get -yq update
  ${SUDO} apt-get -yq install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  ${SUDO} systemctl enable --now docker
  ${SUDO} docker compose version >/dev/null 2>&1 || fail "Docker Compose indisponivel apos instalacao."
}

enable_nginx_service() {
  log "Habilitando Nginx no host"
  ${SUDO} systemctl enable --now nginx
}

apply_env_file() {
  cd "${PROJECT_DIR}"

  if [ ! -f ".env" ] && [ -f ".env.example" ]; then
    log "Aplicando variaveis: criando .env a partir de .env.example"
    cp .env.example .env
  fi

  if [ -f ".env" ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
  fi

  DOMAIN="${DOMAIN:-${DOMAIN_NAME:-}}"
  EMAIL="${EMAIL:-${SSL_EMAIL:-}}"

  [ -n "${DOMAIN}" ] || fail "Defina DOMAIN (env do shell ou .env) para configurar Nginx/Certbot."
  [ -n "${EMAIL}" ] || fail "Defina EMAIL (env do shell ou .env) para executar Certbot."
}

validate_project_layout() {
  [ -f "${COMPOSE_FILE}" ] || fail "docker-compose.yml nao encontrado em ${PROJECT_DIR}"
  [ -f "${PROJECT_DIR}/setup_vps.sh" ] || fail "Execute este script a partir do repositorio clonado."
}

configure_nginx() {
  local nginx_conf="/etc/nginx/sites-available/agenteos"

  log "Configurando Nginx como reverse proxy blindado"
  ${SUDO} tee "${nginx_conf}" >/dev/null <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};

    server_tokens off;
    client_max_body_size 25m;

    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header X-XSS-Protection "1; mode=block" always;

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

  ${SUDO} ln -sfn /etc/nginx/sites-available/agenteos /etc/nginx/sites-enabled/agenteos
  ${SUDO} rm -f /etc/nginx/sites-enabled/default
  ${SUDO} nginx -t
  ${SUDO} systemctl reload nginx
}

build_and_start_stack() {
  cd "${PROJECT_DIR}"
  log "Subindo stack completa pelo Docker Compose"
  ${SUDO} docker compose down -v --remove-orphans || true
  ${SUDO} docker compose build --no-cache
  ${SUDO} docker compose up -d --remove-orphans
}

wait_for_database() {
  cd "${PROJECT_DIR}"
  log "Aguardando banco de dados ficar pronto"

  local max_attempts=90
  local attempt=1
  local db_user="${POSTGRES_USER:-postgres}"

  until ${SUDO} docker compose exec -T db pg_isready -U "${db_user}" >/dev/null 2>&1; do
    if [ "${attempt}" -ge "${max_attempts}" ]; then
      fail "Banco nao ficou pronto no tempo esperado."
    fi
    sleep 2
    attempt=$((attempt + 1))
  done
}

run_certbot() {
  log "Emitindo certificado SSL com Certbot"

  if [ -d "/etc/letsencrypt/live/${DOMAIN}" ]; then
    echo "Certificado para ${DOMAIN} ja existe. Pulando emissao."
    return
  fi

  ${SUDO} certbot --nginx \
    --non-interactive \
    --agree-tos \
    --redirect \
    -d "${DOMAIN}" \
    -m "${EMAIL}"
}

main() {
  mask_unattended_upgrades
  disable_unattended_upgrades
  apt_update_and_upgrade
  install_base_dependencies
  enable_nginx_service
  install_docker_if_needed
  validate_project_layout
  apply_env_file
  configure_nginx
  build_and_start_stack
  wait_for_database
  run_certbot

  echo
  echo "Deploy concluido com sucesso."
  echo "Projeto: ${PROJECT_DIR}"
}

main "$@"
