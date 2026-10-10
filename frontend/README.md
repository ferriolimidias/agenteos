# AgenteOS Frontend

Frontend React/Vite do AgenteOS.

## Node suportado

Use Node 22 para desenvolvimento e build. O projeto declara:

- `.nvmrc`: `22`
- `package.json > engines`: `node >=20.19.0 <23`

O host da VPS pode ter Node mais antigo. Para evitar quebrar outros projetos, o build oficial deve ser feito via Docker:

```bash
docker run --rm -v "$PWD":/app -w /app node:22-alpine sh -lc "npm ci && npm run build"
```

## Scripts

```bash
npm run dev
npm run build
npm run lint
```

## Docker

```bash
cd /root/agenteos
docker compose build frontend
docker compose up -d --build frontend
```
