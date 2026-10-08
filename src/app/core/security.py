"""Password hashing, JWT access tokens and integration API keys."""

import hashlib
import secrets
from datetime import timedelta

import bcrypt
import jwt

from app.common.utils import utcnow
from app.core.config import settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


def create_access_token(username: str, role: str) -> str:
    now = utcnow()
    payload = {"sub": username, "role": role, "iat": now, "exp": now + timedelta(minutes=settings.jwt_expire_minutes)}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def hash_api_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def generate_api_key() -> str:
    return "pe_" + secrets.token_urlsafe(32)
