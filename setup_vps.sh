#!/usr/bin/env bash
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a
export UCF_FORCE_CONFOLD=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${SCRIPT_DIR}"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.yml"
DOMAIN="${DOMAIN:-}"
EMAIL="${EMAIL:-${SSL_EMAIL:-}}"

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

validate_project_layout() {
  [ -f "${COMPOSE_FILE}" ] || fail "docker-compose.yml nao encontrado em ${PROJECT_DIR}"
}

force_apt_unlock() {
  log "Limpando travas do APT/DPKG (modo agressivo)"
  ${SUDO} systemctl stop unattended-upgrades.service apt-daily.service apt-daily.timer apt-daily-upgrade.service apt-daily-upgrade.timer || true
  ${SUDO} systemctl disable unattended-upgrades.service apt-daily.timer apt-daily-upgrade.timer || true
  ${SUDO} systemctl mask unattended-upgrades || true
  ${SUDO} systemctl mask unattended-upgrades.service || true
  ${SUDO} killall -9 apt apt-get dpkg || true
  ${SUDO} rm -rf /var/lib/dpkg/lock*
  ${SUDO} rm -rf /var/cache/apt/archives/lock /var/lib/apt/lists/lock || true
  ${SUDO} dpkg --configure -a || true
}

install_base_dependencies() {
  log "Atualizando host e instalando dependencias base"
  ${SUDO} apt-get -yq update
  ${SUDO} apt-get -yq upgrade
  ${SUDO} apt-get install -y \
    curl \
    ca-certificates \
    gnupg \
    nginx \
    certbot \
    python3-certbot-nginx
  ${SUDO} systemctl enable --now nginx
}

install_docker_official() {
  log "Instalando Docker via script oficial"
  cd /tmp
  curl -fsSL https://get.docker.com -o get-docker.sh
  ${SUDO} sh get-docker.sh
  rm -f get-docker.sh
  ${SUDO} systemctl enable --now docker
  ${SUDO} docker compose version >/dev/null 2>&1 || fail "Docker Compose indisponivel apos instalacao."
}

load_env() {
  cd "${PROJECT_DIR}"
  if [ ! -f ".env" ] && [ -f ".env.example" ]; then
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
  [ -n "${DOMAIN}" ] || fail "Defina DOMAIN no ambiente ou no .env."
  [ -n "${EMAIL}" ] || fail "Defina EMAIL/SSL_EMAIL no ambiente ou no .env."
}

configure_nginx_non_interactive() {
  local nginx_conf="/etc/nginx/sites-available/agenteos"

  log "Configurando Nginx automaticamente"
  ${SUDO} tee "${nginx_conf}" >/dev/null <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};

    client_max_body_size 25m;

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

rebuild_stack_aggressive() {
  log "Recriando stack com rebuild limpo"
  cd "${PROJECT_DIR}"
  ${SUDO} docker compose down -v --remove-orphans || true
  ${SUDO} docker compose build --no-cache
  ${SUDO} docker compose up -d --remove-orphans
}

run_certbot_non_interactive() {
  log "Emitindo SSL via Certbot non-interactive"
  ${SUDO} certbot --nginx \
    --non-interactive \
    --agree-tos \
    --redirect \
    --no-eff-email \
    -m "${EMAIL}" \
    -d "${DOMAIN}"
}

main() {
  validate_project_layout
  force_apt_unlock
  install_base_dependencies
  install_docker_official
  load_env
  configure_nginx_non_interactive
  rebuild_stack_aggressive
  run_certbot_non_interactive

  echo
  echo "Deploy God-Mode concluido."
  echo "Projeto: ${PROJECT_DIR}"
}

main "$@"
