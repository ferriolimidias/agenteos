from fastapi import APIRouter, Depends, HTTPException, status, Header
from typing import Optional
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
import uuid

from app.core.security import (
    create_access_token,
    decode_access_token,
    get_password_hash,
    is_bcrypt_hash,
    verify_password,
)
from db.database import get_db
from db.models import (
    ROOT_ADMIN_ROLE,
    Usuario,
    is_admin_empresa_role,
    is_root_admin_email,
    is_super_admin_role,
    normalize_user_email,
)

router = APIRouter(prefix="/api/auth", tags=["Auth"])

class LoginRequest(BaseModel):
    email: str
    senha: str

@router.post("/login")
async def login(data: LoginRequest, db: AsyncSession = Depends(get_db)):
    email_normalizado = normalize_user_email(data.email)
    result = await db.execute(select(Usuario).where(Usuario.email == email_normalizado))
    user = result.scalars().first()

    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usu?rio ou senha incorretos")

    if is_bcrypt_hash(user.senha_hash):
        password_ok = verify_password(data.senha, user.senha_hash)
    else:
        password_ok = user.senha_hash == data.senha

    if not password_ok:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usu?rio ou senha incorretos")

    if not user.ativo:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usu?rio inativo")

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

    token = create_access_token(
        subject=str(user.id),
        extra_claims={
            "role": user.role,
            "empresa_id": str(user.empresa_id) if user.empresa_id else None,
            "email": user.email,
        },
    )
    return {
        "access_token": token,
        "usuario": {
            "id": str(user.id),
            "nome": user.nome,
            "email": user.email,
            "role": user.role,
            "empresa_id": str(user.empresa_id) if user.empresa_id else None
        }
    }

def _extract_bearer_token(authorization: Optional[str]) -> str:
    raw = str(authorization or "").strip()
    if not raw:
        raise HTTPException(status_code=401, detail="Token ausente.")
    if not raw.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Formato de Authorization inválido.")
    token = raw.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Token ausente.")
    return token


async def get_current_user(
    db: AsyncSession = Depends(get_db),
    authorization: Optional[str] = Header(None),
):
    token = _extract_bearer_token(authorization)
    try:
        claims = decode_access_token(token)
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"Token inválido: {exc}") from exc

    try:
        user_uuid = uuid.UUID(str(claims.get("sub")))
    except ValueError:
        raise HTTPException(status_code=401, detail="Token inválido (subject).")

    result = await db.execute(select(Usuario).where(Usuario.id == user_uuid))
    usuario_bd = result.scalars().first()
    if not usuario_bd or not usuario_bd.ativo:
        raise HTTPException(status_code=401, detail="Usuário não encontrado ou inativo.")
    return usuario_bd


async def require_super_admin(current_user: Usuario = Depends(get_current_user)):
    if is_root_admin_email(current_user.email) or is_super_admin_role(current_user.role):
        return current_user
    raise HTTPException(
        status_code=403,
        detail=f"Acesso negado. Apenas Super Admin com role '{ROOT_ADMIN_ROLE}'.",
    )


async def require_tenant_access(
    empresa_id: str,
    current_user: Usuario = Depends(get_current_user),
):
    try:
        empresa_uuid = uuid.UUID(str(empresa_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="ID da empresa inválido.")

    if is_root_admin_email(current_user.email) or is_super_admin_role(current_user.role):
        return current_user
    if is_admin_empresa_role(current_user.role) and current_user.empresa_id == empresa_uuid:
        return current_user
    raise HTTPException(status_code=403, detail="Acesso negado para esta empresa.")

@router.post("/impersonate/{empresa_id}")
async def impersonate(empresa_id: str, db: AsyncSession = Depends(get_db), _: Usuario = Depends(require_super_admin)):
    try:
        emp_uuid = uuid.UUID(empresa_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID da empresa inv?lido")

    result = await db.execute(select(Usuario).where(Usuario.empresa_id == emp_uuid))
    user = result.scalars().first()

    if not user:
        print(f"[IMPERSONATE] Nenhum usu?rio encontrado para a empresa {empresa_id}")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Empresa sem usu?rios cadastrados")

    print(f"[IMPERSONATE] Gerando token para o usu?rio: {user.email} (Role: {user.role})")

    token = create_access_token(
        subject=str(user.id),
        extra_claims={
            "role": user.role,
            "empresa_id": str(user.empresa_id) if user.empresa_id else None,
            "email": user.email,
        },
    )
    return {
        "access_token": token,
        "usuario": {
            "id": str(user.id),
            "nome": user.nome,
            "email": user.email,
            "role": user.role,
            "empresa_id": str(user.empresa_id)
        }
    }
