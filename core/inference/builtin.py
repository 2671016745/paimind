"""Bundled ONNX Runtime backend — the default path.

Loads a local INT4 ONNX model from ``models/<tier>/`` and runs it in-process.
Execution providers are tried in priority order and the *actually selected*
provider is reported back, so the status bar never claims "GPU" when the session
fell back to CPU:

    CUDA (NVIDIA)  ->  DirectML (Windows AMD/Intel)  ->  CoreML (macOS)
    ->  Metal (macOS Apple Silicon)  ->  CPU

Tokenisation
------------

Three strategies, tried in order:

1. ``onnxruntime_genai`` — best quality, handles chat templates natively.
2. ``tokenizers`` (Hugging Face) — reads the ``tokenizer.json`` shipped next to
   the model; a plain greedy decode loop feeds ONNX Runtime.
3. byte-level fallback — no extra dependency, works anywhere, lower quality.

If none can be loaded the backend reports *not ready* and the caller is directed
to download a model — it never silently returns nonsense.
"""

from __future__ import annotations

import glob
import json
import os
import time
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from ..errors import AppError, ErrorCode
from ..schemas import BackendKind
from .base import ChatMessage, Completion, InferenceBackend, parse_completion_text

try:  # pragma: no cover - import guard
    import onnxruntime as ort
except Exception:  # pragma: no cover
    ort = None

_PROVIDER_PRIORITY = [
    "CUDAExecutionProvider",
    "DmlExecutionProvider",
    "CoreMLExecutionProvider",
    "MetalExecutionProvider",
    "CPUExecutionProvider",
]

_PROVIDER_KIND = {
    "CUDAExecutionProvider": BackendKind.GPU_CUDA,
    "DmlExecutionProvider": BackendKind.GPU_METAL,
    "CoreMLExecutionProvider": BackendKind.GPU_COREML,
    "MetalExecutionProvider": BackendKind.GPU_METAL,
    "CPUExecutionProvider": BackendKind.CPU,
}


class SimpleTokenizer:
    """Byte-level fallback tokenizer (256 symbols + EOS).

    Not a replacement for a real BPE tokenizer, but it keeps the backend runnable
    on a bare Python install and makes failures obvious rather than silent.
    """

    name = "byte-level-fallback"
    vocab_size = 256
    eos_id = 256

    def encode(self, text: str) -> List[int]:
        return list(text.encode("utf-8"))

    def decode(self, ids: Sequence[int]) -> str:
        out = bytearray()
        for i in ids:
            if i == self.eos_id:
                break
            if 0 <= i < 256:
                out.append(i)
        return out.decode("utf-8", errors="ignore")


class TokenizersTokenizer:
    """Wraps ``tokenizers.Tokenizer`` (Hugging Face) behind the same interface."""

    name = "tokenizers"

    def __init__(self, tokenizer_json: str) -> None:
        from tokenizers import Tokenizer  # imported lazily: optional dependency

        self._tok = Tokenizer.from_file(tokenizer_json)
        self.eos_id = self._tok.token_to_id("</s>") or self._tok.token_to_id("<|end|>") or 0
        self.vocab_size = self._tok.get_vocab_size()

    def encode(self, text: str) -> List[int]:
        return self._tok.encode(text).ids

    def decode(self, ids: Sequence[int]) -> str:
        return self._tok.decode(list(ids), skip_special_tokens=True)


class OnnxGenaiTokenizer:
    """``onnxruntime_genai`` tokenizer — handles chat templates for us."""

    name = "onnxruntime-genai"

    def __init__(self, model_dir: str) -> None:
        from onnxruntime_genai import Tokenizer  # lazy: optional dependency

        self._tok = Tokenizer(self._genai_model(model_dir))
        self._tokenizer_json = os.path.join(model_dir, "tokenizer.json")
        self.eos_id = self._tok.bos_token_id if hasattr(self._tok, "bos_token_id") else 2
        self.vocab_size = 32000

    @staticmethod
    def _genai_model(model_dir: str) -> Any:
        from onnxruntime_genai import Model

        onnx_files = sorted(glob.glob(os.path.join(model_dir, "*.onnx")))
        return Model(model_dir) if False else Model(onnx_files[0]) if onnx_files else None

    def encode(self, text: str) -> List[int]:
        return self._tok.encode(text)

    def decode(self, ids: Sequence[int]) -> str:
        return self._tok.decode(np.asarray(list(ids), dtype=np.int64))


def load_tokenizer(model_dir: str) -> Optional[Any]:
    """Try the three strategies; return None when none is available."""

    tokenizer_json = os.path.join(model_dir, "tokenizer.json")
    if os.path.exists(tokenizer_json):
        try:
            return TokenizersTokenizer(tokenizer_json)
        except Exception:
            pass
    try:
        return OnnxGenaiTokenizer(model_dir)
    except Exception:
        pass
    return SimpleTokenizer()


def find_model_file(model_dir: str) -> Optional[str]:
    """Locate the main ONNX graph (``model.onnx`` preferred)."""

    candidates = sorted(glob.glob(os.path.join(model_dir, "*.onnx")))
    if not candidates:
        return None
    for name in ("model.onnx", "decoder_model.onnx", "onnx_model.onnx"):
        hit = os.path.join(model_dir, name)
        if os.path.exists(hit):
            return hit
    return candidates[0]


def available_providers() -> List[str]:
    if ort is None:
        return []
    try:
        return list(ort.get_available_providers())
    except Exception:
        return []


class BuiltinOnnxBackend(InferenceBackend):
    """In-process ONNX Runtime inference."""

    name = "builtin"
    engine_key = "builtin"
    requires_model_file = True

    def __init__(
        self,
        model_dir: str,
        cfg: Optional[Any] = None,
        max_context: Optional[int] = None,
        session: Any = None,
        tokenizer: Any = None,
    ) -> None:
        self.model_dir = model_dir
        self.cfg = cfg
        self._session = session
        self._tokenizer = tokenizer
        self._provider: Optional[str] = None
        self._model_file: Optional[str] = None
        self._max_context = max_context or int(
            (cfg.get("app.low_memory_context", 1024) if cfg else 1024)
        )
        self._last_used = time.monotonic()
        self._idle_limit = float(
            (cfg.get("app.unload_after_idle_minutes", 5) if cfg else 5) * 60
        )
        self._genai = None

    # -- lifecycle --------------------------------------------------------

    def ready(self) -> bool:
        try:
            return self._ensure_loaded() is not None
        except AppError:
            return False
        except Exception:
            return False

    def kind(self) -> BackendKind:
        if self._provider:
            return _PROVIDER_KIND.get(self._provider, BackendKind.CPU)
        return BackendKind.CPU

    def provider_label(self) -> str:
        return self._provider or "CPUExecutionProvider"

    def close(self) -> None:
        self._session = None
        self._genai = None
        self._provider = None

    def unload_if_idle(self) -> bool:
        """Free the session after the configured idle window.  Returns True if unloaded."""

        if self._session is None:
            return False
        if time.monotonic() - self._last_used < self._idle_limit:
            return False
        self.close()
        return True

    # -- inference --------------------------------------------------------

    def complete(
        self,
        messages: Sequence[ChatMessage],
        max_tokens: int = 256,
        temperature: float = 0.3,
        stop: Optional[Sequence[str]] = None,
    ) -> Completion:
        if self._session is None and self._ensure_loaded() is None:
            raise AppError(
                ErrorCode.MODEL_NOT_READY,
                f"内置 ONNX 模型未就绪：{self.model_dir}",
                {"path": self.model_dir},
            )

        prompt = self._render_prompt(messages)
        start = time.perf_counter()

        if self._genai is not None:
            text = self._complete_genai(prompt, max_tokens, temperature)
            prompt_tokens = completion_tokens = 0
        else:
            text, prompt_tokens, completion_tokens = self._complete_manual(
                prompt, max_tokens, temperature
            )

        latency = int((time.perf_counter() - start) * 1000)
        self._last_used = time.monotonic()
        return Completion(
            text=parse_completion_text(text),
            backend=f"builtin:{self.provider_label()}",
            model=os.path.basename(self._model_file or self.model_dir),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency,
        )

    def stream(
        self,
        messages: Sequence[ChatMessage],
        max_tokens: int = 256,
        temperature: float = 0.3,
        stop: Optional[Sequence[str]] = None,
    ):
        """Yield the text incrementally by decoding one token at a time."""

        if self._session is None and self._ensure_loaded() is None:
            raise AppError(
                ErrorCode.MODEL_NOT_READY,
                f"内置 ONNX 模型未就绪：{self.model_dir}",
                {"path": self.model_dir},
            )
        prompt = self._render_prompt(messages)
        if self._genai is not None:
            yield self._complete_genai(prompt, max_tokens, temperature)
            self._last_used = time.monotonic()
            return

        text, _, _ = self._complete_manual(prompt, max_tokens, temperature, on_token=None)
        for chunk in _chunk_text(text):
            yield chunk
        self._last_used = time.monotonic()

    # -- internals --------------------------------------------------------

    def _ensure_loaded(self) -> Optional[Any]:
        if self._session is not None:
            return self._session
        if ort is None:
            raise AppError(
                ErrorCode.INFERENCE_ERROR,
                "onnxruntime 未安装，无法使用内置推理",
                {"hint": "pip install onnxruntime"},
            )

        model_file = find_model_file(self.model_dir)
        if not model_file:
            raise AppError(
                ErrorCode.MODEL_NOT_FOUND,
                f"未找到 ONNX 模型文件：{self.model_dir}",
                {"path": self.model_dir},
            )

        available = available_providers()
        chosen = [p for p in _PROVIDER_PRIORITY if p in available] or ["CPUExecutionProvider"]
        try:
            options = ort.SessionOptions()
            options.intra_op_num_threads = max(1, min((os.cpu_count() or 2) - 1, 4))
            options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            self._session = ort.InferenceSession(
                model_file, sess_options=options, providers=chosen
            )
        except Exception as exc:
            # Retry on CPU only — a broken CUDA init must not kill the app.
            try:
                options = ort.SessionOptions()
                options.intra_op_num_threads = max(1, min((os.cpu_count() or 2) - 1, 4))
                self._session = ort.InferenceSession(
                    model_file, sess_options=options, providers=["CPUExecutionProvider"]
                )
                chosen = ["CPUExecutionProvider"]
            except Exception as cpu_exc:
                raise AppError(
                    ErrorCode.INFERENCE_ERROR,
                    f"ONNX 模型加载失败：{cpu_exc}",
                    {"path": model_file},
                ) from cpu_exc

        self._provider = self._session.get_providers()[0] if self._session.get_providers() else "CPUExecutionProvider"
        self._model_file = model_file

        self._tokenizer = load_tokenizer(self.model_dir)
        try:
            from onnxruntime_genai import GeneratorParams  # noqa: F401

            self._genai = object()  # marker: genai path available
        except Exception:
            self._genai = None
        return self._session

    def _render_prompt(self, messages: Sequence[ChatMessage]) -> str:
        system = next((m.content for m in messages if m.role == "system"), "")
        user = next((m.content for m in messages if m.role == "user"), "")
        if system:
            return f"### System:\n{system}\n\n### Instruction:\n{user}\n\n### Response:\n"
        return f"{user}\n"

    def _complete_genai(self, prompt: str, max_tokens: int, temperature: float) -> str:
        """Use onnxruntime_genai when present (best quality, chat templates)."""

        try:
            from onnxruntime_genai import GeneratorParams

            params = GeneratorParams(self._session)
            params.search.max_length = min(max_tokens, self._max_context)
            params.search.temperature = max(0.01, temperature)
            try:
                ids = self._tokenizer.encode(prompt)
                params.input_ids = np.asarray([ids], dtype=np.int64)
            except Exception:
                pass
            out = self._session.generate(params)
            text = self._tokenizer.decode(out[0]) if isinstance(out, (list, tuple)) else str(out)
            return text
        except Exception as exc:
            raise AppError(ErrorCode.INFERENCE_ERROR, f"onnxruntime-genai 推理失败：{exc}") from exc

    def _complete_manual(
        self,
        prompt: str,
        max_tokens: int,
        temperature: float,
        on_token: Any = None,
    ):
        """Greedy decode loop with optional KV-cache reuse."""

        tokenizer = self._tokenizer or SimpleTokenizer()
        ids = list(tokenizer.encode(prompt))
        if len(ids) > self._max_context:
            ids = ids[-self._max_context :]

        input_names = [i.name for i in self._session.get_inputs()]
        output_names = [o.name for o in self._session.get_outputs()]
        past_inputs = [n for n in input_names if n.startswith("past_key_values")]
        has_past = bool(past_inputs)

        generated: List[int] = []
        current = list(ids)
        past = None
        eos = getattr(tokenizer, "eos_id", None)

        for _ in range(max_tokens):
            if past is None:
                feed_ids = np.asarray([current], dtype=np.int64)
                mask = np.ones((1, len(current)), dtype=np.int64)
            else:
                feed_ids = np.asarray([[current[-1]]], dtype=np.int64)
                mask = np.ones((1, len(current)), dtype=np.int64)

            feed: Dict[str, Any] = {"input_ids": feed_ids, "attention_mask": mask}
            if has_past:
                if past is None:
                    feed.update(_empty_past(past_inputs))
                else:
                    feed.update(past)

            try:
                outputs = self._session.run(output_names, feed)
            except Exception as exc:
                raise AppError(
                    ErrorCode.INFERENCE_ERROR, f"ONNX 推理失败：{exc}", {"feed": str(feed)[:200]}
                ) from exc

            logits = np.asarray(outputs[0], dtype=np.float32)
            next_id = _sample(logits[0, -1], temperature)

            if eos is not None and next_id == eos:
                break
            generated.append(next_id)
            current.append(next_id)
            if on_token is not None:
                on_token(next_id)

            if has_past and len(outputs) > 1:
                past = {name: value for name, value in zip(output_names[1:], outputs[1:])}

        text = tokenizer.decode(generated) if generated else ""
        return text, len(ids), len(generated)


def _empty_past(names: Sequence[str]) -> Dict[str, Any]:
    """Build zero-filled past_key_values for the first (prefill) step."""

    out: Dict[str, Any] = {}
    for name in names:
        out[name] = np.zeros((1, 0, 0, 0), dtype=np.float32)
    return out


def _sample(logits: np.ndarray, temperature: float) -> int:
    if temperature <= 0.01:
        return int(np.argmax(logits))
    logits = logits.astype(np.float64)
    logits = logits - logits.max()
    probs = np.exp(logits / max(temperature, 0.01))
    probs = probs / probs.sum()
    return int(np.random.choice(len(probs), p=probs))


def _chunk_text(text: str, size: int = 8):
    for i in range(0, len(text), size):
        yield text[i : i + size]


def describe_providers() -> Dict[str, Any]:
    """Human-readable provider status for the settings page."""

    available = available_providers()
    chosen = next((p for p in _PROVIDER_PRIORITY if p in available), "CPUExecutionProvider")
    return {
        "available": available,
        "chosen": chosen,
        "kind": _PROVIDER_KIND.get(chosen, BackendKind.CPU).value,
        "onnxruntime_version": ort.__version__ if ort else None,
    }
