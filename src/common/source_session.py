"""One acquisition session: failed sources stay open until the next run."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SourceSession:
    failure_limit: int = 3
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)
    assets: list[dict[str, Any]] = field(default_factory=list)

    def stats(self, source: str) -> dict[str, Any]:
        return self.sources.setdefault(source, {
            "requests": 0, "success": 0, "failed": 0,
            "consecutive_errors": 0, "state": "CLOSED",
        })

    def available(self, source: str) -> bool:
        return self.stats(source)["state"] != "OPEN"

    def record(self, source: str, success: bool, *, empty: bool = False) -> bool:
        s = self.stats(source)
        s["requests"] += 1
        s["success" if success else "failed"] += 1
        # An empty symbol response is not evidence of a broken source.
        s["consecutive_errors"] = 0 if success or empty else s["consecutive_errors"] + 1
        if s["consecutive_errors"] >= self.failure_limit:
            s["state"] = "OPEN"
        return s["state"] == "OPEN"
