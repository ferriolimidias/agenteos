#!/usr/bin/env bash
set -Eeuo pipefail

REPO_CLONE_URL_BASE="https://github.com/ferriolimidias/agenteos.git"
PROJECT_NAME="agenteos"
PROJECT_DIR=""
DOMAIN=""
SSL_EMAIL=""
GITHUB_TOKEN=""

if [ "$(id -u)" -eq 0 ]; then
  SUDO=""
else
  SUDO="sudo"
fi

log() {
  echo
  echo "==> $1"
}

require_command() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Erro: comando obrigatorio nao encontrado: ${cmd}"
    exit 1
  fi
}

ask_inputs() {
  read -r -p "Informe o dominio do sistema (ex: agents.ferriolimidias.com): " DOMAIN
  read -r -p "Informe o e-mail para SSL (Certbot): " SSL_EMAIL
  read -r -s -p "Informe o GitHub Personal Access Token (PAT): " GITHUB_TOKEN
  echo

  if [ -z "${DOMAIN}" ] || [ -z "${SSL_EMAIL}" ] || [ -z "${GITHUB_TOKEN}" ]; then
    echo "Erro: dominio, e-mail e token sao obrigatorios."
    exit 1
  fi
}

update_system_and_install_basics() {
  log "Atualizando sistema e instalando pacotes basicos"
  require_command apt-get

  $SUDO apt-get update -y
  $SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y \
    git \
    curl \
    nginx \
    certbot \
    python3-certbot-nginx

  $SUDO systemctl enable --now nginx
}

install_docker() {
  log "Instalando Docker via script oficial get.docker.com"

  curl -fsSL https://get.docker.com | sh
  $SUDO systemctl enable --now docker

  if ! docker compose version >/dev/null 2>&1; then
    echo "Erro: Docker Compose nao foi disponibilizado apos a instalacao do Docker."
    exit 1
  fi
}

clone_repository_main() {
  log "Clonando/atualizando repositorio na branch main"
  require_command git

  PROJECT_DIR="$(pwd)/${PROJECT_NAME}"

  if [ -d "${PROJECT_DIR}/.git" ]; then
    echo "Clone existente detectado em ${PROJECT_DIR}. Atualizando..."
    cd "${PROJECT_DIR}"
    git remote set-url origin "${REPO_CLONE_URL_BASE}"
    git fetch origin main
    git checkout -B main origin/main
    return
  fi

  if [ -d "${PROJECT_DIR}" ]; then
    echo "Erro: diretorio ${PROJECT_DIR} ja existe e nao e um clone Git valido."
    exit 1
  fi

  git clone "https://${GITHUB_TOKEN}@github.com/ferriolimidias/agenteos.git" "${PROJECT_DIR}"
  cd "${PROJECT_DIR}"
  git remote set-url origin "${REPO_CLONE_URL_BASE}"
  git fetch origin main
  git checkout -B main origin/main
}

configure_nginx_proxy() {
  local nginx_conf="/etc/nginx/sites-available/agenteos"

  log "Configurando Nginx como proxy reverso"
  $SUDO tee "${nginx_conf}" >/dev/null <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

  $SUDO ln -sfn /etc/nginx/sites-available/agenteos /etc/nginx/sites-enabled/agenteos
  if [ -f /etc/nginx/sites-enabled/default ]; then
    $SUDO rm -f /etc/nginx/sites-enabled/default
  fi

  $SUDO nginx -t
  $SUDO systemctl reload nginx
}

run_compose_build() {
  log "Subindo aplicacao com Docker Compose"
  cd "${PROJECT_DIR}"
  docker compose up --build -d
}

configure_ssl() {
  log "Configurando SSL com Certbot"
  $SUDO certbot --nginx \
    -d "${DOMAIN}" \
    --non-interactive \
    --agree-tos \
    -m "${SSL_EMAIL}" \
    --redirect
}

main() {
  ask_inputs
  update_system_and_install_basics
  install_docker
  clone_repository_main
  configure_nginx_proxy
  run_compose_build
  configure_ssl

  echo
  echo "Deploy concluido com sucesso."
  echo "Projeto: ${PROJECT_DIR}"
}

main "$@"
