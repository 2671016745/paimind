"""Inference backend abstraction.

Every engine — the bundled ONNX Runtime, Ollama, vLLM, llama.cpp or any
OpenAI-compatible server — implements :class:`InferenceBackend`.  The decision
pipeline talks only to this interface, so:

* adding an engine means adding one file, nothing else changes;
* the same prompt, the same parameters and the same response shape are used for
  every engine (``core`` never passes engine-specific knobs such as vLLM's
  ``max-model-len`` — that keeps the contract stable across backends);
* external engines are addressed through their OpenAI-compatible
  ``/v1/chat/completions`` endpoint, which all of them expose.

为什么外部引擎统一走 OpenAI 兼容接口 / Why one OpenAI-compatible contract
--------------------------------------------------------------------

Ollama, vLLM and llama-server each have a native API, but all three also ship an
OpenAI-compatible surface.  Standardising on it means the app needs exactly one
HTTP client, one request shape and one error mapping for every external engine,
and any third-party OpenAI-compatible service (a cloud gateway, a llama.cpp fork,
a local LM Studio) plugs in for free.
"""

from __future__ import annotations

import abc
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Sequence

from ..errors import AppError, ErrorCode
from ..schemas import BackendKind


@dataclass
class ChatMessage:
    role: str          # system | user | assistant
    content: str


@dataclass
class Completion:
    """Normalised completion result — identical for every backend."""

    text: str
    backend: str
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    finish_reason: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


class InferenceBackend(abc.ABC):
    """Contract every inference engine implements."""

    name: str = "abstract"
    engine_key: str = ""            # ollama | vllm | llamacpp | builtin | openai
    requires_model_file: bool = False

    # -- lifecycle --------------------------------------------------------
    @abc.abstractmethod
    def ready(self) -> bool:
        """True when the backend can serve a request right now."""

    @abc.abstractmethod
    def kind(self) -> BackendKind:
        """The *actual* runtime in use (may be cpu even when gpu is present)."""

    @abc.abstractmethod
    def complete(
        self,
        messages: Sequence[ChatMessage],
        max_tokens: int = 256,
        temperature: float = 0.3,
        stop: Optional[Sequence[str]] = None,
    ) -> Completion:
        """Run a non-streaming completion."""

    def stream(
        self,
        messages: Sequence[ChatMessage],
        max_tokens: int = 256,
        temperature: float = 0.3,
        stop: Optional[Sequence[str]] = None,
    ) -> Iterator[str]:
        """Yield text deltas.  Defaults to buffering the whole completion."""

        yield self.complete(messages, max_tokens, temperature, stop).text

    def close(self) -> None:
        """Release resources (loaded sessions, threads).  Optional."""

    # -- helpers ----------------------------------------------------------
    def describe(self) -> str:
        return f"{self.name}({self.kind().value})"

    def __enter__(self) -> "InferenceBackend":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class BackendUnavailable(AppError):
    """Raised by backends that cannot serve; carries the engine and URL."""

    def __init__(self, engine: str, url: str, reason: str = "") -> None:
        super().__init__(
            ErrorCode.ENGINE_UNAVAILABLE,
            f"推理引擎 {engine} 不可用：{reason or url}",
            {"engine": engine, "url": url, "reason": reason},
        )


def build_messages(system: str, user: str) -> List[Dict[str, str]]:
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def parse_completion_text(text: str) -> str:
    """Strip common model artefacts (markdown fences, stray labels)."""

    out = text.strip()
    if out.startswith("```"):
        lines = out.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        out = "\n".join(lines).strip()
    return out


def timed(fn, *args, **kwargs):  # pragma: no cover - trivial helper
    start = time.perf_counter()
    result = fn(*args, **kwargs)
    return result, int((time.perf_counter() - start) * 1000)


def raise_inference_error(exc: Exception, backend: str) -> AppError:
    return AppError(
        ErrorCode.INFERENCE_ERROR,
        f"{backend} 推理失败：{exc}",
        {"backend": backend, "error_type": type(exc).__name__},
    )


__all__ = [
    "InferenceBackend",
    "ChatMessage",
    "Completion",
    "BackendUnavailable",
    "BackendKind",
    "build_messages",
    "parse_completion_text",
    "raise_inference_error",
    "json",
]
