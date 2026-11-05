"""Runtime state: which tier and deploy mode the user is currently on.

Persisted to ``data/runtime.json`` so a restart resumes on the same tier instead
of silently reverting to a default.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from .config import Config, get_config
from .schemas import DeployMode

_DEFAULT = {"current_tier": "L0", "current_mode": "builtin", "allow_online": True}


def _path(cfg: Optional[Config] = None) -> Path:
    cfg = cfg or get_config()
    env = os.environ.get("PAIMIND_RUNTIME")
    if env:
        return Path(env)
    root = Path(cfg.get("app.data_dir", "data")) or Path("data")
    if not root.is_absolute():
        root = Path(__file__).resolve().parent.parent / root
    return root / "runtime.json"


def load_state(cfg: Optional[Config] = None) -> Dict[str, Any]:
    path = _path(cfg)
    if not path.exists():
        return dict(_DEFAULT)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(_DEFAULT)
    state = dict(_DEFAULT)
    state.update({k: v for k, v in data.items() if k in _DEFAULT})
    if state.get("current_mode") not in {m.value for m in DeployMode}:
        state["current_mode"] = "builtin"
    if state.get("current_tier") not in ("L0", "L1", "L2", "L3"):
        state["current_tier"] = "L0"
    return state


_lock = threading.Lock()


def save_state(
    tier: Optional[str] = None,
    mode: Optional[str] = None,
    allow_online: Optional[bool] = None,
    cfg: Optional[Config] = None,
) -> Dict[str, Any]:
    with _lock:
        state = load_state(cfg)
        if tier:
            state["current_tier"] = tier
        if mode:
            state["current_mode"] = mode
        if allow_online is not None:
            state["current_allow_online"] = bool(allow_online)
        path = _path(cfg)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
        return state


def set_current_tier(tier: str, cfg: Optional[Config] = None) -> Dict[str, Any]:
    return save_state(tier=tier, cfg=cfg)


def set_current_mode(mode: str, cfg: Optional[Config] = None) -> Dict[str, Any]:
    return save_state(mode=mode, cfg=cfg)


def current_tier(cfg: Optional[Config] = None) -> str:
    return str(load_state(cfg).get("current_tier", "L0"))


def current_mode(cfg: Optional[Config] = None) -> DeployMode:
    try:
        return DeployMode(load_state(cfg).get("current_mode", "builtin"))
    except ValueError:
        return DeployMode.BUILTIN
