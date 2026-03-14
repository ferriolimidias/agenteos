import bcrypt


_BCRYPT_PREFIXES = ("$2a$", "$2b$", "$2y$")
_MAX_BCRYPT_PASSWORD_BYTES = 72


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
