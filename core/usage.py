"""Usage accounting.

Every decision is recorded locally (tier, backend, source, latency, tokens) so
``GET /v1/usage`` can answer "which backend am I actually hitting and how slow is
it".  Records live in ``data/usage.jsonl`` and never leave the machine.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from .config import Config
from .schemas import UsageEntry, UsageSummary


class UsageTracker:
    def __init__(self, cfg: Config, max_records: int = 5000) -> None:
        self.cfg = cfg
        self.max_records = max_records
        self._lock = threading.Lock()
        self._path = self._resolve_path()

    def _resolve_path(self) -> Path:
        env = os.environ.get("PAIMIND_USAGE")
        if env:
            return Path(env)
        root = Path(self.cfg.get("app.data_dir", "data")) or Path("data")
        if not root.is_absolute():
            root = Path(__file__).resolve().parent.parent / root
        return root / "usage.jsonl"

    def record(
        self,
        tier: str,
        backend: str,
        source: str,
        latency_ms: int,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cached: bool = False,
    ) -> None:
        entry = UsageEntry(
            ts=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            tier=tier, backend=backend, source=source, latency_ms=latency_ms,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens, cached=cached,
        )
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(entry.model_dump(), ensure_ascii=False)
            with self._lock, self._path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            self._trim()
        except OSError:
            pass

    def _trim(self) -> None:
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        if len(lines) <= self.max_records:
            return
        keep = lines[-self.max_records :]
        try:
            self._path.write_text("\n".join(keep) + "\n", encoding="utf-8")
        except OSError:
            pass

    def summary(self, recent: int = 50) -> UsageSummary:
        entries: List[UsageEntry] = []
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in lines:
            try:
                entries.append(UsageEntry(**json.loads(line)))
            except Exception:
                continue

        by_backend: Dict[str, int] = {}
        by_tier: Dict[str, int] = {}
        by_source: Dict[str, int] = {}
        total_latency = 0
        total_tokens = 0
        for e in entries:
            by_backend[e.backend] = by_backend.get(e.backend, 0) + 1
            by_tier[e.tier] = by_tier.get(e.tier, 0) + 1
            by_source[e.source] = by_source.get(e.source, 0) + 1
            total_latency += e.latency_ms
            total_tokens += e.total_tokens

        avg = (total_latency / len(entries)) if entries else 0.0
        return UsageSummary(
            total_requests=len(entries),
            by_backend=by_backend,
            by_tier=by_tier,
            by_source=by_source,
            avg_latency_ms=round(avg, 1),
            total_tokens=total_tokens,
            recent=list(reversed(entries))[:recent],
        )

    def clear(self) -> None:
        try:
            self._path.unlink()
        except OSError:
            pass
