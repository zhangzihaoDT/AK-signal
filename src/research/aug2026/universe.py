"""Aug2026 研究：universe 清单与集合差集校验。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from src.common.paths import config_dir, outputs_dir

_ETF_RE = re.compile(r"^(51|52|56|58|15|16|18)\d{4}$")


def load_selection_universe_cn() -> dict[str, dict[str, Any]]:
    """selection_universe.yaml → {symbol: {theme, tier, tier_label, name, sw_industry, participation}}。

    只返回 CN 市场的个股（不含 ETF）。
    """
    path = config_dir() / "selection_universe.yaml"
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    out: dict[str, dict[str, Any]] = {}
    for theme, t in cfg.get("themes", {}).items():
        for tier in t.get("tiers", []):
            participation = tier.get("participation", "tradeable")
            for a in tier.get("assets", []):
                if a.get("market") != "CN":
                    continue
                sym = str(a["symbol"])
                if _ETF_RE.match(sym):
                    continue
                out[sym] = {
                    "symbol": sym,
                    "name": a.get("name", ""),
                    "theme": theme,
                    "tier": tier.get("key", ""),
                    "tier_label": tier.get("label", ""),
                    "sw_industry": a.get("sw_industry", ""),
                    "participation": participation,
                }
    return out


def load_replay_stock_symbols(replay_path: Path) -> set[str]:
    """historical_signals parquet 中 entity_type=stock 的 entity_code 集合。"""
    df = pd.read_parquet(replay_path)
    st = df[df["entity_type"] == "stock"]
    return set(st["entity_code"].astype(str))


def build_universe_manifest() -> dict[str, Any]:
    """构建固定池清单 + 全市场清单 + 补算清单，落盘 universe_manifest.json。"""
    cn = load_selection_universe_cn()
    expected = set(cn.keys())

    replay_path = outputs_dir() / "research" / "historical_signals_20260731.parquet"
    replay = load_replay_stock_symbols(replay_path) if replay_path.exists() else set()

    missing = expected - replay          # 需要从 processed CSV 补算特征
    extra = replay - expected            # 不属于 CN 固定池（如 HK 禾赛/速腾）

    manifest = {
        "trade_window": ["2026-07-31", "2026-08-28"],
        "adjust": "qfq",
        "feature_date": "2026-07-31",
        "expected_universe_cn_stocks": sorted(expected),
        "n_expected": len(expected),
        "replay_stock_symbols": sorted(replay),
        "n_replay": len(replay),
        "intersection": sorted(expected & replay),
        "n_intersection": len(expected & replay),
        "missing_from_replay_need_compute": sorted(missing),
        "replay_extra_not_in_cn_pool": sorted(extra),
        "compute_list": sorted(missing),  # 需从 processed CSV 补算 7/31 特征
    }
    out = STUDY_DIR / "universe_manifest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


from . import STUDY_DIR  # noqa: E402
