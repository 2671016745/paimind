"""Shared application context.

Both the HTTP server and the desktop app need the same collaborators: the loaded
config, a cached hardware snapshot, the last engine probe results, the set of
downloaded tiers and a small LRU of live backends.  :class:`AppContext` owns them
so the two front-ends cannot drift apart.

Hardware and engine probes are refreshed on demand (``refresh_hardware`` /
``refresh_engines``); reads are served from cache so a request never blocks on
``nvidia-smi``.
"""

from __future__ import annotations

import threading
import time
from typing import Dict, List, Optional

from .config import Config, get_config
from .hardware import HardwareInfo, detect_hardware
from .inference import Resolution, build_backend, resolve_backend
from .inference.base import InferenceBackend
from .schemas import DeployMode, EngineKind, EngineStatus, HardwareInfo as HardwareInfoSchema
from .usage import UsageTracker


class AppContext:
    def __init__(self, cfg: Optional[Config] = None) -> None:
        self.cfg = cfg or get_config()
        self._hardware: Optional[HardwareInfoSchema] = None
        self._hardware_ts: float = 0.0
        self._engines: Dict[EngineKind, EngineStatus] = {}
        self._engines_ts: float = 0.0
        self._backends: Dict[str, InferenceBackend] = {}
        self._lock = threading.RLock()
        self.usage = UsageTracker(self.cfg)

    # -- hardware ---------------------------------------------------------

    @property
    def hardware(self) -> HardwareInfoSchema:
        if self._hardware is None or (time.monotonic() - self._hardware_ts) > 300:
            self.refresh_hardware()
        return self._hardware  # type: ignore[return-value]

    def refresh_hardware(self) -> HardwareInfoSchema:
        hw = detect_hardware(str(self.cfg.models_dir))
        with self._lock:
            self._hardware = hw
            self._hardware_ts = time.monotonic()
        return hw

    # -- engines ----------------------------------------------------------

    @property
    def engines(self) -> Dict[EngineKind, EngineStatus]:
        if not self._engines or (time.monotonic() - self._engines_ts) > 30:
            self.refresh_engines()
        return self._engines

    def refresh_engines(self) -> Dict[EngineKind, EngineStatus]:
        from .engines import probe_all

        statuses = probe_all(self.cfg)
        with self._lock:
            self._engines = statuses
            self._engines_ts = time.monotonic()
        return statuses

    def engine(self, kind: EngineKind) -> EngineStatus:
        return self.engines.get(kind) or EngineStatus(
            engine=kind, state="unreachable", base_url="", enabled=False, reachable=False
        )

    # -- backends ---------------------------------------------------------

    def backend_for(
        self,
        tier_id: str,
        mode: DeployMode,
        downloaded: Optional[Dict[str, bool]] = None,
        container_ok: bool = False,
    ) -> Resolution:
        return resolve_backend(
            tier_id, mode, self.cfg, self.engines, downloaded, container_ok
        )

    def cache_backend(self, key: str, backend: InferenceBackend) -> None:
        with self._lock:
            self._backends[key] = backend

    def get_cached_backend(self, key: str) -> Optional[InferenceBackend]:
        with self._lock:
            return self._backends.get(key)

    def drop_backend(self, key: str) -> None:
        with self._lock:
            backend = self._backends.pop(key, None)
        if backend is not None:
            try:
                backend.close()
            except Exception:
                pass

    def sweep_idle_backends(self) -> List[str]:
        """Unload builtin sessions idle past the configured window."""

        swept: List[str] = []
        with self._lock:
            items = list(self._backends.items())
        for key, backend in items:
            unload = getattr(backend, "unload_if_idle", None)
            if callable(unload) and unload():
                swept.append(key)
        return swept

    def close_all(self) -> None:
        with self._lock:
            backends = list(self._backends.values())
            self._backends.clear()
        for backend in backends:
            try:
                backend.close()
            except Exception:
                pass
