#!/usr/bin/env bash
set -e

export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a
export UCF_FORCE_CONFOLD=1

if [ "$(id -u)" -eq 0 ]; then
  SUDO=""
else
  SUDO="sudo"
fi

# Pacotes base para Nginx + Certbot
${SUDO} apt-get update -y
${SUDO} apt-get install -y nginx certbot python3-certbot-nginx curl ca-certificates
${SUDO} systemctl enable --now nginx

# Instalacao oficial do Docker
curl -fsSL https://get.docker.com -o get-docker.sh
${SUDO} sh get-docker.sh
rm -f get-docker.sh
${SUDO} systemctl enable --now docker

# Configuracao via variaveis exportadas
if [ -z "${DOMAIN:-}" ]; then
  echo "Variavel DOMAIN nao definida."
  echo "Execute: export DOMAIN=seu-dominio.com"
  exit 1
fi

if [ -z "${SSL_EMAIL:-}" ]; then
  echo "Variavel SSL_EMAIL nao definida."
  echo "Execute: export SSL_EMAIL=seu-email@dominio.com"
  exit 1
fi

PROJECT_DIR="$(pwd)"
cd "${PROJECT_DIR}"

# Build
${SUDO} docker compose down -v --remove-orphans
${SUDO} docker compose build --no-cache
${SUDO} docker compose up -d

# Nginx/SSL (proxy reverso + certbot nao interativo)
${SUDO} tee /etc/nginx/sites-available/agenteos >/dev/null <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};
    server_tokens off;

    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
    add_header X-XSS-Protection "1; mode=block" always;

    client_max_body_size 20m;
    proxy_connect_timeout 60s;
    proxy_send_timeout 60s;
    proxy_read_timeout 60s;

    location ~ /\.(?!well-known).* {
        deny all;
    }

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

${SUDO} certbot --nginx \
  --non-interactive \
  --agree-tos \
  --redirect \
  --no-eff-email \
  -m "${SSL_EMAIL}" \
  -d "${DOMAIN}"

${SUDO} systemctl enable --now certbot.timer

echo "Setup finalizado."
