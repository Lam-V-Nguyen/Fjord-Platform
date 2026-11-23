"""
secretbox.py - Encrypts secrets stored on disk (e.g. client_secret / client_password in credentials.json).
The key derives from SECRET_KEY (config) -> changing SECRET_KEY makes stored values unreadable (users re-enter them).
Older plain values (without the 'enc:' prefix) are still read and get encrypted on the next save.
"""
import base64, hashlib
from cryptography.fernet import Fernet, InvalidToken
from config import SECRET_KEY

_PREFIX = "enc:"
_f = Fernet(base64.urlsafe_b64encode(hashlib.sha256(("fjord-secretbox:" + SECRET_KEY).encode()).digest()))

def encrypt(value: str | None) -> str:
    if not value: return ""
    return _PREFIX + _f.encrypt(str(value).encode()).decode()

def decrypt(value: str | None) -> str:
    if not value: return ""
    if not str(value).startswith(_PREFIX): return str(value)          # legacy plain value
    try: return _f.decrypt(str(value)[len(_PREFIX):].encode()).decode()
    except InvalidToken: return ""                                  # SECRET_KEY changed -> user re-enters it

SECRET_FIELDS = ("client_secret", "client_password")

def protect(record: dict) -> dict:
    return {k: (encrypt(v) if k in SECRET_FIELDS else v) for k, v in record.items()}

def reveal(record: dict) -> dict:
    return {k: (decrypt(v) if k in SECRET_FIELDS else v) for k, v in record.items()}
