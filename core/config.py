"""Configuration loading and defaults.

The app reads ``config.yaml`` at the project root (override with the
``PAIMIND_CONFIG`` environment variable).  Anything missing falls back to the
built-in defaults declared here, so a fresh checkout runs with zero setup.

Secrets are never stored here — only *references* to the system keyring, see
:mod:`core.keys`.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .errors import AppError, ErrorCode

APP_NAME = "paimind"
DISPLAY_NAME = "应急食品"
APP_VERSION = "0.0.1"


def project_root() -> Path:
    """Directory containing ``config.yaml`` — the repo root when running from source."""

    env = os.environ.get("PAIMIND_ROOT")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent


def default_config_path() -> Path:
    env = os.environ.get("PAIMIND_CONFIG")
    if env:
        return Path(env)
    return project_root() / "config.yaml"


# --------------------------------------------------------------------------
# Built-in defaults
# --------------------------------------------------------------------------

DEFAULT_TIERS: Dict[str, Dict[str, Any]] = {
    "L0": {
        "params": "0.5B",
        "disk_mb": 300,
        "peak_memory_gb": 1.2,
        "display_name": "L0 超小档",
        "suggestion": "老手机、低配本、纯 CPU",
        "min_available_memory_gb": 1.5,
        "min_disk_mb": 500,
        "cpu_x86_avx2": False,
        "gpu_vram_gb": 0.0,
        "gpu_optional": True,
    },
    "L1": {
        "params": "1.5B",
        "disk_mb": 1000,
        "peak_memory_gb": 2.8,
        "display_name": "L1 基础档",
        "suggestion": "主流手机、普通笔记本",
        "min_available_memory_gb": 3.0,
        "min_disk_mb": 1500,
        "cpu_x86_avx2": True,
        "gpu_vram_gb": 0.0,
        "gpu_optional": True,
    },
    "L2": {
        "params": "3B",
        "disk_mb": 2000,
        "peak_memory_gb": 5.0,
        "display_name": "L2 均衡档",
        "suggestion": "中端台式 / 核显本",
        "min_available_memory_gb": 6.0,
        "min_disk_mb": 3000,
        "cpu_x86_avx2": True,
        "gpu_vram_gb": 0.0,
        "gpu_optional": True,
    },
    "L3": {
        "params": "7B",
        "disk_mb": 4500,
        "peak_memory_gb": 10.0,
        "display_name": "L3 大档",
        "suggestion": "独显台式",
        "min_available_memory_gb": 12.0,
        "min_disk_mb": 6000,
        "cpu_x86_avx2": True,
        "gpu_vram_gb": 6.0,
        "gpu_optional": True,
    },
}

DEFAULT_MIRRORS: List[Dict[str, Any]] = [
    {
        "id": "official", "name": "GitHub 官方", "provider": "github",
        "url_template": "https://github.com/{repo}/releases/download/{tag}/{file}",
        "auth_required": False, "enabled": True, "priority": 100, "kind": "builtin",
    },
    {
        "id": "ghproxy", "name": "ghproxy 镜像", "provider": "github",
        "url_template": "https://ghproxy.com/https://github.com/{repo}/releases/download/{tag}/{file}",
        "auth_required": False, "enabled": True, "priority": 200, "kind": "builtin",
    },
    {
        "id": "ghfast", "name": "ghfast 镜像", "provider": "github",
        "url_template": "https://ghfast.top/https://github.com/{repo}/releases/download/{tag}/{file}",
        "auth_required": False, "enabled": True, "priority": 300, "kind": "builtin",
    },
    {
        "id": "official", "name": "Hugging Face 官方", "provider": "huggingface",
        "url_template": "https://huggingface.co/{repo}/resolve/{revision}/{path}",
        "auth_required": False, "enabled": True, "priority": 100, "kind": "builtin",
    },
    {
        "id": "hf-mirror", "name": "HF Mirror 镜像", "provider": "huggingface",
        "url_template": "https://hf-mirror.com/{repo}/resolve/{revision}/{path}",
        "auth_required": False, "enabled": True, "priority": 200, "kind": "builtin",
    },
]

TEMPLATE_VARIABLES: Dict[str, str] = {
    "{repo}": "owner/name",
    "{tag}": "GitHub Release tag",
    "{revision}": "HF 分支或 tag",
    "{file}": "文件名",
    "{path}": "仓库相对路径（HF 必须）",
    "{branch}": "分支名",
}

DEFAULTS: Dict[str, Any] = {
    "app": {
        "name": APP_NAME,
        "display_name": DISPLAY_NAME,
        "version": APP_VERSION,
        "log_level": "INFO",
        "models_dir": "models",
        "data_dir": "data",
        "inference_threads": "auto",
        "unload_after_idle_minutes": 5,
        "low_memory_context": 1024,
    },
    "tiers": {
        "apple_silicon_memory_factor": 0.85,
        "allow_force_load": False,
        "disk_headroom_percent": 30,
        **DEFAULT_TIERS,
    },
    "decision": {
        "confidence_threshold": 0.6,
        "max_reason_length": 20,
        "language": "zh",
        "allow_online": True,
        "upload_options": True,
        "online_keywords": [
            "今天", "最新", "现在", "价格", "新闻", "汇率", "版本", "发布",
            "今天", "本周", "当前", "实时", "行情", "现货",
            "today", "latest", "now", "price", "news", "exchange rate",
            "version", "release", "current", "quote",
        ],
    },
    "online": {
        "timeout_seconds": 5,
        "cache_ttl_hours": 24,
        "max_results": 3,
        "search": {
            "provider": "auto",
            "serpapi_key_ref": "serpapi_key",
            "bing_key_ref": "bing_key",
            "brave_key_ref": "brave_key",
            "duckduckgo_url": "https://html.duckduckgo.com/html/",
        },
    },
    "download": {
        "default_provider": "github",
        "default_mirror_by_provider": {"github": "official", "huggingface": "official"},
        "fallback_order": {
            "github": ["official", "ghproxy", "ghfast"],
            "huggingface": ["official", "hf-mirror"],
        },
        "timeout_seconds": 30,
        "retry_per_source": 1,
        "verify_sha256": True,
        "resume_support": True,
        "allow_http_mirrors": False,
        "follow_redirects": 5,
        "providers": {
            "github": {
                "enabled": True, "repo": f"{APP_NAME}/models", "tag": f"v{APP_VERSION}",
                "mirrors": ["official", "ghproxy", "ghfast"],
            },
            "huggingface": {
                "enabled": True, "repo": f"{APP_NAME}/models", "revision": "main",
                "private": False, "mirrors": ["official", "hf-mirror"],
                "auth_token_ref": "hf_token",
            },
        },
        "custom_mirrors": [],
    },
    "engines": {
        "ollama": {
            "enabled": True, "base_url": "http://localhost:11434",
            "probe_timeout_seconds": 2,
            "request_timeout_seconds": 120,
            "api_key": None,
            "openai_base_url": "http://localhost:11434/v1",
            "model_map": {
                "L0": "qwen2.5:0.5b", "L1": "qwen2.5:1.5b",
                "L2": "qwen2.5:3b", "L3": "qwen2.5:7b",
            },
            "modelfile_params": {"temperature": 0.6, "num_ctx": 4096},
        },
        "vllm": {
            "enabled": True, "base_url": "http://localhost:8000",
            "probe_timeout_seconds": 2,
            "request_timeout_seconds": 120,
            "api_key": None,
            "openai_base_url": "http://localhost:8000/v1",
            "model_map": {
                "L0": "Qwen/Qwen2.5-0.5B-Instruct-AWQ",
                "L1": "Qwen/Qwen2.5-1.5B-Instruct-AWQ",
                "L2": "Qwen/Qwen2.5-3B-Instruct-AWQ",
                "L3": "Qwen/Qwen2.5-7B-Instruct-AWQ",
            },
            "requires_nvidia_gpu": True,
            "default_max_model_len": 4096,
        },
        "llamacpp": {
            "enabled": True, "base_url": "http://localhost:8080",
            "probe_timeout_seconds": 2,
            "request_timeout_seconds": 120,
            "api_key": None,
            "openai_base_url": "http://localhost:8080/v1",
            "model_map": {
                "L0": "qwen2.5-0.5b-instruct-q4_k_m.gguf",
                "L1": "qwen2.5-1.5b-instruct-q4_k_m.gguf",
                "L2": "qwen2.5-3b-instruct-q4_k_m.gguf",
                "L3": "qwen2.5-7b-instruct-q4_k_m.gguf",
            },
            "default_context": 2048,
        },
        "docker": {
            "enabled": True,
            "socket": "unix:///var/run/docker.sock",
            "windows_named_pipe": r"\\.\pipe\docker_engine",
            "timeout_seconds": 3,
            "containers": {
                "ollama": "ollama",
                "vllm": "vllm-openai",
            },
        },
    },
    "security": {
        "hf_token_ref": "hf_token",
        "api_token_ref": "api_token",
        "hf_mirror_send_token": False,
        "keyring_service": APP_NAME,
    },
    "api": {
        "host": "127.0.0.1",
        "port": 8686,
        "cors_origins": [
            "http://127.0.0.1:8686", "http://localhost:8686",
            "http://127.0.0.1:5180", "http://localhost:5180",
        ],
        "rate_limit_per_minute": 60,
        "enable_auth": False,
    },
    "logging": {
        "redact_patterns": ["hf_[A-Za-z0-9]{20,}", "Bearer\\s+[A-Za-z0-9._-]{20,}"],
        "log_downloads": True,
        "log_uploads": True,
    },
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursive merge where *override* wins; lists are replaced, not appended."""

    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


class Config:
    """Loaded, merged and validated application configuration."""

    def __init__(self, data: Dict[str, Any], path: Optional[Path] = None) -> None:
        self._data = data
        self.path = path
        self.models_dir = self._resolve_models_dir()

    # -- construction -----------------------------------------------------

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Config":
        path = path or default_config_path()
        raw: Dict[str, Any] = {}
        if path.exists():
            try:
                loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as exc:
                raise AppError(
                    ErrorCode.CONFIG_ERROR,
                    f"配置文件解析失败：{path}：{exc}",
                    {"path": str(path)},
                ) from exc
            if not isinstance(loaded, dict):
                raise AppError(
                    ErrorCode.CONFIG_ERROR,
                    f"配置文件根节点必须是映射：{path}",
                    {"path": str(path)},
                )
            raw = loaded
        merged = _deep_merge(DEFAULTS, raw)
        return cls(merged, path)

    @classmethod
    def with_overrides(cls, overrides: Dict[str, Any]) -> "Config":
        """Build a config from defaults plus *overrides* (used by tests)."""

        return cls(_deep_merge(DEFAULTS, overrides), None)

    # -- accessors --------------------------------------------------------

    def section(self, name: str) -> Dict[str, Any]:
        value = self._data.get(name, {})
        return value if isinstance(value, dict) else {}

    def get(self, dotted: str, default: Any = None) -> Any:
        """Fetch ``a.b.c`` from the merged config."""

        node: Any = self._data
        for part in dotted.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node

    def set(self, dotted: str, value: Any) -> None:
        """Set ``a.b.c`` in the in-memory config (does not persist)."""

        parts = dotted.split(".")
        node = self._data
        for part in parts[:-1]:
            nxt = node.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                node[part] = nxt
            node = nxt
        node[parts[-1]] = value

    def as_dict(self) -> Dict[str, Any]:
        return copy.deepcopy(self._data)

    def to_public_dict(self) -> Dict[str, Any]:
        """Config safe to ship to a client: secret *references* only, never values."""

        data = self.as_dict()
        data.pop("security", None)
        return data

    # -- helpers ----------------------------------------------------------

    def _resolve_models_dir(self) -> Path:
        raw = self.get("app.models_dir", "models")
        p = Path(raw)
        if not p.is_absolute():
            p = project_root() / p
        return p

    def tier(self, tier_id: str) -> Dict[str, Any]:
        tiers = self.section("tiers")
        if tier_id not in tiers:
            raise AppError(ErrorCode.MODEL_NOT_FOUND, f"未知档位：{tier_id}", {"tier": tier_id})
        return dict(tiers[tier_id])

    def tier_ids(self) -> List[str]:
        return [t for t in ("L0", "L1", "L2", "L3") if t in self.section("tiers")]

    def engine(self, name: str) -> Dict[str, Any]:
        engines = self.section("engines")
        if name not in engines:
            raise AppError(ErrorCode.ENGINE_UNSUPPORTED, f"未知引擎：{name}", {"engine": name})
        return dict(engines[name])

    def deploy_modes(self) -> List[str]:
        return ["builtin", "ollama", "vllm", "llamacpp", "docker"]

    def save(self, path: Optional[Path] = None) -> Path:
        """Persist the current config.  Secret values are never written."""

        target = path or self.path or default_config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = self.as_dict()
        payload.pop("security", None)
        target.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return target


_config: Optional[Config] = None


def get_config() -> Config:
    """Process-wide singleton."""

    global _config
    if _config is None:
        _config = Config.load()
    return _config


def reset_config_cache() -> None:
    """Drop the singleton (tests / config reload)."""

    global _config
    _config = None


def render_url_template(template: str, variables: Dict[str, str]) -> str:
    """Expand a download URL template, leaving unknown placeholders intact."""

    out = template
    for key, value in variables.items():
        out = out.replace(key, str(value))
    return out


def validate_url_template(template: str) -> List[str]:
    """Return a list of problems with *template* (empty list means valid)."""

    problems: List[str] = []
    if not template:
        problems.append("URL 模板不能为空")
        return problems
    if not template.startswith("https://"):
        problems.append("URL 模板必须使用 https（http 需显式开启 allow_http_mirrors）")
    for var in ("{repo}",):
        if var not in template:
            problems.append(f"缺少必要变量 {var}")
    return problems
