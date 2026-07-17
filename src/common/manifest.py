from __future__ import annotations

import json
import os
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from .paths import manifest_path, project_root
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

    prev = existing.get(ctx.subsystem, {})

    # 不覆盖已有 completed 记录
    prev_completed = prev.get("status") == "completed"
    if prev_completed and ctx.status in ("failed", "waiting_for_source", "no_new_data", "partial"):
        return path

    existing[ctx.subsystem] = ctx.to_dict()
    raw = json.dumps(existing, ensure_ascii=False, indent=2)

    tmp = path.with_suffix(".tmp")
    tmp.write_text(raw, encoding="utf-8")
    tmp.flush() if hasattr(tmp, "flush") else None
    os.replace(str(tmp), str(path))
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


def read_latest_run(
    subsystem: str,
    require_status: str | set[str] | None = None,
) -> RunContext | None:
    raw = read_subsystem_manifest(subsystem)
    if raw is None:
        return None

    if require_status is not None:
        allowed = {require_status} if isinstance(require_status, str) else set(require_status)
        if raw.get("status") not in allowed:
            return None

    return RunContext(
        schema_version=raw.get("schema_version", 1),
        subsystem=raw["subsystem"],
        run_id=raw.get("run_id", ""),
        run_date=date.fromisoformat(raw["run_date"]),
        status=raw["status"],
        offline=raw.get("offline", False),
        started_at=raw.get("started_at", ""),
        finished_at=raw.get("finished_at", ""),
        summary=raw.get("summary", {}),
        artifacts=raw.get("artifacts", []),
    )


def find_latest_artifact(
    subsystem: str,
    glob_pattern: str,
    require_status: str = "completed",
) -> Path | None:
    root = project_root()

    ctx = read_latest_run(subsystem, require_status=require_status)
    if ctx is not None:
        for art in ctx.artifacts:
            p = root / art
            if p.match(glob_pattern) and p.exists():
                return p

    base = root / "outputs" / subsystem
    if not base.exists():
        return None
    matches = sorted(base.glob(glob_pattern), reverse=True)
    if not matches:
        return None
    result = matches[0]
    if not result.exists():
        return None
    return result
