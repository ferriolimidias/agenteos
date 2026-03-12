#!/usr/bin/env bash
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive

REPO_URL="https://github.com/ferriolimidias/agenteos.git"
PROJECT_NAME="agenteos"
PROJECT_DIR=""
DOMAIN="${DOMAIN:-}"
SSL_EMAIL="${SSL_EMAIL:-}"

if [ "$(id -u)" -eq 0 ]; then
  SUDO=""
else
  SUDO="sudo"
fi

# Primeira acao: aguardar locks do apt e atualizacoes de primeiro boot.
while true; do
  if pgrep -fa "unattended-upgrades?|apt.systemd.daily|apt-get|apt |dpkg" >/dev/null 2>&1; then
    echo "Aguardando processos de atualizacao do sistema liberarem..."
    sleep 5
    continue
  fi

  locked=0
  for lock_file in \
    /var/lib/dpkg/lock \
    /var/lib/dpkg/lock-frontend \
    /var/cache/apt/archives/lock \
    /var/lib/apt/lists/lock; do
    if [ -e "${lock_file}" ] && ! ${SUDO} flock -n "${lock_file}" -c true >/dev/null 2>&1; then
      locked=1
      break
    fi
  done

  if [ "${locked}" -eq 0 ]; then
    break
  fi

  echo "Aguardando lock files do apt/dpkg serem liberados..."
  sleep 5
done

log() {
  echo
  echo "==> $1"
}

fail() {
  echo "ERRO: $1" >&2
  exit 1
}

require_command() {
  local cmd="$1"
  command -v "${cmd}" >/dev/null 2>&1 || fail "Comando obrigatorio nao encontrado: ${cmd}"
}

on_error() {
  local line="$1"
  fail "Falha na linha ${line}. Abortando."
}
trap 'on_error $LINENO' ERR

update_os() {
  log "Atualizando o sistema operacional por completo"
  ${SUDO} apt-get update
  ${SUDO} apt-get upgrade -y
}

install_base_dependencies() {
  log "Instalando dependencias basicas"
  ${SUDO} apt-get install -y git curl ca-certificates certbot python3-certbot-nginx
}

install_nginx_if_needed() {
  if command -v nginx >/dev/null 2>&1; then
    log "Nginx ja instalado"
  else
    log "Instalando Nginx"
    ${SUDO} apt-get install -y nginx
  fi
  ${SUDO} systemctl enable --now nginx
}

install_docker_if_needed() {
  if command -v docker >/dev/null 2>&1; then
    log "Docker ja instalado"
  else
    log "Instalando Docker"
    curl -fsSL https://get.docker.com | ${SUDO} sh
  fi

  ${SUDO} systemctl enable --now docker

  if ! ${SUDO} docker compose version >/dev/null 2>&1; then
    log "Instalando plugin docker compose"
    ${SUDO} apt-get install -y docker-compose-plugin
  fi

  ${SUDO} docker compose version >/dev/null 2>&1 || fail "Docker Compose indisponivel apos instalacao."
}

resolve_project_directory() {
  local current_basename
  current_basename="$(basename "$(pwd)")"

  if [ -d ".git" ] && [ "${current_basename}" = "${PROJECT_NAME}" ]; then
    PROJECT_DIR="$(pwd)"
    log "Repositorio local detectado em ${PROJECT_DIR}. Pulando clone."
    return
  fi

  if [ -d "$(pwd)/${PROJECT_NAME}/.git" ]; then
    PROJECT_DIR="$(pwd)/${PROJECT_NAME}"
    log "Repositorio detectado em ${PROJECT_DIR}. Pulando clone."
    return
  fi

  PROJECT_DIR="$(pwd)/${PROJECT_NAME}"
  if [ -d "${PROJECT_DIR}" ] && [ ! -d "${PROJECT_DIR}/.git" ]; then
    fail "Diretorio ${PROJECT_DIR} existe, mas nao e um repositorio Git valido."
  fi

  if [ ! -d "${PROJECT_DIR}/.git" ]; then
    log "Clonando repositorio ${PROJECT_NAME}"
    local clone_url="${REPO_URL}"
    if [ -n "${GITHUB_TOKEN:-}" ]; then
      clone_url="https://${GITHUB_TOKEN}@github.com/ferriolimidias/agenteos.git"
    fi

    git clone "${clone_url}" "${PROJECT_DIR}" || fail "Falha no clone. Se o repo for privado, exporte GITHUB_TOKEN e execute novamente."
  fi
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

  DOMAIN="${DOMAIN:-}"
  SSL_EMAIL="${SSL_EMAIL:-}"

  [ -n "${DOMAIN}" ] || fail "Defina DOMAIN (env do shell ou .env) para configurar Nginx/Certbot."
  [ -n "${SSL_EMAIL}" ] || fail "Defina SSL_EMAIL (env do shell ou .env) para executar Certbot."
}

configure_nginx() {
  local nginx_conf="/etc/nginx/sites-available/agenteos"

  log "Configurando Nginx para frontend 8080 e backend 8000"
  ${SUDO} tee "${nginx_conf}" >/dev/null <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};

    location /api/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location / {
        proxy_pass http://127.0.0.1:8080;
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

  ${SUDO} ln -sfn /etc/nginx/sites-available/agenteos /etc/nginx/sites-enabled/agenteos
  ${SUDO} rm -f /etc/nginx/sites-enabled/default
  ${SUDO} nginx -t
  ${SUDO} systemctl reload nginx
}

build_and_start_stack() {
  cd "${PROJECT_DIR}"
  require_command git

  log "Executando build blindado do ambiente Docker"
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
  log "Executando Certbot"

  if [ -d "/etc/letsencrypt/live/${DOMAIN}" ]; then
    echo "Certificado para ${DOMAIN} ja existe. Pulando emissao."
    return
  fi

  ${SUDO} certbot --nginx \
    --non-interactive \
    --agree-tos \
    --redirect \
    -d "${DOMAIN}" \
    -m "${SSL_EMAIL}"
}

main() {
  update_os
  install_base_dependencies
  install_nginx_if_needed
  install_docker_if_needed
  resolve_project_directory
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
