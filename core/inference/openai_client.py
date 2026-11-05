"""OpenAI-compatible HTTP backend.

One implementation serves Ollama, vLLM, llama-server and any other
OpenAI-compatible endpoint.  Engine-specific configuration (base URL, API key,
model map) lives in ``config.yaml`` under ``engines.*``; this class only speaks
the wire protocol.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Iterator, List, Optional, Sequence

import httpx

from ..errors import AppError, ErrorCode
from ..schemas import BackendKind
from .base import (
    BackendUnavailable,
    ChatMessage,
    Completion,
    InferenceBackend,
    parse_completion_text,
)


def to_dicts(messages: Sequence[ChatMessage]) -> List[Dict[str, str]]:
    return [{"role": m.role, "content": m.content} for m in messages]


class OpenAIClientBackend(InferenceBackend):
    """Talks to ``{base_url}/chat/completions``."""

    engine_key = "openai"
    name = "openai-compatible"

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: Optional[str] = None,
        timeout_seconds: float = 60.0,
        probe_timeout_seconds: float = 2.0,
        label: str = "openai",
        extra_headers: Optional[Dict[str, str]] = None,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.probe_timeout_seconds = probe_timeout_seconds
        self.label = label
        self.extra_headers = extra_headers or {}
        self._client = http_client or httpx.Client(timeout=timeout_seconds)
        self._owns_client = http_client is None

    # -- InferenceBackend -------------------------------------------------

    def kind(self) -> BackendKind:
        key = self.label.lower()
        if key == "ollama":
            return BackendKind.OLLAMA
        if key == "vllm":
            return BackendKind.VLLM
        if key in ("llamacpp", "llama.cpp", "llama-server"):
            return BackendKind.LLAMACPP
        return BackendKind.CPU

    def ready(self) -> bool:
        try:
            r = self._client.get(
                f"{self.base_url}/models",
                headers=self._headers(),
                timeout=self.probe_timeout_seconds,
            )
            if r.status_code == 200:
                return True
        except Exception:
            return False
        # Some servers only expose /v1/models
        try:
            r = self._client.get(
                f"{self.base_url}/models",
                headers=self._headers(),
                timeout=self.probe_timeout_seconds,
            )
            return r.status_code == 200
        except Exception:
            return False

    def complete(
        self,
        messages: Sequence[ChatMessage],
        max_tokens: int = 256,
        temperature: float = 0.3,
        stop: Optional[Sequence[str]] = None,
    ) -> Completion:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": to_dicts(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        if stop:
            payload["stop"] = list(stop)

        start = time.perf_counter()
        try:
            resp = self._client.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise BackendUnavailable(self.label, self.base_url, "请求超时") from exc
        except httpx.HTTPError as exc:
            raise BackendUnavailable(self.label, self.base_url, str(exc)) from exc

        latency = int((time.perf_counter() - start) * 1000)

        if resp.status_code in (404, 405):
            raise BackendUnavailable(self.label, self.base_url, f"HTTP {resp.status_code}")
        if resp.status_code == 401:
            raise AppError(ErrorCode.UNAUTHORIZED, f"{self.label} 返回 401，请检查 API key",
                           {"engine": self.label})
        if resp.status_code == 429:
            raise AppError(ErrorCode.RATE_LIMITED, f"{self.label} 触发限流", {"engine": self.label})
        if resp.status_code >= 500:
            raise BackendUnavailable(self.label, self.base_url, f"HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise AppError(ErrorCode.INVALID_REQUEST, f"{self.label} 返回 HTTP {resp.status_code}",
                           {"engine": self.label, "body": resp.text[:500]})

        try:
            data = resp.json()
        except ValueError as exc:
            raise AppError(ErrorCode.INFERENCE_ERROR, f"{self.label} 返回非 JSON 响应",
                           {"body": resp.text[:300]}) from exc

        try:
            choice = data["choices"][0]
            text = choice["message"]["content"] or ""
            usage = data.get("usage") or {}
        except (KeyError, IndexError, TypeError) as exc:
            raise AppError(ErrorCode.INFERENCE_ERROR, f"{self.label} 响应结构异常",
                           {"body": str(data)[:300]}) from exc

        return Completion(
            text=parse_completion_text(text),
            backend=self.label,
            model=data.get("model", self.model),
            prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage.get("completion_tokens", 0) or 0),
            latency_ms=latency,
            finish_reason=str(choice.get("finish_reason", "") or ""),
            raw=data,
        )

    def stream(
        self,
        messages: Sequence[ChatMessage],
        max_tokens: int = 256,
        temperature: float = 0.3,
        stop: Optional[Sequence[str]] = None,
    ) -> Iterator[str]:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": to_dicts(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if stop:
            payload["stop"] = list(stop)

        try:
            with self._client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
                timeout=self.timeout_seconds,
            ) as resp:
                if resp.status_code >= 400:
                    body = resp.read().decode("utf-8", "replace")
                    raise BackendUnavailable(self.label, self.base_url, f"HTTP {resp.status_code}")
                for line in resp.iter_lines():
                    if not line:
                        continue
                    if line.startswith("data:"):
                        chunk = line[5:].strip()
                    else:
                        chunk = line.strip()
                    if not chunk or chunk == "[DONE]":
                        continue
                    try:
                        obj = json.loads(chunk)
                    except ValueError:
                        continue
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    delta = (choices[0].get("delta") or {}).get("content")
                    if delta:
                        yield delta
        except httpx.TimeoutException as exc:
            raise BackendUnavailable(self.label, self.base_url, "流式请求超时") from exc
        except httpx.HTTPError as exc:
            raise BackendUnavailable(self.label, self.base_url, str(exc)) from exc

    def close(self) -> None:
        if self._owns_client:
            try:
                self._client.close()
            except Exception:
                pass

    # -- internals --------------------------------------------------------
    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json", **self.extra_headers}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers


class OllamaBackend(OpenAIClientBackend):
    name = "ollama"
    engine_key = "ollama"

    def __init__(self, config: Dict[str, Any], model: str, **kwargs: Any) -> None:
        super().__init__(
            base_url=str(config.get("openai_base_url") or (str(config.get("base_url", "")).rstrip("/") + "/v1")),
            model=model,
            api_key=config.get("api_key") or "ollama",
            # Probe and request timeouts are deliberately separate: a 2 s probe
            # must not also cap a real inference call.
            timeout_seconds=float(config.get("request_timeout_seconds") or 120),
            probe_timeout_seconds=float(config.get("probe_timeout_seconds") or 2),
            label="ollama",
            **kwargs,
        )

    def kind(self) -> BackendKind:
        return BackendKind.OLLAMA


class VllmBackend(OpenAIClientBackend):
    name = "vllm"
    engine_key = "vllm"

    def __init__(self, config: Dict[str, Any], model: str, **kwargs: Any) -> None:
        super().__init__(
            base_url=str(config.get("openai_base_url") or (str(config.get("base_url", "")).rstrip("/") + "/v1")),
            model=model,
            api_key=config.get("api_key") or "EMPTY",
            timeout_seconds=float(config.get("request_timeout_seconds") or 120),
            probe_timeout_seconds=float(config.get("probe_timeout_seconds") or 2),
            label="vllm",
            **kwargs,
        )

    def kind(self) -> BackendKind:
        return BackendKind.VLLM


class LlamaCppBackend(OpenAIClientBackend):
    name = "llama.cpp"
    engine_key = "llamacpp"

    def __init__(self, config: Dict[str, Any], model: str, **kwargs: Any) -> None:
        super().__init__(
            base_url=str(config.get("openai_base_url") or (str(config.get("base_url", "")).rstrip("/") + "/v1")),
            model=model,
            api_key=config.get("api_key") or "llamacpp",
            timeout_seconds=float(config.get("request_timeout_seconds") or 120),
            probe_timeout_seconds=float(config.get("probe_timeout_seconds") or 2),
            label="llamacpp",
            **kwargs,
        )

    def kind(self) -> BackendKind:
        return BackendKind.LLAMACPP
