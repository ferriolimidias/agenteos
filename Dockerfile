FROM python:3.11-slim

WORKDIR /app

# Instalar dependências do sistema necessárias para compilar pacotes Python (como asyncpg, psycopg2, etc)
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copiar arquivos de requisitos primeiro para aproveitar o cache do Docker
COPY requirements.txt .

# Instalar as dependências do projeto.
# Incluímos as específicas solicitadas pelo usuário caso não estejam no requirements.txt.
RUN pip install --no-cache-dir -r requirements.txt || echo "requirements.txt em branco ou ausente, prosseguindo com dependencias manuais"
RUN pip install --no-cache-dir fastapi uvicorn sqlalchemy asyncpg pydantic pydantic-settings python-multipart PyPDF2 langchain langchain-openai langchain-community langchain-text-splitters redis httpx pgvector openai

# Copiar o restante do código
COPY . .

EXPOSE 8000

# Executar a aplicação
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
