"""主题篮子配置与资产展开。"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import hashlib
import json

import yaml

from src.common.paths import config_dir, research_observations_path, selection_universe_path


def load_baskets(path: Path | None = None) -> dict[str, dict[str, Any]]:
    p = path or config_dir() / "research_baskets.yaml"
    with p.open(encoding="utf-8") as f:
        return (yaml.safe_load(f) or {}).get("baskets", {})


def load_basket(key: str, path: Path | None = None) -> dict[str, Any]:
    baskets = load_baskets(path)
    if key not in baskets:
        raise KeyError(f"research basket not found: {key}")
    cfg = dict(baskets[key])
    cfg["key"] = key
    return cfg


def basket_config_hash(basket: dict[str, Any]) -> str:
    payload = json.dumps(basket, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _load_universe_sections(universe_path: Path | None = None) -> dict[str, Any]:
    """合并加载篮子资产的两个来源（2026-08 配置拆分后分属两文件）：

    - themes（Selection 固定资产池）     ← config/selection_universe.yaml
    - observation_groups（研究观察组）   ← config/research_observations.yaml

    universe_path 非空时作为单一覆盖文件（测试/离线重放用），两个 section 都从它读取。
    """
    if universe_path is not None:
        with universe_path.open(encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    universe: dict[str, Any] = {}
    for path, section in ((selection_universe_path(), "themes"),
                          (research_observations_path(), "observation_groups")):
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if section in data:
            universe[section] = data[section]
    return universe


def expand_constituents(basket: dict[str, Any], universe_path: Path | None = None) -> list[dict[str, Any]]:
    """从 selection_universe.yaml / research_observations.yaml 展开篮子资产，保留 source group 元数据。"""
    universe = _load_universe_sections(universe_path)
    source = basket["source"]
    source_type, source_key = source["type"], source["key"]
    if source_type == "observation_group":
        section = (universe.get("observation_groups") or {}).get(source_key, {})
        containers = section.get("groups", {})
        items = containers.items()
        asset_key = "listed_assets"
    elif source_type == "theme":
        section = (universe.get("themes") or {}).get(source_key, {})
        containers = section.get("tiers", [])
        items = ((c.get("key", str(i)), c) for i, c in enumerate(containers))
        asset_key = "assets"
    else:
        raise ValueError(f"unsupported basket source type: {source_type}")

    include = set(source.get("include_groups", []))
    exclude = set(source.get("exclude_groups", []))
    out: list[dict[str, Any]] = []
    for group, container in items:
        group = str(group)
        if include and group not in include:
            continue
        if group in exclude:
            continue
        for asset in container.get(asset_key, []) or []:
            symbol = str(asset.get("symbol", "")).strip()
            if not symbol:
                continue
            out.append({
                "group": group,
                "group_label": str(container.get("label", group)),
                "symbol": symbol,
                "name": str(asset.get("name", symbol)),
                "market": str(asset.get("market", "CN")).upper(),
                "evidence_stage": str(asset.get("evidence_stage", "")),
                "capacity_stage": str(asset.get("capacity_stage", "")),
                "revenue_evidence": str(asset.get("revenue_evidence", "")),
                "note": str(asset.get("note", "")),
            })
    if not out:
        raise ValueError(f"basket has no constituents: {basket['key']}")
    return out
