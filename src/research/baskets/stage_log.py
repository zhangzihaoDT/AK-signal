"""Research Basket Stage-Change 日志解析。

日志文件 config/research_stage_log.yaml 记录每次 evidence-driven stage update，
未来 stage-upgrade event study 直接读它重建每个标的的阶段历史。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from src.common.paths import config_dir
from .config import expand_constituents, load_baskets

FIELDS = ("evidence_stage", "revenue_evidence", "capacity_stage")

STAGE_LOG_FILENAME = "research_stage_log.yaml"

# 商业化阶段中文描述（展示层映射，不改事实；与 research_observations.yaml 的固定枚举一一对应）。
EVIDENCE_STAGE_CN: dict[str, str] = {
    "VALIDATION": "客户验证",
    "DESIGN_WIN": "设计定标",
    "ORDER": "已获订单",
    "SMALL_BATCH": "小批量供货",
    "MASS_PRODUCTION": "批量量产",
}


def evidence_stage_cn(stage: str) -> str:
    """把 evidence_stage 枚举转成中文描述；未知值原样返回。"""
    return EVIDENCE_STAGE_CN.get(str(stage).strip().upper(), stage or "")


def stage_log_path() -> Path:
    return config_dir() / STAGE_LOG_FILENAME


def load_stage_log(path: Path | None = None) -> dict[str, Any]:
    p = path or stage_log_path()
    with p.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict) or "genesis" not in data:
        raise ValueError(f"stage log missing genesis section: {p}")
    return data


def _normalize(value: Any) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    return "" if s.lower() in {"", "none", "null"} else s


def _blank_state() -> dict[str, str]:
    return {field: "" for field in FIELDS}


def _genesis_state(log: dict[str, Any]) -> dict[tuple[str, str], dict[str, str]]:
    state: dict[tuple[str, str], dict[str, str]] = {}
    for basket, assets in (log.get("genesis") or {}).items():
        for asset in assets:
            key = (str(basket), str(asset["symbol"]))
            state[key] = {
                field: _normalize(asset.get(field)) for field in FIELDS
            }
    return state


def apply_stage_log(log: dict[str, Any]) -> dict[tuple[str, str], dict[str, str]]:
    """genesis 快照 + entries 增量 → 逐 (basket, symbol) 的当前阶段。

    校验链一致性：每条 entry 的 from 必须等于当前已应用状态，否则抛错。
    """
    state = _genesis_state(log)
    for entry in sorted(log.get("entries") or [], key=lambda e: e["date"]):
        key = (str(entry["basket"]), str(entry["symbol"]))
        field = entry.get("field", "evidence_stage")
        if field not in FIELDS:
            raise ValueError(f"invalid stage-log field: {field}")
        current = state.setdefault(key, _blank_state())
        expected_from = _normalize(entry.get("from"))
        if expected_from != current[field]:
            raise ValueError(
                f"stage-log chain mismatch: {key} {field}: log from={entry.get('from')!r} "
                f"but applied state is {current[field]!r}"
            )
        current[field] = _normalize(entry["to"])
    return state


def config_matches_log(
    baskets: dict[str, Any] | None = None,
    log: dict[str, Any] | None = None,
    universe_path: Path | None = None,
) -> tuple[bool, list[str]]:
    """校验 research_observations.yaml / selection_universe.yaml 当前阶段 == stage log（genesis + entries）推得阶段。

    每次 evidence-driven stage update 必须同时改 config 与追加 log entry；
    本校验保证两者不漂移。
    """
    baskets = baskets or load_baskets()
    log = log or load_stage_log()
    expected = apply_stage_log(log)
    diffs: list[str] = []

    log_baskets = set(log.get("genesis") or {}) | {str(e.get("basket")) for e in log.get("entries") or []}
    for basket in sorted(log_baskets):
        if basket not in baskets:
            diffs.append(f"basket {basket} in stage log but missing from research_baskets.yaml")

    for basket in sorted(log_baskets):
        if basket not in baskets:
            continue
        for asset in expand_constituents(baskets[basket], universe_path):
            symbol = str(asset["symbol"])
            expected_asset = expected.get((basket, symbol))
            if expected_asset is None:
                diffs.append(f"{basket}/{symbol} in config but missing from stage log")
                continue
            for field in FIELDS:
                if _normalize(asset[field]) != expected_asset[field]:
                    diffs.append(
                        f"{basket}/{symbol} {field}: config={asset[field]!r} != stage-log={expected_asset[field]!r}"
                    )
    return not diffs, diffs
