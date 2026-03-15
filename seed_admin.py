import asyncio

from sqlalchemy import select

from app.core.security import get_password_hash
from db.database import AsyncSessionLocal
from db.models import Usuario


ADMIN_EMAIL = "admin@ferriolimidias.com"
ADMIN_PASSWORD = "Admin123!"
ADMIN_NAME = "Administrador"
ADMIN_ROLE = "super_admin"


async def seed_admin() -> None:
    async with AsyncSessionLocal() as session:
        try:
            result = await session.execute(
                select(Usuario).where(Usuario.email == ADMIN_EMAIL)
            )
            usuario = result.scalars().first()

            if usuario:
                usuario.senha_hash = get_password_hash(ADMIN_PASSWORD)
                usuario.role = ADMIN_ROLE
                usuario.ativo = True
                if hasattr(usuario, "is_superuser"):
                    usuario.is_superuser = True
                if not usuario.nome:
                    usuario.nome = ADMIN_NAME
                acao = "Senha redefinida e acesso de superusuário garantido"
            else:
                usuario = Usuario(
                    nome=ADMIN_NAME,
                    email=ADMIN_EMAIL,
                    senha_hash=get_password_hash(ADMIN_PASSWORD),
                    role=ADMIN_ROLE,
                    ativo=True,
                )
                if hasattr(usuario, "is_superuser"):
                    usuario.is_superuser = True
                session.add(usuario)
                acao = "Usuário administrador criado com sucesso"

            await session.commit()
            print(f"{acao}: {ADMIN_EMAIL}")
            print("Senha definida para: Admin123!")
        except Exception:
            await session.rollback()
            raise


if __name__ == "__main__":
    asyncio.run(seed_admin())
