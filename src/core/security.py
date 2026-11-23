import base64
import bcrypt
from cryptography.fernet import Fernet
from datetime import datetime, timedelta, timezone
from typing import Optional, Any
from jose import jwt, JWTError

from src.config import settings


def get_fernet_cipher() -> Fernet:
    """Returns Fernet cipher instance using settings.ENCRYPTION_MASTER_KEY."""
    try:
        key = settings.ENCRYPTION_MASTER_KEY.encode()
        # If it's not 32 url-safe base64-encoded bytes, pad/hash or ensure valid
        if len(key) != 44:
            # Generate deterministic 32-byte key from sha256
            import hashlib
            h = hashlib.sha256(key).digest()
            key = base64.urlsafe_b64encode(h)
        return Fernet(key)
    except Exception:
        # Fallback to deterministic key
        import hashlib
        h = hashlib.sha256(b"default_reseller_master_key_fallback_seed").digest()
        return Fernet(base64.urlsafe_b64encode(h))


def encrypt_secret(raw_text: str) -> bytes:
    """Encrypts raw text string into bytes."""
    cipher = get_fernet_cipher()
    return cipher.encrypt(raw_text.encode("utf-8"))


def decrypt_secret(encrypted_bytes: bytes) -> str:
    """Decrypts bytes into string."""
    cipher = get_fernet_cipher()
    return cipher.decrypt(encrypted_bytes).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        pw_bytes = plain_password.encode("utf-8")[:72]
        return bcrypt.checkpw(pw_bytes, hashed_password.encode("utf-8"))
    except Exception:
        return False


def get_password_hash(password: str) -> str:
    pw_bytes = password.encode("utf-8")[:72]
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pw_bytes, salt).decode("utf-8")


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return encoded_jwt


def decode_access_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        return payload
    except JWTError:
        return None
