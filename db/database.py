from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/agent_os"
    OPENAI_API_KEY: str = "sua_chave_aqui"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()

# Async Engine setup
engine = create_async_engine(settings.DATABASE_URL, echo=True)

# Async SessionMaker
AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)

# Declarative Base for models
Base = declarative_base()

# Dependency for FastAPI
async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
