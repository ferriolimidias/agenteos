import logging

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/agent_os"
    SQL_ECHO: bool = False
    SQL_LOG_LEVEL: str = "WARNING"
    # Opcional: chaves por empresa/config no banco; ausência aqui não impede o app de subir.
    OPENAI_API_KEY: str | None = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()

def _configure_sqlalchemy_logging() -> None:
    level_name = (settings.SQL_LOG_LEVEL or "WARNING").upper()
    level = getattr(logging, level_name, logging.WARNING)
    if settings.SQL_ECHO:
        level = logging.INFO

    for logger_name in (
        "sqlalchemy.engine",
        "sqlalchemy.pool",
        "sqlalchemy.dialects",
    ):
        logging.getLogger(logger_name).setLevel(level)


_configure_sqlalchemy_logging()

# Async Engine setup
engine = create_async_engine(settings.DATABASE_URL, echo=settings.SQL_ECHO)

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
