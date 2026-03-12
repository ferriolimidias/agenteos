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

install_base_dependencies() {
  echo "Instalando dependencias basicas..."
  $SUDO apt-get update -y
  $SUDO apt-get install -y ca-certificates curl gnupg lsb-release
}

install_docker_if_needed() {
  if command -v docker >/dev/null 2>&1; then
    echo "Docker ja instalado."
    return
  fi

  echo "Docker nao encontrado. Instalando..."
  curl -fsSL https://get.docker.com | sh
  $SUDO systemctl enable docker
  $SUDO systemctl start docker
}

install_compose_if_needed() {
  if docker compose version >/dev/null 2>&1; then
    echo "Docker Compose ja instalado."
    return
  fi

  echo "Docker Compose nao encontrado. Instalando plugin..."
  $SUDO apt-get update -y
  $SUDO apt-get install -y docker-compose-plugin
}

install_nginx_if_needed() {
  if command -v nginx >/dev/null 2>&1; then
    echo "Nginx ja instalado."
    return
  fi

  echo "Nginx nao encontrado. Instalando..."
  $SUDO apt-get update -y
  $SUDO apt-get install -y nginx
  $SUDO systemctl enable nginx
  $SUDO systemctl start nginx
}

install_certbot_if_needed() {
  if command -v certbot >/dev/null 2>&1; then
    echo "Certbot ja instalado."
    return
  fi

  echo "Certbot nao encontrado. Instalando..."
  $SUDO apt-get update -y
  $SUDO apt-get install -y certbot python3-certbot-nginx
}

ensure_project_directory() {
  local current_dir
  current_dir="$(pwd)"

  if [ -f "${current_dir}/docker-compose.yml" ] && [ -f "${current_dir}/.env.example" ] && [ -d "${current_dir}/.git" ]; then
    PROJECT_DIR="${current_dir}"
    echo "Repositorio ja detectado no diretorio atual: ${PROJECT_DIR}"
  else
    PROJECT_DIR="${current_dir}/${PROJECT_NAME}"
    if [ -d "${PROJECT_DIR}/.git" ]; then
      echo "Clone existente detectado em ${PROJECT_DIR}. Reaproveitando."
      cd "${PROJECT_DIR}"
    else
      if [ -d "${PROJECT_DIR}" ]; then
        echo "Erro: diretorio ${PROJECT_DIR} ja existe, mas nao parece ser um clone valido."
        echo "Remova/renomeie a pasta ou execute o script de dentro do repositorio."
        exit 1
      fi

      echo "Clonando repositorio em ${PROJECT_DIR}..."
      git clone "https://${GITHUB_TOKEN}@github.com/ferriolimidias/agenteos.git" "${PROJECT_DIR}"
      cd "${PROJECT_DIR}"
      git remote set-url origin "${REPO_CLONE_URL_BASE}"
    fi
  fi

  cd "${PROJECT_DIR}"
  echo "Garantindo branch main atualizada..."
  git fetch origin main
  git checkout main
  git pull origin main
}

prepare_env_file() {
  cd "${PROJECT_DIR}"
  if [ ! -f ".env.example" ]; then
    echo "Erro: arquivo .env.example nao encontrado em ${PROJECT_DIR}"
    exit 1
  fi

  if [ -f ".env" ]; then
    local backup_file=".env.backup.$(date +%Y%m%d_%H%M%S)"
    cp ".env" "${backup_file}"
    echo "Backup do .env atual criado: ${backup_file}"
  fi

  cp ".env.example" ".env"
  echo ".env gerado a partir de .env.example."

  local api_base_url="https://${DOMAIN}"
  if grep -q "^API_BASE_URL=" ".env"; then
    sed -i "s|^API_BASE_URL=.*|API_BASE_URL=${api_base_url}|g" ".env"
  else
    printf "\nAPI_BASE_URL=%s\n" "${api_base_url}" >> ".env"
  fi

  echo "API_BASE_URL configurada para ${api_base_url}"
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
  echo "Nginx configurado com sucesso."
}

wait_for_backend() {
  local retries=20
  local wait_seconds=5

  echo "Aguardando backend ficar disponivel..."
  for _ in $(seq 1 "${retries}"); do
    if docker compose exec -T backend python -c "print('backend-ok')" >/dev/null 2>&1; then
      return
    fi
    sleep "${wait_seconds}"
  done

  echo "Erro: backend nao ficou disponivel a tempo."
  exit 1
}

start_containers_and_migrate() {
  cd "${PROJECT_DIR}"
  echo "Subindo containers..."
  docker compose up -d

  wait_for_backend

  echo "Executando migracao alter_db.py..."
  docker compose exec -T backend python alter_db.py
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

create_logs_alias() {
  local alias_file="/etc/profile.d/agenteos_alias.sh"
  local alias_cmd="alias agenteos-logs='cd ${PROJECT_DIR} && docker compose logs -f backend'"

  echo "Criando alias global agenteos-logs..."
  $SUDO tee "${alias_file}" >/dev/null <<EOF
#!/usr/bin/env bash
${alias_cmd}
EOF
  $SUDO chmod +x "${alias_file}"
}

main() {
  ask_inputs
  install_base_dependencies
  install_docker_if_needed
  install_compose_if_needed
  install_nginx_if_needed
  install_certbot_if_needed
  ensure_project_directory
  prepare_env_file
  configure_nginx
  start_containers_and_migrate
  configure_ssl
  create_logs_alias

  echo
  echo "Instalacao concluida com sucesso."
  echo "Projeto: ${PROJECT_DIR}"
  echo "Alias de logs: agenteos-logs"
}

main "$@"
