"""Encryption at rest for the credentials of managed connectors (test identities: tokens and passwords).

Values are stored as `enc:v1:<fernet token>`. The key comes from TOOLS_SECRET_KEY: a Fernet key, `env:NAME` (a variable
of the tools service process) or `file:PATH` (a file only the tools service can read). With nothing set a key file is created next to the
database (`<database folder>/secret.key`) the first time a credential is saved, so a copy of the database file alone does
not reveal credentials. Production service credentials are never stored: connectors name them by `secret_ref`.
Reading accepts old plaintext values; the next write (or the start-up sweep) seals them.
"""
import copy
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .config import AppError

PREFIX = "enc:v1:"
SECRET_FIELDS = ("token", "password")  # the secret parts of one identity of a connector
KEY_FILE = "secret.key"


def key_for(settings, create=True):
    """The Fernet key in use, or None when there is no key yet and `create` is False."""
    ref = (getattr(settings, "secret_key", "") or "").strip()
    if ref.startswith("env:"):
        value = os.environ.get(ref[4:], "").strip()
        if not value:
            raise AppError("secret_key_missing", f"TOOLS_SECRET_KEY names {ref}, which is not set", 500)
        return value.encode()
    path = Path(ref[5:]) if ref.startswith("file:") else None
    if ref and path is None:
        return ref.encode()
    if path is None:
        path = Path(getattr(settings, "database_path", "data/tools.sqlite3")).resolve().parent / KEY_FILE
    if path.is_file():
        return path.read_text(encoding="utf-8").strip().encode()
    if not create:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    path.write_text(key.decode() + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:  # Windows: the folder permissions apply
        pass
    return key


def seal_value(value, settings):
    if not isinstance(value, str) or not value or value.startswith(PREFIX):
        return value
    return PREFIX + Fernet(key_for(settings)).encrypt(value.encode("utf-8")).decode("ascii")


def open_value(value, settings):
    if not isinstance(value, str) or not value.startswith(PREFIX):
        return value
    key = key_for(settings, create=False)
    if key is None:
        raise AppError("secret_key_missing", "Credentials are stored encrypted but no key is available (TOOLS_SECRET_KEY)", 500)
    try:
        return Fernet(key).decrypt(value[len(PREFIX):].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        raise AppError("secret_key_wrong", "The stored credentials cannot be decrypted with the configured key (TOOLS_SECRET_KEY)", 500)


def _map(content, settings, convert):
    out = copy.deepcopy(content)
    for identity in (out.get("identities") or {}).values():
        if isinstance(identity, dict):
            for field in SECRET_FIELDS:
                if field in identity:
                    identity[field] = convert(identity[field], settings)
    return out


def seal(content, settings):
    """A copy of a connector's content with every identity secret encrypted (already sealed values are kept)."""
    return _map(content, settings, seal_value)


def unseal(content, settings):
    """A copy with every identity secret decrypted, for the executor and checks. Never returned by an API."""
    return _map(content, settings, open_value)


def has_plaintext(content):
    return any(isinstance(i, dict) and any(isinstance(i.get(f), str) and i[f] and not i[f].startswith(PREFIX) for f in SECRET_FIELDS)
               for i in (content.get("identities") or {}).values())
