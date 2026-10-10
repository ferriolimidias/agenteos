from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken


class MetaCredentialCryptoError(ValueError):
    pass


def _derive_fernet_key(raw_key: str) -> bytes:
    key = str(raw_key or "").strip()
    if len(key) < 32:
        raise MetaCredentialCryptoError("META_CREDENTIAL_ENCRYPTION_KEY ausente ou curta demais.")
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _fernet(raw_key: str | None = None) -> Fernet:
    key = raw_key if raw_key is not None else os.getenv("META_CREDENTIAL_ENCRYPTION_KEY")
    return Fernet(_derive_fernet_key(str(key or "")))


def encrypt_meta_secret(value: str, raw_key: str | None = None) -> str:
    secret = str(value or "").strip()
    if not secret:
        raise MetaCredentialCryptoError("Segredo Meta vazio.")
    return _fernet(raw_key).encrypt(secret.encode("utf-8")).decode("ascii")


def decrypt_meta_secret(value: str, raw_key: str | None = None) -> str:
    encrypted = str(value or "").strip()
    if not encrypted:
        raise MetaCredentialCryptoError("Segredo Meta criptografado ausente.")
    try:
        return _fernet(raw_key).decrypt(encrypted.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError) as exc:
        raise MetaCredentialCryptoError("Credencial Meta invalida ou chave incorreta.") from exc


def mask_last4(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return f"***{text[-4:]}" if len(text) > 4 else "***"

