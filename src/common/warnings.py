"""
run-day 跨步骤警告收集器。

各子系统在运行期把非致命问题（如 drilldown 个股行情抓取失败）通过 record()
登记到当前进程缓冲区，再在步骤落盘时用 save_warnings(trade_date) 追加进按日期的
JSON 文件（outputs/run_warnings_{trade_date}.json），供 run-day 末端 Final Validation
统一汇总展示。

设计：进程内 buffer 线程安全；落盘按 (category, message) 去重，多次重跑不重复累积。
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from .paths import outputs_dir

_lock = threading.Lock()
_buffered: list[dict[str, Any]] = []


def record(category: str, message: str, **fields: Any) -> None:
    """登记一条警告到当前进程缓冲区（线程安全）。"""
    entry: dict[str, Any] = {"category": category, "message": str(message)}
    entry.update(fields)
    with _lock:
        _buffered.append(entry)


def drain() -> list[dict[str, Any]]:
    """取走并清空当前进程缓冲区。"""
    with _lock:
        out = list(_buffered)
        _buffered.clear()
        return out


def warnings_path(trade_date: str) -> Path:
    return outputs_dir() / f"run_warnings_{trade_date}.json"


def load_warnings(trade_date: str) -> list[dict[str, Any]]:
    path = warnings_path(trade_date)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_warnings(trade_date: str, categories: set[str] | None = None) -> int:
    """把当前进程缓冲区的警告（可过滤类别）追加进按日期文件。

    Returns:
        新增落盘的警告条数。
    """
    items = drain()
    if categories is not None:
        items = [w for w in items if w.get("category") in categories]
    if not items:
        return 0

    existing = load_warnings(trade_date)
    seen = {(w.get("category"), w.get("message")) for w in existing}
    merged = list(existing)
    added = 0
    for w in items:
        key = (w.get("category"), w.get("message"))
        if key in seen:
            continue
        merged.append(w)
        seen.add(key)
        added += 1

    if added:
        path = warnings_path(trade_date)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(path))
    return added
