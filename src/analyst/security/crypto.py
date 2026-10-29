"""Credential encryption at rest + API key hashing."""

import hashlib
import json
import secrets
from typing import Any

from cryptography.fernet import Fernet

from analyst.config import get_settings


def _fernet() -> Fernet:
    return Fernet(get_settings().require("app_secret_key").encode())


def encrypt_json(data: dict[str, Any]) -> bytes:
    return _fernet().encrypt(json.dumps(data).encode())


def decrypt_json(token: bytes) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(_fernet().decrypt(bytes(token)))
    return result


def new_api_key() -> str:
    return "dak_" + secrets.token_urlsafe(32)


def hash_api_key(key: str) -> str:
    # Keys are 256-bit random, so a fast hash is fine (no need for bcrypt/argon2 here).
    return hashlib.sha256(key.encode()).hexdigest()
