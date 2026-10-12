"""Tenant JEV settings. Credentials never enter company JSON or public DTOs."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

import httpx
from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MODEL = 'jev-1.13.0'


class JevCredentialError(ValueError):
    pass


class JevConfigUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    provider: str = 'typesafe'
    model: str = MODEL
    daily_budget_usd: Decimal = Field(default=Decimal('0'), ge=0, le=1000, max_digits=12, decimal_places=6)
    enabled: bool = False
    mode: str = 'shadow'
    timeout_seconds: int = Field(default=8, ge=1, le=15)
    api_key: SecretStr | None = None
    remove_api_key: bool = False

    @field_validator('provider', 'model', 'mode')
    @classmethod
    def allowed(cls, value, info):
        expected = {'provider': 'typesafe', 'model': MODEL, 'mode': 'shadow'}[info.field_name]
        if value != expected:
            raise ValueError('Valor não suportado.')
        return value

    @field_validator('api_key')
    @classmethod
    def validate_key(cls, value):
        if value is not None:
            raw = value.get_secret_value().strip()
            if not 8 <= len(raw) <= 4096 or any(ord(c) < 33 or ord(c) > 126 for c in raw):
                raise ValueError('Formato de credencial inválido.')
            return SecretStr(raw)
        return value

    @model_validator(mode='after')
    def exclusive_secret_action(self):
        if self.remove_api_key and self.api_key is not None:
            raise ValueError('Escolha atualizar ou remover a credencial.')
        return self


def _fernet(empresa_id: UUID) -> Fernet:
    # Dedicated key wins; an explicitly invalid dedicated key never falls back.
    raw = os.getenv('JEV_CREDENTIAL_ENCRYPTION_KEY')
    if raw is None:
        raw = os.getenv('META_CREDENTIAL_ENCRYPTION_KEY', '')
    if len(raw.strip()) < 32:
        raise JevCredentialError('Armazenamento seguro indisponível.')
    domain = b'agentos/jev/credential/v1/' + str(empresa_id).encode('ascii')
    digest = hmac.new(raw.encode('utf-8'), domain, hashlib.sha256).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def credential_storage_available() -> bool:
    try:
        _fernet(UUID(int=0))
        return True
    except JevCredentialError:
        return False


def encrypt_key(empresa_id: UUID, secret: SecretStr) -> str:
    return _fernet(empresa_id).encrypt(secret.get_secret_value().encode()).decode('ascii')


def decrypt_key(empresa_id: UUID, encrypted: str) -> SecretStr:
    try:
        return SecretStr(_fernet(empresa_id).decrypt(encrypted.encode('ascii')).decode())
    except (InvalidToken, UnicodeError, ValueError) as exc:
        raise JevCredentialError('Credencial indisponível; atualize a chave no painel.') from exc


@dataclass
class JevTenantConfig:
    empresa_id: UUID
    provider: str = 'typesafe'
    model: str = MODEL
    daily_budget_usd: Decimal = Decimal('0')
    enabled: bool = False
    mode: str = 'shadow'
    timeout_seconds: int = 8
    has_key: bool = False
    api_key: SecretStr | None = field(default=None, repr=False)
    connection_status: str = 'not_configured'
    tested_at: object = None
    version: int = 0

    def public(self) -> dict:
        status = 'not_configured' if not self.has_key else self.connection_status
        return {
            'empresa_id': str(self.empresa_id), 'provider': self.provider, 'model': self.model,
            'daily_budget_usd': str(self.daily_budget_usd), 'enabled': self.enabled,
            'mode': self.mode, 'timeout_seconds': self.timeout_seconds, 'has_key': self.has_key,
            'configured': self.has_key, 'status': status,
            'tested_at': self.tested_at.isoformat() if self.tested_at else None,
            'version': self.version, 'credential_storage_available': credential_storage_available(),
            'synthetic_only': True, 'real_conversation_egress_enabled': False,
        }


async def tenant_exists(session: AsyncSession, empresa_id: UUID) -> bool:
    return (await session.execute(text('SELECT 1 FROM empresas WHERE id=:id'), {'id': empresa_id})).scalar_one_or_none() is not None


async def load_config(session: AsyncSession, empresa_id: UUID, *, decrypt: bool = True) -> JevTenantConfig:
    row = (await session.execute(text('SELECT * FROM jev_tenant_config WHERE empresa_id=:id'), {'id': empresa_id})).mappings().first()
    if row is None:
        return JevTenantConfig(empresa_id=empresa_id)
    config = JevTenantConfig(empresa_id=empresa_id, provider=row['provider'], model=row['model'],
        daily_budget_usd=row['daily_budget_usd'], enabled=row['enabled'], mode=row['mode'],
        timeout_seconds=row['timeout_seconds'], has_key=bool(row['api_key_encrypted']),
        connection_status=row['connection_status'], tested_at=row['tested_at'], version=row['version'])
    if config.has_key and decrypt:
        try:
            config.api_key = decrypt_key(empresa_id, row['api_key_encrypted'])
        except JevCredentialError:
            config.connection_status = 'credential_unavailable'
    return config


async def save_config(session: AsyncSession, empresa_id: UUID, update: JevConfigUpdate) -> JevTenantConfig:
    encrypted = encrypt_key(empresa_id, update.api_key) if update.api_key is not None else None
    # Single atomic UPSERT preserves an omitted credential even across concurrent updates.
    await session.execute(text('''
        INSERT INTO jev_tenant_config (empresa_id,provider,model,daily_budget_usd,enabled,mode,
          timeout_seconds,api_key_encrypted,connection_status)
        VALUES (:id,:provider,:model,:budget,:enabled,'shadow',:timeout,:encrypted,'not_evaluated')
        ON CONFLICT (empresa_id) DO UPDATE SET provider=EXCLUDED.provider,model=EXCLUDED.model,
          daily_budget_usd=EXCLUDED.daily_budget_usd,enabled=EXCLUDED.enabled,mode='shadow',
          timeout_seconds=EXCLUDED.timeout_seconds,
          api_key_encrypted=CASE WHEN :remove THEN NULL WHEN :replace THEN EXCLUDED.api_key_encrypted
            ELSE jev_tenant_config.api_key_encrypted END,
          connection_status='not_evaluated',tested_at=NULL,version=jev_tenant_config.version+1,updated_at=now()
    '''), {'id': empresa_id, 'provider': update.provider, 'model': update.model,
        'budget': update.daily_budget_usd, 'enabled': update.enabled, 'timeout': update.timeout_seconds,
        'encrypted': encrypted, 'remove': update.remove_api_key, 'replace': update.api_key is not None})
    await session.commit()
    return await load_config(session, empresa_id, decrypt=False)


async def test_connection(session: AsyncSession, empresa_id: UUID) -> dict:
    config = await load_config(session, empresa_id)
    if not config.has_key:
        return config.public()
    status = 'credential_unavailable'
    if config.api_key is not None:
        try:
            async with httpx.AsyncClient(timeout=config.timeout_seconds, follow_redirects=False, trust_env=False) as client:
                response = await client.get('https://api.typesafe.ai/v1/models', headers={
                    'Authorization': 'Bearer ' + config.api_key.get_secret_value()})
            if response.status_code == 200:
                payload = response.json()
                models = payload.get('models') if isinstance(payload, dict) else None
                status = 'connected' if isinstance(models, list) and models and all(
                    isinstance(item, dict) and isinstance(item.get('name'), str) for item in models
                ) else 'invalid_response'
            else:
                status = {401: 'invalid_credentials', 403: 'invalid_credentials', 429: 'rate_limited',
                    529: 'unavailable'}.get(response.status_code, 'unavailable')
        except httpx.TimeoutException:
            status = 'timeout'
        except (httpx.HTTPError, ValueError, TypeError):
            status = 'unavailable'
    # Do not overwrite newer configuration status if changed during network call.
    await session.execute(text('UPDATE jev_tenant_config SET connection_status=:status,tested_at=now() '
        'WHERE empresa_id=:id AND version=:version'), {'status': status, 'id': empresa_id, 'version': config.version})
    await session.commit()
    return (await load_config(session, empresa_id, decrypt=False)).public()
