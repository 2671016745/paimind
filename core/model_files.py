"""On-disk model inventory.

Knows where tier models live, whether a tier is actually downloaded, and how to
import a user-supplied GGUF/ONNX file as a custom model.

A tier counts as *downloaded* when its directory holds at least one ``.onnx``
graph — the tokenizer files alone are not enough to run.  Once a tier is
downloaded the disk-space floor is no longer enforced (the bytes are already
spent).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional

from .config import Config, get_config
from .errors import AppError, ErrorCode

TIER_DIR_NAMES = {"L0": "L0", "L1": "L1", "L2": "L2", "L3": "L3"}

# A custom model's peak memory is estimated from its file size: INT4 weights are
# ~0.5 byte/param, and a resident model additionally needs KV cache + activations.
_CUSTOM_MEMORY_MULTIPLIER = 1.6
_CUSTOM_MIN_MEMORY_GB = 1.0


def tier_dir(cfg: Config, tier_id: str) -> Path:
    return Path(cfg.models_dir) / TIER_DIR_NAMES.get(tier_id, tier_id)


def custom_models_dir(cfg: Config) -> Path:
    env = os.environ.get("PAIMIND_CUSTOM_MODELS")
    if env:
        return Path(env)
    return Path(cfg.models_dir) / "custom"


def _onnx_files(directory: Path) -> List[str]:
    if not directory.is_dir():
        return []
    return sorted(p.name for p in directory.glob("*.onnx"))


def _gguf_files(directory: Path) -> List[str]:
    if not directory.is_dir():
        return []
    return sorted(p.name for p in directory.glob("*.gguf"))


def is_downloaded(cfg: Config, tier_id: str) -> bool:
    """Whether *tier_id* can be served by anything already on disk.

    A tier is not tied to one engine: it may live as a builtin ONNX graph, a
    llama.cpp GGUF, a Hugging Face checkout, or an Ollama pull.  Checking only
    one of those made the upload wizard and the disk-space floor lie on any
    machine that uses an external engine.
    """

    if _onnx_files(tier_dir(cfg, tier_id)):
        return True

    models_dir = Path(cfg.models_dir)

    # llama.cpp: the GGUF named in the engine's model_map.
    gguf_wanted = str(cfg.engine("llamacpp").get("model_map", {}).get(tier_id, ""))
    if gguf_wanted and (models_dir / "gguf" / gguf_wanted).is_file():
        return True

    # vLLM / HF: the checkpoint named in the engine's model_map.
    hf_wanted = str(cfg.engine("vllm").get("model_map", {}).get(tier_id, ""))
    if hf_wanted:
        hf_dir = models_dir / "hf"
        if hf_dir.is_dir():
            for candidate in hf_dir.iterdir():
                if candidate.is_dir() and (
                    candidate.name == hf_wanted
                    or candidate.name.lower() == hf_wanted.lower()
                    or hf_wanted.lower() in candidate.name.lower()
                ):
                    return True

    return _ollama_has_model(cfg, tier_id)


def _ollama_has_model(cfg: Config, tier_id: str) -> bool:
    """Check the Ollama manifest tree for the tier's mapped model."""

    model_map = cfg.engine("ollama").get("model_map", {}) or {}
    wanted = str(model_map.get(tier_id, "")).strip()
    if not wanted or ":" not in wanted:
        return False
    name, _, tag = wanted.partition(":")
    manifests = Path(cfg.models_dir) / "ollama" / "manifests"
    if not manifests.is_dir():
        return False
    # registry.ollama.ai/library/<name>/<tag>, but be tolerant of other registries.
    for registry in manifests.iterdir():
        if not registry.is_dir():
            continue
        for scope in registry.iterdir():
            candidate = scope / name / tag
            if candidate.is_file():
                return True
    return False


def downloaded_files(cfg: Config, tier_id: str) -> List[str]:
    directory = tier_dir(cfg, tier_id)
    if not directory.is_dir():
        return []
    return sorted(p.name for p in directory.iterdir() if p.is_file())


def downloaded_map(cfg: Optional[Config] = None) -> Dict[str, bool]:
    cfg = cfg or get_config()
    return {t: is_downloaded(cfg, t) for t in ("L0", "L1", "L2", "L3")}


def tier_size_bytes(cfg: Config, tier_id: str) -> int:
    """Total bytes the tier occupies, wherever the engine keeps it.

    ``models/L{x}`` is only the builtin layout; external engines keep their
    weights elsewhere, so summing just that directory reports 0 for a tier the
    user has already pulled.
    """

    total = 0
    seen: set[Path] = set()

    def _add(path: Path) -> None:
        nonlocal total
        if not path.exists() or path in seen:
            return
        seen.add(path)
        if path.is_file():
            total += path.stat().st_size
        elif path.is_dir():
            for child in path.rglob("*"):
                if child.is_file():
                    total += child.stat().st_size

    _add(tier_dir(cfg, tier_id))

    models_dir = Path(cfg.models_dir)

    gguf_wanted = str(cfg.engine("llamacpp").get("model_map", {}).get(tier_id, ""))
    if gguf_wanted:
        _add(models_dir / "gguf" / gguf_wanted)

    hf_wanted = str(cfg.engine("vllm").get("model_map", {}).get(tier_id, ""))
    if hf_wanted:
        hf_dir = models_dir / "hf"
        if hf_dir.is_dir():
            for candidate in hf_dir.iterdir():
                if candidate.is_dir() and hf_wanted.lower() in candidate.name.lower():
                    _add(candidate)

    name, _, tag = str(
        cfg.engine("ollama").get("model_map", {}).get(tier_id, "")
    ).partition(":")
    if name and tag:
        manifests = models_dir / "ollama" / "manifests"
        if manifests.is_dir():
            for registry in manifests.iterdir():
                if not registry.is_dir():
                    continue
                for scope in registry.iterdir():
                    manifest = scope / name / tag
                    if manifest.is_file():
                        _add(manifest)

    return total


def checksums_path(cfg: Optional[Config] = None) -> Path:
    cfg = cfg or get_config()
    env = os.environ.get("PAIMIND_CHECKSUMS")
    if env:
        return Path(env)
    return Path(cfg.models_dir) / "checksums.json"


def load_checksums(cfg: Optional[Config] = None) -> Dict[str, Dict[str, str]]:
    """Return ``{tier: {filename: sha256}}`` from the bundled manifest."""

    path = checksums_path(cfg)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: Dict[str, Dict[str, str]] = {}
    if isinstance(data, dict):
        for tier, files in data.items():
            if isinstance(files, dict):
                out[str(tier)] = {str(k): str(v).lower() for k, v in files.items()}
    return out


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def expected_sha256(cfg: Config, tier_id: str, filename: str) -> Optional[str]:
    return load_checksums(cfg).get(tier_id, {}).get(filename)


def verify_tier_checksums(cfg: Config, tier_id: str) -> List[Dict[str, object]]:
    """Verify every file in a tier against the manifest.  Returns per-file results."""

    directory = tier_dir(cfg, tier_id)
    manifest = load_checksums(cfg).get(tier_id, {})
    out: List[Dict[str, object]] = []
    for path in sorted(directory.glob("*")) if directory.is_dir() else []:
        if not path.is_file():
            continue
        expected = manifest.get(path.name)
        actual = sha256_file(path)
        out.append({
            "file": path.name,
            "ok": (expected is None) or (actual == expected),
            "expected": expected,
            "actual": actual,
        })
    return out


# --------------------------------------------------------------------------
# Custom model import
# --------------------------------------------------------------------------


def estimate_peak_memory_bytes(file_size_bytes: int) -> int:
    """Conservative peak-memory estimate for an imported model file."""

    return int(file_size_bytes * _CUSTOM_MEMORY_MULTIPLIER) + int(_CUSTOM_MIN_MEMORY_GB * 1024 ** 3)


def estimate_peak_memory_gb(file_size_bytes: int) -> float:
    return round(estimate_peak_memory_bytes(file_size_bytes) / 1024 ** 3, 2)


def scan_custom_models(cfg: Optional[Config] = None) -> List[Dict[str, object]]:
    """List user-imported GGUF/ONNX files with a size-derived memory estimate."""

    cfg = cfg or get_config()
    directory = custom_models_dir(cfg)
    out: List[Dict[str, object]] = []
    if not directory.is_dir():
        return out
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() not in (".gguf", ".onnx"):
            continue
        size = path.stat().st_size
        out.append({
            "name": path.name,
            "path": str(path),
            "kind": "gguf" if path.suffix.lower() == ".gguf" else "onnx",
            "size_bytes": size,
            "size_mb": round(size / 1024 ** 2, 1),
            "estimated_peak_memory_gb": estimate_peak_memory_gb(size),
        })
    return out


def import_custom_model(source: Path, cfg: Optional[Config] = None) -> Dict[str, object]:
    """Copy a user-supplied model file into ``models/custom`` and describe it."""

    cfg = cfg or get_config()
    source = Path(source)
    if not source.exists():
        raise AppError(ErrorCode.MODEL_NOT_FOUND, f"找不到模型文件：{source}", {"path": str(source)})
    if source.suffix.lower() not in (".gguf", ".onnx"):
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            f"不支持的模型格式：{source.suffix}（仅支持 .gguf / .onnx）",
            {"suffix": source.suffix},
        )

    directory = custom_models_dir(cfg)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / source.name
    if target.exists() and target.stat().st_size == source.stat().st_size:
        size = target.stat().st_size
    else:
        shutil.copy2(source, target)
        size = target.stat().st_size

    return {
        "name": target.name,
        "path": str(target),
        "kind": "gguf" if target.suffix.lower() == ".gguf" else "onnx",
        "size_bytes": size,
        "size_mb": round(size / 1024 ** 2, 1),
        "estimated_peak_memory_gb": estimate_peak_memory_gb(size),
    }


def ollama_modelfile(name: str, gguf_path: Path, params: Optional[Dict[str, object]] = None) -> str:
    """Generate a Modelfile for importing a GGUF into Ollama."""

    params = params or {}
    lines = [f"FROM {gguf_path}"]
    for key, value in params.items():
        lines.append(f"PARAMETER {key} {value}")
    return "\n".join(lines) + "\n"


def llamacpp_args(gguf_path: Path, port: int = 8080, context: int = 2048) -> List[str]:
    """Build a ``llama-server`` command line for a GGUF."""

    return [
        "llama-server",
        "-m", str(gguf_path),
        "--port", str(port),
        "-c", str(context),
        "--host", "127.0.0.1",
    ]
