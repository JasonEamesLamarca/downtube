"""Token encryption at rest (Fernet = AES-128-CBC + HMAC-SHA256) and key derivation.

Keys are derived from ``DOWNTUBE_SECRET_KEY`` with HKDF so the session key and the
token-encryption key are independent.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from downtube.config import get_config


class CryptoError(Exception):
    pass


def _derive(info: bytes, secret: str | None = None) -> bytes:
    key = (secret if secret is not None else get_config().secret_key).encode()
    if len(key) < 32:
        raise CryptoError("DOWNTUBE_SECRET_KEY must be at least 32 characters")
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=b"downtube-v1", info=info).derive(key)


@lru_cache(maxsize=4)
def _fernet(secret: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(_derive(b"token-encryption", secret)))


def encrypt(plaintext: str) -> str:
    return _fernet(get_config().secret_key).encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet(get_config().secret_key).decrypt(token.encode()).decode()
    except InvalidToken as e:  # wrong key or tampered
        raise CryptoError("cannot decrypt stored token (secret key changed?)") from e


def random_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return secrets.compare_digest(a.encode(), b.encode())
