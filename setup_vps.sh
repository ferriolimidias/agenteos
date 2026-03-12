#!/usr/bin/env bash
set -euo pipefail

echo "==> Atualizando pacotes do sistema"
sudo apt update -y

echo "==> Instalando dependencias para repositorio Docker"
sudo apt install -y ca-certificates curl gnupg lsb-release

echo "==> Configurando repositorio oficial Docker"
sudo install -m 0755 -d /etc/apt/keyrings
. /etc/os-release
DOCKER_DISTRO="${ID}"
if [ "${ID}" != "ubuntu" ] && [ "${ID}" != "debian" ]; then
  echo "Distribuicao nao suportada automaticamente: ${ID}"
  exit 1
fi

if [ ! -f /etc/apt/keyrings/docker.gpg ]; then
  curl -fsSL "https://download.docker.com/linux/${DOCKER_DISTRO}/gpg" | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  sudo chmod a+r /etc/apt/keyrings/docker.gpg
fi

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/${DOCKER_DISTRO} \
  ${VERSION_CODENAME} stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

echo "==> Instalando Docker Engine e Docker Compose Plugin"
sudo apt update -y
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker

echo "==> Criando arquivo .env de exemplo"
cat > .env.example <<'EOF'
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
POSTGRES_DB=agent_os
OPENAI_API_KEY=sua_chave_openai_aqui
EVOLUTION_API_URL=https://sua-evolution-api.example.com
EVOLUTION_API_TOKEN=seu_token_evolution_aqui
EOF

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Arquivo .env criado a partir do .env.example"
else
  echo "Arquivo .env ja existe; mantendo configuracao atual"
fi

echo "==> Subindo containers"
sudo docker compose up -d --build

echo "==> Executando migracoes manuais (alter_db.py) no backend"
sudo docker compose exec -T backend python alter_db.py

echo "==> Setup concluido com sucesso."
