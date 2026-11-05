"""Hardware detection.

Collects the five signals the tier rules need:

1. available physical memory  (never total — a machine "having" 16 GB tells us
   nothing if 14 GB is already committed)
2. total memory
3. CPU architecture / cores / instruction set (AVX2 on x86)
4. free disk space on the volume that will hold the models
5. GPU model, VRAM and whether a usable compute driver is present

Every probe is wrapped in ``try/except`` with a timeout so a missing tool, a
missing permission or an exotic platform degrades to a conservative "unknown"
instead of blocking startup.

Design notes / 设计要点
-----------------------

* **Available memory, not total.**  The tier thresholds are expressed against
  *available* memory because that is what actually decides whether a model can
  be resident.  ``GlobalMemoryStatusEx.ullAvailPhys`` on Windows,
  ``vm_stat`` free+inactive+purgeable on macOS, ``/proc/meminfo`` MemAvailable
  on Linux, ``ActivityManager.MemoryInfo.availMem`` on Android and
  ``os_proc_available_memory()`` on iOS.
* **Apple Silicon unified memory is discounted** by
  ``tiers.apple_silicon_memory_factor`` (default 0.85) because the GPU shares
  the same pool, so a nominal 8 GB is really ~6.8 GB for a CPU-only model.
* **GPU detection never blocks**: each probe has a 2 s timeout and any failure
  falls back to CPU.
"""

from __future__ import annotations

import ctypes
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

from .config import get_config
from .schemas import CpuInfo, DiskInfo, GpuInfo, HardwareInfo, MemoryInfo

PROBE_TIMEOUT_SECONDS = 2.0

# Windows PF_* constants
PF_AVX2_AVAILABLE = 40
PF_XMMI64_INSTRUCTIONS_AVAILABLE = 10  # SSE2

_NVIDIA_RE = re.compile(r"^\s*([^,]+?)\s*,\s*(\d+)\s*MiB\s*$")


# --------------------------------------------------------------------------
# Memory
# --------------------------------------------------------------------------


def _memory_windows() -> Tuple[int, int]:
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_uint32),
            ("dwMemoryLoad", ctypes.c_uint32),
            ("ullTotalPhys", ctypes.c_uint64),
            ("ullAvailPhys", ctypes.c_uint64),
            ("ullTotalPageFile", ctypes.c_uint64),
            ("ullAvailPageFile", ctypes.c_uint64),
            ("ullTotalVirtual", ctypes.c_uint64),
            ("ullAvailVirtual", ctypes.c_uint64),
            ("ullAvailExtendedVirtual", ctypes.c_uint64),
        ]

    stat = MEMORYSTATUSEX()
    stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
        raise OSError("GlobalMemoryStatusEx failed")
    return int(stat.ullTotalPhys), int(stat.ullAvailPhys)


def _memory_macos() -> Tuple[int, int]:
    """vm_stat free + inactive + purgeable, scaled by page size."""

    page_size = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
    out = subprocess.run(
        ["vm_stat"], capture_output=True, text=True, timeout=PROBE_TIMEOUT_SECONDS
    ).stdout
    values: Dict[str, int] = {}
    for line in out.splitlines():
        m = re.match(r"^(.*?):\s+([\d.]+)", line)
        if not m:
            continue
        key = m.group(1).strip().lower()
        values[key] = int(float(m.group(2)))

    free = values.get("pages free", 0)
    inactive = values.get("pages inactive", 0)
    purgeable = values.get("pages purgeable", 0)
    speculative = values.get("pages speculative", 0)
    available = (free + inactive + purgeable + speculative) * page_size

    hw = subprocess.run(
        ["sysctl", "-n", "hw.memsize"],
        capture_output=True, text=True, timeout=PROBE_TIMEOUT_SECONDS,
    ).stdout.strip()
    total = int(hw) if hw.isdigit() else available
    return total, available


def _memory_linux() -> Tuple[int, int]:
    info: Dict[str, int] = {}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r"^(\w+):\s+(\d+)\s*kB", line)
                if m:
                    info[m.group(1)] = int(m.group(2)) * 1024
    except OSError:
        pass
    total = info.get("MemTotal", 0)
    available = info.get("MemAvailable", 0)
    if not available:
        available = info.get("MemFree", 0) + info.get("Buffers", 0) + info.get("Cached", 0)
    return total, available


def _memory_android() -> Tuple[int, int]:
    """Best-effort via ``adb``-less heuristics; real values come from the Flutter side."""

    total = 0
    available = 0
    try:  # Android exposes /proc/meminfo inside the app sandbox on many builds
        info: Dict[str, int] = {}
        with open("/proc/meminfo", "r", encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r"^(\w+):\s+(\d+)\s*kB", line)
                if m:
                    info[m.group(1)] = int(m.group(2)) * 1024
        total = info.get("MemTotal", 0)
        available = info.get("MemAvailable", 0)
    except OSError:
        pass
    if not total:
        total = int(os.environ.get("PAIMIND_TOTAL_MEM", "0"))
        available = int(os.environ.get("PAIMIND_AVAIL_MEM", "0"))
    return total, available


def _memory_ios() -> Tuple[int, int]:
    """iOS: ``os_proc_available_memory()`` is not exposed to Python; Flutter supplies it."""

    total = int(os.environ.get("PAIMIND_TOTAL_MEM", "0"))
    available = int(os.environ.get("PAIMIND_AVAIL_MEM", "0"))
    if not total:
        total, available = _memory_linux()  # Darwin shares the sysctl path
    return total, available


def detect_memory() -> MemoryInfo:
    system = platform.system().lower()
    try:
        if system == "windows":
            total, available = _memory_windows()
        elif system == "darwin":
            total, available = _memory_macos()
        elif "android" in sys.platform or system == "android":
            total, available = _memory_android()
        elif system == "ios" or "ios" in sys.platform:
            total, available = _memory_ios()
        else:
            total, available = _memory_linux()
    except Exception:
        total, available = 0, 0

    factor = 1.0
    if platform.machine().lower() in ("arm64", "aarch64") and system == "darwin":
        try:
            factor = float(get_config().get("tiers.apple_silicon_memory_factor", 0.85))
        except (TypeError, ValueError):
            factor = 0.85
        available = int(available * factor)

    return MemoryInfo(
        total_bytes=total,
        available_bytes=available,
        total_gb=round(total / 1024 ** 3, 2),
        available_gb=round(available / 1024 ** 3, 2),
    )


def memory_factor_applied() -> float:
    if platform.machine().lower() in ("arm64", "aarch64") and platform.system() == "Darwin":
        try:
            return float(get_config().get("tiers.apple_silicon_memory_factor", 0.85))
        except (TypeError, ValueError):
            return 0.85
    return 1.0


# --------------------------------------------------------------------------
# CPU
# --------------------------------------------------------------------------


def _avx2_windows() -> bool:
    try:
        return bool(ctypes.windll.kernel32.IsProcessorFeaturePresent(PF_AVX2_AVAILABLE))
    except Exception:
        return False


def _avx2_linux() -> bool:
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if line.startswith("flags"):
                    return "avx2" in line
    except OSError:
        pass
    return False


def _avx2_macos() -> bool:
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        return False  # Apple Silicon: no x86 AVX2 concept, skip the check entirely
    try:
        out = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.features"],
            capture_output=True, text=True, timeout=PROBE_TIMEOUT_SECONDS,
        ).stdout.upper()
        return "AVX2" in out
    except Exception:
        return False


def detect_cpu() -> CpuInfo:
    machine = platform.machine().lower()
    is_x86 = machine in ("amd64", "x86_64", "i386", "i686", "x86")
    cores_physical = os.cpu_count() or 1
    cores_logical = os.cpu_count() or 1

    flags: List[str] = []
    has_avx2 = False
    try:
        if platform.system() == "Windows":
            has_avx2 = _avx2_windows()
        elif platform.system() == "Darwin":
            has_avx2 = _avx2_macos()
        else:
            has_avx2 = _avx2_linux()
    except Exception:
        has_avx2 = False

    if has_avx2:
        flags.append("avx2")

    # Windows: derive physical cores from the registry-free Win32 API
    if platform.system() == "Windows" and hasattr(ctypes, "windll"):
        try:
            GetMaximumProcessorCount = ctypes.windll.kernel32.GetMaximumProcessorCount
            GetMaximumProcessorCount.restype = ctypes.c_uint32
            groups = max(1, ctypes.windll.kernel32.GetActiveProcessorGroupCount())
            physical = int(GetMaximumProcessorCount(0)) * groups
            if physical > 0:
                cores_physical = physical
        except Exception:
            pass

    return CpuInfo(
        architecture=machine,
        cores_physical=cores_physical,
        cores_logical=cores_logical,
        flags=flags,
        has_avx2=has_avx2,
    )


# --------------------------------------------------------------------------
# Disk
# --------------------------------------------------------------------------


def detect_disk(path: Optional[str] = None) -> DiskInfo:
    target = path or os.path.expanduser("~")
    total, free = _usage_for(target)

    # macOS: os.statvfs on the *volume* is more truthful than shutil for APFS
    if platform.system() == "Darwin":
        try:
            st = os.statvfs(target)
            total = st.f_frsize * st.f_blocks
            free = st.f_frsize * st.f_bavail
        except Exception:
            pass

    return DiskInfo(
        path=target,
        total_bytes=total,
        free_bytes=free,
        free_gb=round(free / 1024 ** 3, 2),
    )


def _usage_for(target: str) -> Tuple[int, int]:
    """Free space of *target*, falling back to the nearest existing ancestor.

    ``models/`` may not exist yet on a fresh install; measuring a missing
    directory must not report "0 bytes free" and disable every tier.
    """

    candidate = os.path.abspath(target)
    for _ in range(8):
        try:
            usage = shutil.disk_usage(candidate)
            return usage.total, usage.free
        except Exception:
            parent = os.path.dirname(candidate)
            if parent == candidate or not parent:
                break
            candidate = parent
    return 0, 0


# --------------------------------------------------------------------------
# GPU
# --------------------------------------------------------------------------


def _run(cmd: List[str], timeout: float = PROBE_TIMEOUT_SECONDS) -> Optional[str]:
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            creationflags=_creation_flags(),
        )
        if out.returncode != 0:
            return None
        return out.stdout
    except Exception:
        return None


def _creation_flags() -> int:
    if platform.system() == "Windows":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def _detect_nvidia() -> Optional[GpuInfo]:
    out = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"])
    if not out:
        return None
    gpu: Optional[GpuInfo] = None
    for line in out.splitlines():
        m = _NVIDIA_RE.match(line.strip())
        if not m:
            continue
        name = m.group(1).strip()
        vram = int(m.group(2)) * 1024 * 1024
        gpu = GpuInfo(
            name=name,
            vram_bytes=vram,
            vram_gb=round(vram / 1024 ** 3, 2),
            vendor="nvidia",
            driver_ok=True,
            compute_backend="cuda",
            source="nvidia-smi",
        )
    return gpu


def _detect_windows_wmi() -> Optional[GpuInfo]:
    out = _run([
        "wmic", "path", "win32_VideoController", "get",
        "Name,AdapterRAM,DriverVersion", "/format:csv",
    ])
    if not out:
        return None
    best: Optional[GpuInfo] = None
    for line in out.splitlines()[1:]:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4 or not parts[-3]:
            continue
        name = parts[-3]
        try:
            vram = int(parts[-2])
        except ValueError:
            vram = 0
        driver = parts[-1]
        low = name.lower()
        vendor = "nvidia" if "nvidia" in low else "amd" if ("amd" in low or "radeon" in low) else \
                 "intel" if "intel" in low else "other"
        # AdapterRAM is clamped at 4 GB on 32-bit values; nvidia-smi is authoritative
        gpu = GpuInfo(
            name=name, vram_bytes=vram, vram_gb=round(vram / 1024 ** 3, 2),
            vendor=vendor, driver_ok=bool(driver),
            compute_backend="cuda" if vendor == "nvidia" and driver else
                             "directml" if vendor in ("amd", "intel") and driver else "none",
            source="wmi",
        )
        if best is None or gpu.vram_bytes > best.vram_bytes:
            best = gpu
    return best


def _detect_macos() -> List[GpuInfo]:
    out = _run(["system_profiler", "SPDisplaysDataType"], timeout=5.0)
    if not out:
        return []
    gpus: List[GpuInfo] = []
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        # Apple Silicon: the GPU is part of the SoC; Metal is always available
        name = "Apple Silicon GPU"
        m = re.search(r"Chipset Model:\s*(.+)", out)
        if m:
            name = m.group(1).strip()
        gpus.append(GpuInfo(
            name=name, vram_bytes=0, vram_gb=0.0, vendor="apple",
            driver_ok=True, compute_backend="metal", source="system_profiler",
        ))
        return gpus

    current: Dict[str, Any] = {}
    for line in out.splitlines():
        if re.match(r"^\s{8}\S", line) and current:
            gpus.append(_macos_gpu_from_dict(current))
            current = {}
        m = re.match(r"^\s{8,12}([A-Za-z][A-Za-z /()_-]+):\s*(.+)$", line)
        if m:
            current[m.group(1).strip()] = m.group(2).strip()
    if current:
        gpus.append(_macos_gpu_from_dict(current))
    return [g for g in gpus if g.name]


def _macos_gpu_from_dict(d: Dict[str, Any]) -> GpuInfo:
    name = str(d.get("Chipset Model", d.get("Vendor", ""))).strip()
    vram_raw = str(d.get("VRAM (Total)", d.get("VRAM", "")))
    vram = 0
    m = re.search(r"(\d+)\s*MB", vram_raw)
    if m:
        vram = int(m.group(1)) * 1024 * 1024
    low = name.lower()
    vendor = "nvidia" if "nvidia" in low else "amd" if ("amd" in low or "radeon" in low) else \
             "intel" if "intel" in low else "apple" if "apple" in low else "other"
    backend = "metal" if vendor in ("apple", "amd", "intel") else "none"
    return GpuInfo(
        name=name, vram_bytes=vram, vram_gb=round(vram / 1024 ** 3, 2),
        vendor=vendor, driver_ok=bool(name), compute_backend=backend,
        source="system_profiler",
    )


def _detect_linux() -> List[GpuInfo]:
    out = _run(["lspci", "-nn"])
    if not out:
        return []
    gpus: List[GpuInfo] = []
    for line in out.splitlines():
        low = line.lower()
        if not any(k in low for k in ("vga", "3d controller", "display controller")):
            continue
        name = line.split(":", 2)[-1].strip() if line.count(":") >= 2 else line.strip()
        vendor = "nvidia" if "nvidia" in low else "amd" if ("amd" in low or "radeon" in low) else \
                 "intel" if "intel" in low else "other"
        backend = "cuda" if vendor == "nvidia" else "vulkan" if vendor in ("amd", "intel") else "none"
        gpus.append(GpuInfo(
            name=name, vram_bytes=0, vram_gb=0.0, vendor=vendor,
            driver_ok=True, compute_backend=backend, source="lspci",
        ))
    return gpus


def detect_gpus() -> List[GpuInfo]:
    """Return detected GPUs; NVIDIA first (authoritative VRAM via nvidia-smi)."""

    gpus: List[GpuInfo] = []
    system = platform.system()

    if system == "Windows":
        nvidia = _detect_nvidia()
        if nvidia:
            gpus.append(nvidia)
        else:
            wmi = _detect_windows_wmi()
            if wmi:
                gpus.append(wmi)
    elif system == "Darwin":
        gpus = _detect_macos()
    elif system == "Linux":
        nvidia = _detect_nvidia()
        if nvidia:
            gpus.append(nvidia)
        else:
            gpus = _detect_linux()
    else:
        # Android / iOS: Vulkan / Metal are queried by the mobile client and fed in
        for raw in os.environ.get("PAIMIND_GPUS", "").split(";"):
            if not raw.strip():
                continue
            name, vram_gb, vendor, backend = (raw.split("|") + ["", "", ""])[:4]
            gpus.append(GpuInfo(
                name=name, vram_bytes=int(float(vram_gb or 0) * 1024 ** 3),
                vram_gb=float(vram_gb or 0), vendor=vendor, driver_ok=bool(backend),
                compute_backend=backend or "none", source="client",
            ))
    return gpus


def nvidia_gpu() -> Optional[GpuInfo]:
    for gpu in detect_gpus():
        if gpu.vendor == "nvidia" and gpu.driver_ok:
            return gpu
    return None


# --------------------------------------------------------------------------
# Aggregate
# --------------------------------------------------------------------------


def detect_hardware(models_dir: Optional[str] = None) -> HardwareInfo:
    memory = detect_memory()
    cpu = detect_cpu()
    disk = detect_disk(models_dir or str(get_config().models_dir))
    gpus = detect_gpus()
    return HardwareInfo(
        platform=_platform_key(),
        platform_release=platform.release(),
        hostname=socket.gethostname(),
        memory=memory,
        cpu=cpu,
        disk=disk,
        gpus=gpus,
        apple_silicon=platform.machine().lower() in ("arm64", "aarch64")
        and platform.system() == "Darwin",
        memory_factor_applied=memory_factor_applied(),
        detected_at=__import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
    )


def _platform_key() -> str:
    system = platform.system().lower()
    if system == "windows":
        return "windows"
    if system == "darwin":
        return "darwin"
    if "android" in sys.platform:
        return "android"
    if "ios" in sys.platform:
        return "ios"
    return "linux"


def hardware_snapshot(hw: Optional[HardwareInfo] = None) -> Dict[str, Any]:
    """Compact snapshot used in 422 MODEL_UNSUPPORTED details."""

    hw = hw or detect_hardware()
    return {
        "platform": hw.platform,
        "available_memory_gb": hw.memory.available_gb,
        "total_memory_gb": hw.memory.total_gb,
        "memory_factor_applied": hw.memory_factor_applied,
        "cpu_arch": hw.cpu.architecture,
        "cpu_cores": hw.cpu.cores_physical,
        "avx2": hw.cpu.has_avx2,
        "free_disk_gb": hw.disk.free_gb,
        "gpus": [
            {
                "name": g.name, "vram_gb": g.vram_gb, "vendor": g.vendor,
                "driver_ok": g.driver_ok, "compute_backend": g.compute_backend,
            }
            for g in hw.gpus
        ],
    }
