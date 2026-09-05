"""Local, symmetric encryption for broker credentials.

Credentials never leave this machine. They're encrypted at rest with a Fernet
key derived from PORTAL_SECRET_KEY so a leaked config file isn't a leaked login.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

try:
    from cryptography.fernet import Fernet, InvalidToken
    _HAVE_CRYPTO = True
except Exception:  # pragma: no cover
    _HAVE_CRYPTO = False

from .config import ROOT, settings

VAULT_PATH = ROOT / "secrets" / "vault.enc"


def _fernet() -> "Fernet":
    if not _HAVE_CRYPTO:
        raise RuntimeError("cryptography not installed; run pip install cryptography")
    key = settings.secret_key
    if not key:
        raise RuntimeError(
            "PORTAL_SECRET_KEY is not set. Generate one with:\n"
            '  python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        )
    # Accept either a raw Fernet key or an arbitrary passphrase.
    try:
        return Fernet(key.encode())
    except Exception:
        digest = hashlib.sha256(key.encode()).digest()
        return Fernet(base64.urlsafe_b64encode(digest))


def save_credentials(creds: dict[str, Any]) -> None:
    """Encrypt and persist a credentials dict to the vault."""
    VAULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    token = _fernet().encrypt(json.dumps(creds).encode())
    VAULT_PATH.write_bytes(token)


def load_credentials() -> dict[str, Any]:
    """Decrypt the vault, returning {} if absent or key is wrong."""
    if not VAULT_PATH.exists():
        return {}
    try:
        data = _fernet().decrypt(VAULT_PATH.read_bytes())
        return json.loads(data.decode())
    except Exception:  # InvalidToken or missing crypto
        return {}


def mask(value: str, keep: int = 2) -> str:
    """Render a secret safe for logs/UI: 'ab****yz'."""
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}{'*' * (len(value) - keep * 2)}{value[-keep:]}"
