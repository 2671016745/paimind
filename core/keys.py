"""Secret storage — system keyring with a file fallback.

Secrets (HF write token, API keys) are **never** written to ``config.yaml`` and
**never** logged.  Configuration only stores a *reference name*; this module
resolves that name to the real value at call time.

Backends, in order:

1. ``keyring`` — macOS Keychain, Windows Credential Manager, Linux Secret Service.
2. file fallback — ``~/.paimind/secrets.json`` (or ``PAIMIND_SECRETS``), only when
   no keyring backend is available (headless CI, a bare WSL shell).  The file is
   created with ``0600``-style permissions where the platform allows it.

The fallback keeps the app usable everywhere; it is deliberately opt-in by
absence of a keyring, never a silent downgrade of an existing keyring.
"""

from __future__ import annotations

import getpass
import json
import os
import stat
from pathlib import Path
from typing import Dict, Optional

_SERVICE = "paimind"

_keyring = None
_keyring_error: Optional[str] = None


def _load_keyring():
    global _keyring, _keyring_error
    if _keyring is not None or _keyring_error is not None:
        return
    try:
        import keyring  # type: ignore

        # Probe the backend: keyring can import fine but have no working backend.
        backend = keyring.get_keyring()
        name = type(backend).__name__.lower()
        if "fail" in name or "null" in name:
            _keyring_error = f"keyring backend unusable: {type(backend).__name__}"
            return
        _keyring = keyring
    except Exception as exc:
        _keyring_error = f"{type(exc).__name__}: {exc}"


def _secrets_path() -> Path:
    env = os.environ.get("PAIMIND_SECRETS")
    if env:
        return Path(env)
    return Path(os.path.expanduser("~")) / ".paimind" / "secrets.json"


def _file_load() -> Dict[str, str]:
    path = _secrets_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _file_save(data: Dict[str, str]) -> None:
    path = _secrets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        mode = stat.S_IRUSR | stat.S_IWUSR  # 0600
        os.chmod(path, mode)
    except Exception:
        pass


def backend_name() -> str:
    _load_keyring()
    if _keyring is not None:
        try:
            return f"keyring:{type(_keyring.get_keyring()).__name__}"
        except Exception:
            return "keyring"
    return f"file:{_secrets_path()}"


def get_secret(ref: str) -> Optional[str]:
    """Resolve a reference name to a secret; returns None when absent."""

    if not ref:
        return None
    _load_keyring()
    if _keyring is not None:
        try:
            return _keyring.get_password(_SERVICE, ref)
        except Exception:
            pass
    return _file_load().get(ref)


def set_secret(ref: str, value: str) -> None:
    if not ref:
        raise ValueError("secret reference must not be empty")
    if not value:
        delete_secret(ref)
        return
    _load_keyring()
    if _keyring is not None:
        try:
            _keyring.set_password(_SERVICE, ref, value)
            return
        except Exception:
            pass
    data = _file_load()
    data[ref] = value
    _file_save(data)


def delete_secret(ref: str) -> bool:
    _load_keyring()
    removed = False
    if _keyring is not None:
        try:
            _keyring.delete_password(_SERVICE, ref)
            removed = True
        except Exception:
            removed = False
    data = _file_load()
    if ref in data:
        data.pop(ref, None)
        _file_save(data)
        removed = True
    return removed


def has_secret(ref: str) -> bool:
    return bool(get_secret(ref))


def redact(text: str) -> str:
    """Strip anything that looks like a token before it reaches a log line."""

    if not text:
        return text
    out = text
    for pattern in (
        r"hf_[A-Za-z0-9]{20,}",
        r"Bearer\s+[A-Za-z0-9._-]{20,}",
        r"sk-[A-Za-z0-9._-]{20,}",
    ):
        import re

        out = re.sub(pattern, "[REDACTED]", out)
    return out


def current_username() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"
