from fastapi import APIRouter, Depends, HTTPException, status, Header
from typing import Optional
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
import uuid

from app.core.security import get_password_hash, is_bcrypt_hash, verify_password
from db.database import get_db
from db.models import Usuario

router = APIRouter(prefix="/api/auth", tags=["Auth"])

class LoginRequest(BaseModel):
    email: str
    senha: str

@router.post("/login")
async def login(data: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Usuario).where(Usuario.email == data.email))
    user = result.scalars().first()

    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuário ou senha incorretos")

    if is_bcrypt_hash(user.senha_hash):
        password_ok = verify_password(data.senha, user.senha_hash)
    else:
        password_ok = user.senha_hash == data.senha

    if not password_ok:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuário ou senha incorretos")

    if not user.ativo:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuário inativo")

    if not is_bcrypt_hash(user.senha_hash):
        try:
            user.senha_hash = get_password_hash(data.senha)
            await db.commit()
            await db.refresh(user)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

    return {
        "access_token": "token_fake",
        "usuario": {
            "id": str(user.id),
            "nome": user.nome,
            "role": user.role,
            "empresa_id": str(user.empresa_id) if user.empresa_id else None
        }
    }

async def require_super_admin(
    db: AsyncSession = Depends(get_db),
    x_user_id: Optional[str] = Header(None),
):
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Usuário não autenticado.")

    try:
        user_uuid = uuid.UUID(x_user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="Identificador de usuário inválido.")

    result = await db.execute(select(Usuario).where(Usuario.id == user_uuid))
    usuario_bd = result.scalars().first()
    if not usuario_bd:
        raise HTTPException(status_code=401, detail="Usuário não encontrado.")

    role_normalizada = (usuario_bd.role or "").strip().lower().replace("-", "_").replace(" ", "_")
    print(f"Role no Banco: {usuario_bd.role}")
    roles_permitidas = {"super_admin", "superadmin"}
    if role_normalizada not in roles_permitidas:
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas Super Admin.")

@router.post("/impersonate/{empresa_id}")
async def impersonate(empresa_id: str, db: AsyncSession = Depends(get_db), _: None = Depends(require_super_admin)):
    try:
        emp_uuid = uuid.UUID(empresa_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID da empresa inválido")

    result = await db.execute(select(Usuario).where(Usuario.empresa_id == emp_uuid))
    user = result.scalars().first()

    if not user:
        print(f"[IMPERSONATE] Nenhum usuário encontrado para a empresa {empresa_id}")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Empresa sem usuários cadastrados")

    print(f"[IMPERSONATE] Gerando token para o usuário: {user.email} (Role: {user.role})")

    return {
        "access_token": "token_fake_impersonate",
        "usuario": {
            "id": str(user.id),
            "nome": user.nome,
            "role": user.role,
            "empresa_id": str(user.empresa_id)
        }
    }
