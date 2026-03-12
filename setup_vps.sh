#!/usr/bin/env bash
set -euo pipefail

REPO_CLONE_URL_BASE="https://github.com/ferriolimidias/agenteos.git"
PROJECT_NAME="agenteos"

if [ "$(id -u)" -eq 0 ]; then
  SUDO=""
else
  SUDO="sudo"
fi

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

install_dependencies() {
  echo "Instalando dependencias necessarias..."
  $SUDO apt-get update -y
  $SUDO apt-get install -y git docker.io docker-compose-plugin nginx certbot python3-certbot-nginx
  $SUDO systemctl enable docker nginx
  $SUDO systemctl start docker nginx
}

clone_repository() {
  PROJECT_DIR="$(pwd)/${PROJECT_NAME}"

  if [ -d "${PROJECT_DIR}/.git" ]; then
    echo "Clone existente detectado em ${PROJECT_DIR}. Atualizando..."
    cd "${PROJECT_DIR}"
    git fetch origin main
    git checkout main
    git pull origin main
    return
  fi

  if [ -d "${PROJECT_DIR}" ]; then
    echo "Erro: diretorio ${PROJECT_DIR} ja existe e nao e um clone Git valido."
    exit 1
  fi

  echo "Clonando repositorio em ${PROJECT_DIR}..."
  git clone "https://${GITHUB_TOKEN}@github.com/ferriolimidias/agenteos.git" "${PROJECT_DIR}"
  cd "${PROJECT_DIR}"
  git remote set-url origin "${REPO_CLONE_URL_BASE}"
}

configure_nginx() {
  local nginx_conf="/etc/nginx/sites-available/agenteos"

  echo "Configurando Nginx..."
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

configure_ssl() {
  echo "Gerando certificado SSL com Certbot..."
  $SUDO certbot --nginx \
    -d "${DOMAIN}" \
    --non-interactive \
    --agree-tos \
    -m "${SSL_EMAIL}" \
    --redirect
}

run_compose_build() {
  cd "${PROJECT_DIR}"
  echo "Subindo containers com build limpo..."
  docker compose up --build -d
}

main() {
  ask_inputs
  install_dependencies
  clone_repository
  configure_nginx
  configure_ssl
  run_compose_build

  echo
  echo "Deploy concluido com sucesso."
  echo "Projeto: ${PROJECT_DIR}"
}

main "$@"
