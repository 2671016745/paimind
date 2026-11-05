"""Shared pytest fixtures."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import Config  # noqa: E402


@pytest.fixture
def cfg(tmp_path) -> Config:
    """A config whose data lives in a temp dir so tests never touch the real one."""

    return Config.with_overrides({
        "app": {"data_dir": str(tmp_path / "data"), "models_dir": str(tmp_path / "models")},
        "api": {"rate_limit_per_minute": 0},
    })


@pytest.fixture
def ctx(cfg):
    from core.context import AppContext

    context = AppContext(cfg)
    yield context
    context.close_all()


@pytest.fixture(scope="session")
def stub_engine():
    """Start the OpenAI-compatible stub engine once for the whole session.

    Skipped automatically when the port is already taken.
    """

    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "tests" / "stub_engine.py"),
         "--port", str(port), "--model", "qwen2.5:1.5b"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            import httpx

            if httpx.get(f"{base}/v1/models", timeout=1).status_code == 200:
                break
        except Exception:
            time.sleep(0.3)
    else:
        proc.kill()
        pytest.skip("stub engine did not start")

    yield {"base_url": base, "port": port, "model": "qwen2.5:1.5b", "proc": proc}
    proc.kill()
    proc.wait(timeout=5)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def api_stub(stub_engine, monkeypatch):
    """API client wired to a config that points at the stub engine.

    Needed by tests that must exercise a *successful* decision over HTTP (SSE,
    usage recording) — the plain ``api`` fixture has no model and no engine.
    """

    import tempfile

    import core.config as config_module

    data_dir = tempfile.mkdtemp(prefix="paimind-api-")
    cfg = Config.with_overrides({
        "app": {"data_dir": data_dir, "models_dir": f"{data_dir}/models"},
        "engines": {"ollama": {
            "base_url": stub_engine["base_url"],
            "openai_base_url": f"{stub_engine['base_url']}/v1",
            "timeout_seconds": 5,
        }},
        "api": {"rate_limit_per_minute": 0},
    })
    monkeypatch.setattr(config_module, "_config", cfg)

    from server import deps
    from server.app import create_app
    from fastapi.testclient import TestClient

    from core.runtime import save_state

    save_state(tier="L1", mode="ollama", cfg=cfg)
    deps.reset_ctx()
    app = create_app()
    with TestClient(app) as client:
        yield client
    deps.reset_ctx()


@pytest.fixture
def api(cfg, monkeypatch):
    """A TestClient wired to the temp config.

    The server's context singleton is rebuilt against *cfg* so tests never read
    or write the developer's real ``data/`` directory.
    """

    import core.config as config_module

    monkeypatch.setattr(config_module, "_config", cfg)
    monkeypatch.setenv("PAIMIND_CONFIG", str(cfg.path or ""))

    from server import deps
    from server.app import create_app
    from fastapi.testclient import TestClient

    deps.reset_ctx()
    app = create_app()
    with TestClient(app) as client:
        yield client
    deps.reset_ctx()
