"""PII protection module: deterministic HMAC hashing, masking, and AES encryption."""

from __future__ import annotations

import base64
import hashlib
import hmac
from cryptography.fernet import Fernet
from solution.config import DEFAULT_SECRET_KEY

# Cached Fernet instance
_fernet_instance: Fernet | None = None
_current_secret: str | None = None


def _get_fernet(secret: str = DEFAULT_SECRET_KEY) -> Fernet:
    global _fernet_instance, _current_secret
    if _fernet_instance is None or _current_secret != secret:
        key_32 = hashlib.sha256(secret.encode("utf-8")).digest()
        fernet_key = base64.urlsafe_b64encode(key_32)
        _fernet_instance = Fernet(fernet_key)
        _current_secret = secret
    return _fernet_instance


def get_phone_hash(canonical_phone: str, secret: str = DEFAULT_SECRET_KEY) -> str:
    """Compute a deterministic HMAC-SHA256 hash of a canonical phone number.
    
    Used as an opaque index and join key for matching leads and events without
    storing or exposing plaintext phone numbers.
    """
    if not canonical_phone:
        return ""
    return hmac.new(secret.encode("utf-8"), canonical_phone.encode("utf-8"), hashlib.sha256).hexdigest()


def encrypt_pii(val: str, secret: str = DEFAULT_SECRET_KEY) -> str:
    """Encrypt a PII string (e.g. customer name or canonical phone) using AES/Fernet."""
    if not val:
        return ""
    fernet = _get_fernet(secret)
    return fernet.encrypt(val.encode("utf-8")).decode("utf-8")


def decrypt_pii(ciphertext: str, secret: str = DEFAULT_SECRET_KEY) -> str:
    """Decrypt an encrypted PII string."""
    if not ciphertext:
        return ""
    fernet = _get_fernet(secret)
    return fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")


def mask_phone(canonical_phone: str) -> str:
    """Produce the masked phone format required by the output contract:
    '+91******' followed by the last 4 digits (e.g. '+91******5499').
    """
    if not canonical_phone:
        return ""
    last_four = canonical_phone[-4:]
    return f"+91******{last_four}"

