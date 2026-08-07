from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import bcrypt
from cryptography.fernet import Fernet
from jose import JWTError, jwt

from app.config import get_settings

settings = get_settings()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        return False


def create_access_token(subject: str, extra: dict[str, Any] | None = None) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": subject, "exp": expire}
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise ValueError("Invalid token") from exc


def _fernet() -> Fernet:
    key = settings.encryption_key.encode()
    try:
        return Fernet(key)
    except Exception:
        import base64
        import hashlib

        digest = hashlib.sha256(settings.encryption_key.encode()).digest()
        return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_token(raw: str) -> bytes:
    return _fernet().encrypt(raw.encode())


def decrypt_token(blob: bytes) -> str:
    return _fernet().decrypt(blob).decode()


def parse_uuid(value: str | UUID) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))
