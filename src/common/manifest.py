from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from .paths import manifest_path
from .run_context import RunContext


def write_run_manifest(ctx: RunContext) -> Path:
    path = manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict[str, Any] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}

    existing[ctx.subsystem] = ctx.to_dict()

    path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_subsystem_manifest(subsystem: str) -> dict[str, Any] | None:
    path = manifest_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get(subsystem)
    except Exception:
        return None


def read_latest_run(subsystem: str) -> RunContext | None:
    raw = read_subsystem_manifest(subsystem)
    if raw is None:
        return None
    return RunContext(
        subsystem=raw["subsystem"],
        run_date=date.fromisoformat(raw["run_date"]),
        status=raw["status"],
        summary=raw.get("summary", {}),
        artifacts=raw.get("artifacts", []),
        timestamp=raw.get("timestamp", ""),
    )


def find_latest_artifact(subsystem: str, glob_pattern: str) -> Path | None:
    from .paths import outputs_dir
    base = outputs_dir() / subsystem
    if not base.exists():
        return None
    matches = sorted(base.glob(glob_pattern), reverse=True)
    return matches[0] if matches else None
