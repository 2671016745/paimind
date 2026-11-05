"""Run the API server inside the desktop process.

The desktop app embeds the same FastAPI app so the UI works with zero external
setup, while still exposing HTTP on 127.0.0.1:8686 for scripts and the mobile PWA.
"""

from __future__ import annotations

import threading
from typing import Optional

import uvicorn

from core.config import get_config


class ServerHost:
    def __init__(self) -> None:
        self._server: Optional[uvicorn.Server] = None
        self._thread: Optional[threading.Thread] = None
        self._error: Optional[str] = None

    @property
    def running(self) -> bool:
        return self._server is not None and self._server.started

    @property
    def error(self) -> Optional[str]:
        return self._error

    def start(self, host: Optional[str] = None, port: Optional[int] = None) -> bool:
        if self.running:
            return True
        cfg = get_config()
        host = host or str(cfg.get("api.host", "127.0.0.1"))
        port = int(port or cfg.get("api.port", 8686))

        try:
            config = uvicorn.Config(
                "server.app:create_app", factory=True, host=host, port=port,
                log_level="warning", access_log=False,
            )
            self._server = uvicorn.Server(config)
            self._thread = threading.Thread(target=self._server.run, daemon=True)
            self._thread.start()
        except Exception as exc:
            self._error = f"{type(exc).__name__}: {exc}"
            return False
        return True

    def wait_ready(self, timeout: float = 8.0) -> bool:
        import time

        import httpx

        cfg = get_config()
        url = f"http://{cfg.get('api.host')}:{cfg.get('api.port')}/health"
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._server is not None and self._server.started:
                try:
                    if httpx.get(url, timeout=1.0).status_code == 200:
                        return True
                except Exception:
                    pass
            time.sleep(0.2)
        return False

    def stop(self) -> None:
        if self._server is not None:
            try:
                self._server.should_exit = True
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        self._server = None
        self._thread = None
