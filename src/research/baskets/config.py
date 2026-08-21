"""主题篮子配置与资产展开。"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import hashlib
import json

import yaml

from src.common.paths import config_dir, stock_universe_path


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


def expand_constituents(basket: dict[str, Any], universe_path: Path | None = None) -> list[dict[str, Any]]:
    """从 stock_universe.yaml 展开篮子资产，保留 source group 元数据。"""
    with (universe_path or stock_universe_path()).open(encoding="utf-8") as f:
        universe = yaml.safe_load(f) or {}
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
