"""Pydantic v2 data models shared by core, server, desktop and mobile.

These models are the *contract*: the HTTP API validates requests against them and
serialises responses from them, the desktop UI reads the same shapes, and the
Flutter/PWA clients mirror them.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------


class DecisionMode(str, Enum):
    """How the decision pipeline may use the network."""

    AUTO = "auto"      # local first, online only when needed
    LOCAL = "local"    # never touch the network
    ONLINE = "online"  # always search, then decide with evidence


class Language(str, Enum):
    ZH = "zh"
    EN = "en"


class TierId(str, Enum):
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


class DeployMode(str, Enum):
    BUILTIN = "builtin"
    OLLAMA = "ollama"
    VLLM = "vllm"
    LLAMACPP = "llamacpp"
    DOCKER = "docker"


class TierState(str, Enum):
    """Tri-state availability of a tier under a deploy mode."""

    READY = "ready"
    WARNING = "warning"
    DISABLED = "disabled"


class SourceKind(str, Enum):
    LOCAL = "local"
    ONLINE = "online"
    HYBRID = "hybrid"


class BackendKind(str, Enum):
    """The *actual* runtime used, which may differ from the requested mode."""

    GPU_CUDA = "gpu:cuda"
    GPU_METAL = "gpu:metal"
    GPU_COREML = "gpu:coreml"
    CPU = "cpu"
    OLLAMA = "ollama"
    VLLM = "vllm"
    LLAMACPP = "llamacpp"


class EngineKind(str, Enum):
    OLLAMA = "ollama"
    VLLM = "vllm"
    LLAMACPP = "llamacpp"
    DOCKER = "docker"


class EngineState(str, Enum):
    READY = "ready"
    UNREACHABLE = "unreachable"
    MODEL_MISSING = "model_missing"
    NOT_INSTALLED = "not_installed"


class DownloadState(str, Enum):
    DOWNLOADED = "downloaded"
    NOT_DOWNLOADED = "not_downloaded"
    READY = "ready"
    WARNING = "warning"
    DISABLED = "disabled"


class MirrorKind(str, Enum):
    BUILTIN = "builtin"
    CUSTOM = "custom"


# --------------------------------------------------------------------------
# Request / response
# --------------------------------------------------------------------------


class Option(BaseModel):
    """One selectable answer.  ``id`` is stable, ``text`` is what the user sees."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, max_length=64, description="选项稳定标识")
    text: str = Field(..., min_length=1, max_length=200, description="选项显示文本")

    @field_validator("id")
    @classmethod
    def _strip_id(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("选项 id 不能为空")
        return v


class DecideRequest(BaseModel):
    """Body of ``POST /v1/decide`` and ``POST /v1/decide/stream``."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(..., min_length=1, max_length=2000, description="问题（必填）")
    options: List[Option] = Field(..., min_length=2, max_length=20, description="选项列表（2–20 项）")
    context: Optional[str] = Field(None, max_length=2000, description="可选背景信息")
    mode: DecisionMode = DecisionMode.AUTO
    model_tier: TierId | str = "auto"
    language: Language = Language.ZH
    stream: bool = False
    confidence_threshold: float = Field(0.6, ge=0.0, le=1.0)
    max_reason_length: int = Field(20, ge=1, le=80)
    allow_online: bool = True
    upload_options: bool = True
    request_id: Optional[str] = Field(None, max_length=128)

    @field_validator("question")
    @classmethod
    def _question_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("问题不能为空")
        return v.strip()

    @field_validator("model_tier")
    @classmethod
    def _normalise_tier(cls, v: str) -> str:
        v = str(v).strip().upper()
        if v == "AUTO":
            return "auto"
        if v not in {t.value for t in TierId}:
            raise ValueError(f"未知档位: {v}")
        return v

    @model_validator(mode="after")
    def _option_ids_unique(self) -> "DecideRequest":
        ids = [o.id for o in self.options]
        if len(set(ids)) != len(ids):
            raise ValueError("选项 id 不能重复")
        return self


class Selected(BaseModel):
    id: str
    text: str
    confidence: float = Field(..., ge=0.0, le=1.0)


class Alternative(BaseModel):
    id: str
    text: str
    score: float = Field(..., ge=0.0, le=1.0)


class SourceRef(BaseModel):
    title: str
    url: str
    domain: str


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class DecisionResponse(BaseModel):
    """Canonical decision payload returned by every entry point."""

    model_config = ConfigDict(extra="forbid")

    id: str
    object: str = "decision"
    created: int
    request_id: Optional[str] = None
    model: str
    tier: str
    backend: str
    source: SourceKind
    selected: Selected
    reason: str = Field(..., max_length=200)
    alternatives: List[Alternative] = Field(default_factory=list)
    sources: List[SourceRef] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    latency_ms: int = 0


# --------------------------------------------------------------------------
# Hardware / tiers
# --------------------------------------------------------------------------


class MemoryInfo(BaseModel):
    total_bytes: int
    available_bytes: int
    total_gb: float
    available_gb: float


class CpuInfo(BaseModel):
    architecture: str
    cores_physical: int
    cores_logical: int
    flags: List[str] = Field(default_factory=list)
    has_avx2: bool = False


class GpuInfo(BaseModel):
    name: str = ""
    vram_bytes: int = 0
    vram_gb: float = 0.0
    vendor: str = ""          # nvidia | amd | intel | apple
    driver_ok: bool = False
    compute_backend: str = ""  # cuda | metal | coreml | rocm | vulkan | directml | opencl | none
    source: str = ""          # how it was detected


class DiskInfo(BaseModel):
    path: str
    total_bytes: int
    free_bytes: int
    free_gb: float


class HardwareInfo(BaseModel):
    platform: str            # windows | darwin | linux | android | ios
    platform_release: str
    hostname: str
    memory: MemoryInfo
    cpu: CpuInfo
    disk: DiskInfo
    gpus: List[GpuInfo] = Field(default_factory=list)
    apple_silicon: bool = False
    memory_factor_applied: float = 1.0
    detected_at: str = ""


class TierRequirements(BaseModel):
    min_available_memory_gb: float
    min_disk_mb: float
    cpu_x86_avx2: bool = False
    gpu_vram_gb: float = 0.0
    gpu_optional: bool = True


class TierInfo(BaseModel):
    tier: TierId
    params: str
    disk_mb: float
    peak_memory_gb: float
    display_name: str
    suggestion: str
    requirements: TierRequirements
    downloaded: bool = False
    files_present: List[str] = Field(default_factory=list)


class TierStatus(BaseModel):
    """A tier evaluated under one deploy mode — the card the UI renders."""

    model_config = ConfigDict(extra="forbid")

    tier: TierId
    deploy_mode: DeployMode
    state: TierState
    downloaded: bool = False
    unavailable_reason: Optional[str] = None
    warning_reason: Optional[str] = None
    requirements: Optional[TierRequirements] = None
    backend_hint: Optional[str] = None
    engine_state: Optional[EngineState] = None
    engine_model_ready: Optional[bool] = None
    engine_model: Optional[str] = None
    recommended: bool = False


# --------------------------------------------------------------------------
# Engines
# --------------------------------------------------------------------------


class EngineStatus(BaseModel):
    engine: EngineKind
    state: EngineState
    base_url: str
    enabled: bool
    reachable: bool = False
    latency_ms: Optional[int] = None
    models: List[str] = Field(default_factory=list)
    message: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------
# Download sources
# --------------------------------------------------------------------------


class Mirror(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    url_template: str
    auth_required: bool = False
    enabled: bool = True
    priority: int = 100
    kind: MirrorKind = MirrorKind.BUILTIN
    provider: str = ""            # github | huggingface | custom
    last_test_ok: Optional[bool] = None
    last_test_message: Optional[str] = None


class ProviderConfig(BaseModel):
    provider: str                 # github | huggingface
    enabled: bool = True
    repo: str = ""
    tag: str = ""                 # github release tag
    revision: str = ""            # hf branch / tag
    private: bool = False
    auth_token_ref: Optional[str] = None
    mirrors: List[str] = Field(default_factory=list)
    files: List[str] = Field(default_factory=list)


class DownloadSourceGroup(BaseModel):
    provider: str
    label: str
    mirrors: List[Mirror] = Field(default_factory=list)


class DownloadSourcesPayload(BaseModel):
    default_provider: str = "github"
    default_mirror_by_provider: Dict[str, str] = Field(default_factory=dict)
    fallback_order: Dict[str, List[str]] = Field(default_factory=dict)
    groups: List[DownloadSourceGroup] = Field(default_factory=list)
    custom_mirrors: List[Mirror] = Field(default_factory=list)


class MirrorTestResult(BaseModel):
    provider: str
    mirror_id: str
    ok: bool
    url: str
    status_code: Optional[int] = None
    latency_ms: int = 0
    message: str = ""
    content_length: Optional[int] = None
    sha256_prefix: Optional[str] = None
    used_token: bool = False


class DownloadAttempt(BaseModel):
    provider: str
    mirror_id: str
    url: str
    ok: bool
    error: Optional[str] = None
    http_status: Optional[int] = None
    bytes_written: int = 0
    elapsed_ms: int = 0
    used_token: bool = False


class DownloadTask(BaseModel):
    task_id: str
    tier: TierId
    provider: str
    mirror_id: str
    state: str = "pending"        # pending | running | done | failed | cancelled
    progress: float = 0.0
    downloaded_bytes: int = 0
    total_bytes: int = 0
    speed_bps: float = 0.0
    eta_seconds: Optional[float] = None
    url: str = ""
    attempts: List[DownloadAttempt] = Field(default_factory=list)
    error: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""


# --------------------------------------------------------------------------
# Hugging Face
# --------------------------------------------------------------------------


class HFAuthStatus(BaseModel):
    logged_in: bool = False
    username: Optional[str] = None
    full_name: Optional[str] = None
    avatar_url: Optional[str] = None
    token_stored: bool = False
    token_valid: bool = False
    token_write_scope: Optional[bool] = None
    message: Optional[str] = None


class HFRepo(BaseModel):
    id: str
    private: bool = False
    downloads: int = 0
    likes: int = 0
    last_modified: Optional[str] = None
    files: List[str] = Field(default_factory=list)


class HFRepoCreateInfo(BaseModel):
    name: str = Field(..., min_length=1, max_length=96)
    private: bool = False
    create_readme: bool = True

    @field_validator("name")
    @classmethod
    def _valid_repo_name(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", v):
            raise ValueError("仓库名只能包含字母、数字、- _ .，且以字母或数字开头，长度 ≤ 96")
        return v


class UploadFileResult(BaseModel):
    path: str
    size: int = 0
    ok: bool = False
    skipped: bool = False
    sha256: Optional[str] = None
    error: Optional[str] = None
    elapsed_ms: int = 0


class UploadTask(BaseModel):
    task_id: str
    repo_id: str
    state: str = "pending"        # pending | running | done | failed | cancelled
    total_bytes: int = 0
    uploaded_bytes: int = 0
    progress: float = 0.0
    skipped_count: int = 0
    files: List[UploadFileResult] = Field(default_factory=list)
    repo_url: Optional[str] = None
    error: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""


# --------------------------------------------------------------------------
# Config / usage
# --------------------------------------------------------------------------


class ConfigPayload(BaseModel):
    """Everything a client may read or write through ``/v1/config``."""

    model_config = ConfigDict(extra="allow")

    app: Dict[str, Any] = Field(default_factory=dict)
    tiers: Dict[str, Any] = Field(default_factory=dict)
    decision: Dict[str, Any] = Field(default_factory=dict)
    online: Dict[str, Any] = Field(default_factory=dict)
    download: Dict[str, Any] = Field(default_factory=dict)
    engines: Dict[str, Any] = Field(default_factory=dict)
    security: Dict[str, Any] = Field(default_factory=dict)
    api: Dict[str, Any] = Field(default_factory=dict)


class UsageEntry(BaseModel):
    ts: str
    tier: str
    backend: str
    source: str
    latency_ms: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached: bool = False


class UsageSummary(BaseModel):
    total_requests: int = 0
    by_backend: Dict[str, int] = Field(default_factory=dict)
    by_tier: Dict[str, int] = Field(default_factory=dict)
    by_source: Dict[str, int] = Field(default_factory=dict)
    avg_latency_ms: float = 0.0
    total_tokens: int = 0
    recent: List[UsageEntry] = Field(default_factory=list)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def utc_now_epoch() -> int:
    return int(datetime.now(timezone.utc).timestamp())
