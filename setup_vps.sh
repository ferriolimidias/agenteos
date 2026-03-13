#!/usr/bin/env bash
set -e
set -x

export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a
export UCF_FORCE_CONFOLD=1

if [ "$(id -u)" -eq 0 ]; then
  SUDO=""
else
  SUDO="sudo"
fi

# 2) Destrava
${SUDO} systemctl mask unattended-upgrades
${SUDO} killall -9 apt apt-get dpkg || true
${SUDO} rm -rf /var/lib/dpkg/lock*

${SUDO} rm -rf /var/cache/apt/archives/lock /var/lib/apt/lists/lock || true
${SUDO} dpkg --configure -a || true

# Pacotes base para Nginx + Certbot
${SUDO} apt-get update -y
${SUDO} apt-get install -y nginx certbot python3-certbot-nginx curl ca-certificates
${SUDO} systemctl enable --now nginx

# 3) Instalação Docker (explícito)
curl -fsSL https://get.docker.com -o get-docker.sh
sh get-docker.sh
rm -f get-docker.sh
${SUDO} systemctl enable --now docker

# 4) Configuração via variáveis exportadas
: "${DOMAIN:?Defina DOMAIN via export antes de executar.}"
: "${SSL_EMAIL:?Defina SSL_EMAIL via export antes de executar.}"

PROJECT_DIR="$(pwd)"
cd "${PROJECT_DIR}"

# 5) Build
${SUDO} docker compose down -v --remove-orphans || true
${SUDO} docker compose up --build -d

# 6) Nginx/SSL (proxy reverso + certbot não interativo)
${SUDO} tee /etc/nginx/sites-available/agenteos >/dev/null <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};

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

echo "Setup finalizado."
