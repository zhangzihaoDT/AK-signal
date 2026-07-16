from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .paths import project_root


def _generate_run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{ts}-{uuid.uuid4().hex[:8]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _rel_path(p: str | Path) -> str:
    try:
        return str(Path(p).relative_to(project_root()))
    except ValueError:
        return str(p)


@dataclass
class RunContext:
    schema_version: int = 1
    subsystem: str = ""
    run_id: str = ""
    run_date: date = field(default_factory=lambda: date.today())
    status: str = ""
    offline: bool = False
    started_at: str = ""
    finished_at: str = ""
    summary: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.run_id:
            self.run_id = _generate_run_id()
        if not self.started_at:
            self.started_at = _now_iso()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "subsystem": self.subsystem,
            "run_id": self.run_id,
            "run_date": self.run_date.isoformat(),
            "status": self.status,
            "offline": self.offline,
            "started_at": self.started_at,
            "finished_at": self.finished_at or _now_iso(),
            "summary": self.summary,
            "artifacts": [_rel_path(p) for p in self.artifacts],
        }
