"""HTTP client for the paimind API.

The desktop app talks to the API over HTTP even when the server runs in-process,
so the UI behaves identically in both modes (and the mobile PWA reuses the same
wire format).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import httpx

from core.config import get_config


class ApiClient:
    def __init__(self, base_url: Optional[str] = None, token: Optional[str] = None) -> None:
        cfg = get_config()
        self.base_url = (base_url or f"http://{cfg.get('api.host')}:{cfg.get('api.port')}").rstrip("/")
        self.token = token
        self._client = httpx.Client(timeout=30.0)

    # -- low level ---------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        return self._client.request(method, f"{self.base_url}{path}",
                                    headers=self._headers(), **kwargs)

    def get(self, path: str, **kwargs) -> Dict[str, Any]:
        r = self._request("GET", path, **kwargs)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, body: Optional[dict] = None, **kwargs) -> Dict[str, Any]:
        r = self._request("POST", path, json=body, **kwargs)
        r.raise_for_status()
        return r.json()

    def put(self, path: str, body: Optional[dict] = None, **kwargs) -> Dict[str, Any]:
        r = self._request("PUT", path, json=body, **kwargs)
        r.raise_for_status()
        return r.json()

    # -- domain ------------------------------------------------------------

    def health(self) -> Dict[str, Any]:
        return self.get("/health")

    def hardware(self, refresh: bool = False) -> Dict[str, Any]:
        return self.get("/v1/hardware", params={"refresh": refresh})

    def models(self, refresh: bool = False) -> Dict[str, Any]:
        return self.get("/v1/models", params={"refresh": refresh})

    def decide(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.post("/v1/decide", payload)

    def decide_stream(self, payload: Dict[str, Any], on_event):
        """Consume the SSE stream, invoking ``on_event(event_name, data)``."""

        with self._client.stream("POST", f"{self.base_url}/v1/decide/stream",
                                 headers=self._headers(), json=payload,
                                 timeout=httpx.Timeout(300.0)) as resp:
            resp.raise_for_status()
            event = None
            for line in resp.iter_lines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    raw = line[5:].strip()
                    try:
                        data = json.loads(raw) if raw else {}
                    except ValueError:
                        data = {"raw": raw}
                    on_event(event, data)

    def engines(self, refresh: bool = False) -> Dict[str, Any]:
        return self.get("/v1/engines", params={"refresh": refresh})

    def test_engine(self, engine: str) -> Dict[str, Any]:
        return self.post(f"/v1/engines/{engine}/test")

    def set_deploy_mode(self, tier: str, mode: str) -> Dict[str, Any]:
        return self.put(f"/v1/models/{tier}/deploy-mode", {"mode": mode})

    def activate_tier(self, tier: str) -> Dict[str, Any]:
        return self.post(f"/v1/models/{tier}/activate")

    def download_sources(self) -> Dict[str, Any]:
        return self.get("/v1/download-sources")

    def test_mirror(self, provider: str, mirror: str) -> Dict[str, Any]:
        return self.post(f"/v1/download-sources/{provider}/{mirror}/test")

    def restore_sources(self) -> Dict[str, Any]:
        return self.post("/v1/download-sources/restore-defaults")

    def start_download(self, tier: str, provider: str, mirror: str) -> Dict[str, Any]:
        return self.post(f"/v1/models/{tier}/download",
                         params={"provider": provider, "mirror": mirror})

    def download_status(self, task_id: str) -> Dict[str, Any]:
        return self.get(f"/v1/downloads/{task_id}")

    def hf_status(self) -> Dict[str, Any]:
        return self.get("/v1/hf/auth/status")

    def hf_login(self, token: str) -> Dict[str, Any]:
        return self.post("/v1/hf/auth/login", {"token": token})

    def hf_logout(self) -> Dict[str, Any]:
        return self.post("/v1/hf/auth/logout")

    def hf_repos(self) -> Dict[str, Any]:
        return self.get("/v1/hf/repos")

    def hf_create_repo(self, name: str, private: bool, readme: bool) -> Dict[str, Any]:
        return self.post("/v1/hf/repos", {"name": name, "private": private,
                                          "create_readme": readme})

    def hf_set_download_source(self, repo: str) -> Dict[str, Any]:
        return self.post("/v1/hf/repos/set-download-source", {"repo": repo})

    def upload_preview(self, repo: str, tiers: List[str], include_custom: bool) -> Dict[str, Any]:
        return self.post("/v1/upload/tasks/preview",
                         {"repo": repo, "tiers": tiers, "include_custom": include_custom})

    def upload_start(self, repo: str, tiers: List[str], include_custom: bool) -> Dict[str, Any]:
        return self.post("/v1/upload/tasks",
                         {"repo": repo, "tiers": tiers, "include_custom": include_custom})

    def upload_status(self, task_id: str) -> Dict[str, Any]:
        return self.get(f"/v1/upload/tasks/{task_id}")

    def upload_cancel(self, task_id: str) -> Dict[str, Any]:
        return self.post(f"/v1/upload/tasks/{task_id}/cancel")

    def upload_candidates(self) -> Dict[str, Any]:
        return self.get("/v1/upload/candidates")

    def usage(self) -> Dict[str, Any]:
        return self.get("/v1/usage")

    def config(self) -> Dict[str, Any]:
        return self.get("/v1/config")

    def why_unavailable(self, tier: str, mode: Optional[str] = None) -> Dict[str, Any]:
        params = {"mode": mode} if mode else None
        return self.get(f"/v1/models/{tier}/why-unavailable", params=params)

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass
