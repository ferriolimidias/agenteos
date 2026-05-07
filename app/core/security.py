import bcrypt
import base64
import hashlib
import hmac
import json
import os
import time


_BCRYPT_PREFIXES = ("$2a$", "$2b$", "$2y$")
_MAX_BCRYPT_PASSWORD_BYTES = 72
_JWT_ALG = "HS256"
_JWT_DEFAULT_EXP_SECONDS = int(os.getenv("JWT_EXPIRES_SECONDS", "86400"))


def is_bcrypt_hash(value: str | None) -> bool:
    return bool(value) and value.startswith(_BCRYPT_PREFIXES)


def get_password_hash(password: str) -> str:
    password_bytes = _encode_password(password)
    salt = bcrypt.gensalt()
    password_hash = bcrypt.hashpw(password_bytes, salt)
    return password_hash.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    if not is_bcrypt_hash(hashed_password):
        return False

    try:
        plain_password_bytes = _encode_password(plain_password)
        hashed_password_bytes = hashed_password.encode("utf-8")
        return bcrypt.checkpw(plain_password_bytes, hashed_password_bytes)
    except ValueError:
        return False


def _encode_password(password: str) -> bytes:
    password_bytes = password.encode("utf-8")
    if len(password_bytes) > _MAX_BCRYPT_PASSWORD_BYTES:
        raise ValueError("A senha deve ter no máximo 72 bytes para ser armazenada com bcrypt.")
    return password_bytes


def create_access_token(subject: str, extra_claims: dict | None = None, expires_in_seconds: int | None = None) -> str:
    secret_key = _jwt_secret_key()
    now = int(time.time())
    exp_seconds = int(expires_in_seconds or _JWT_DEFAULT_EXP_SECONDS)
    payload = {
        "sub": str(subject),
        "iat": now,
        "exp": now + max(60, exp_seconds),
    }
    if extra_claims:
        payload.update(extra_claims)
    header = {"alg": _JWT_ALG, "typ": "JWT"}
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
    signature = hmac.new(secret_key.encode("utf-8"), signing_input, hashlib.sha256).digest()
    sig_b64 = _b64url_encode(signature)
    return f"{header_b64}.{payload_b64}.{sig_b64}"


def decode_access_token(token: str) -> dict:
    secret_key = _jwt_secret_key()
    token = str(token or "").strip()
    if not token:
        raise ValueError("Token ausente.")
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Formato de token inválido.")
    header_b64, payload_b64, sig_b64 = parts
    signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
    expected_sig = hmac.new(secret_key.encode("utf-8"), signing_input, hashlib.sha256).digest()
    provided_sig = _b64url_decode(sig_b64)
    if not hmac.compare_digest(expected_sig, provided_sig):
        raise ValueError("Assinatura de token inválida.")
    payload_raw = _b64url_decode(payload_b64).decode("utf-8")
    payload = json.loads(payload_raw)
    if not isinstance(payload, dict):
        raise ValueError("Payload de token inválido.")
    exp = int(payload.get("exp", 0) or 0)
    if exp <= int(time.time()):
        raise ValueError("Token expirado.")
    if not str(payload.get("sub") or "").strip():
        raise ValueError("Token sem subject.")
    return payload


def _jwt_secret_key() -> str:
    key = str(os.getenv("SECRET_KEY") or "").strip()
    if len(key) < 32:
        raise ValueError("SECRET_KEY ausente ou muito curta. Defina SECRET_KEY com no mínimo 32 caracteres.")
    return key


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    data = str(data or "")
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))
