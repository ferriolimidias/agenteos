"""Centralized tenant user provisioning; callers never persist plaintext passwords."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_password_hash
from db.models import Empresa, Usuario, normalize_user_email


async def create_or_update_tenant_user(
    db: AsyncSession,
    *,
    empresa_id,
    nome: str,
    email: str,
    password: str,
    role: str,
    ativo: bool = True,
) -> Usuario:
    empresa = (await db.execute(select(Empresa).where(Empresa.id == empresa_id))).scalars().first()
    if not empresa:
        raise ValueError("empresa_not_found")
    email_normalized = normalize_user_email(email)
    if not email_normalized or "@" not in email_normalized:
        raise ValueError("invalid_email")
    user = (await db.execute(select(Usuario).where(Usuario.email == email_normalized))).scalars().first()
    password_hash = get_password_hash(password)
    if user and user.empresa_id != empresa.id:
        raise ValueError("email_belongs_to_other_tenant")
    if user is None:
        user = Usuario(empresa_id=empresa.id, nome=nome.strip(), email=email_normalized, senha_hash=password_hash, role=role, ativo=bool(ativo))
        db.add(user)
    else:
        user.empresa_id = empresa.id
        user.nome = nome.strip()
        user.senha_hash = password_hash
        user.role = role
        user.ativo = bool(ativo)
    await db.commit()
    await db.refresh(user)
    return user
