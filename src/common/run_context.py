from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class RunContext:
    subsystem: str
    run_date: date
    status: str
    summary: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def to_dict(self) -> dict[str, Any]:
        return {
            "subsystem": self.subsystem,
            "run_date": self.run_date.isoformat(),
            "status": self.status,
            "summary": self.summary,
            "artifacts": [str(p) for p in self.artifacts],
            "timestamp": self.timestamp,
        }
