FROM python:3.11-slim-bookworm

WORKDIR /app

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install pgvector
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x /app/docker/backend-entrypoint.sh

EXPOSE 8000

CMD ["/app/docker/backend-entrypoint.sh"]
