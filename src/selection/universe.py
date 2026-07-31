"""
Layer ③ 分层资产池 — universe 加载与行业确认注入

职责：
  - 加载 config/stock_universe.yaml（theme → tier → assets 分层结构）
  - 将分层池扁平化为可运行的资产列表（保留 theme/tier 元数据）
  - 读取 Layer ② confirmation_{date}.parquet，按 sw_industry 注入行业确认信号
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from src.trend_engine.asset import Asset

logger = logging.getLogger("selection.universe")

MARKET_ALIAS = {
    "A": "CN", "ASHARE": "CN", "CN": "CN",
    "HK": "HK", "H": "HK",
    "US": "US", "U": "US",
}


def _norm_market(v: Any) -> str:
    return MARKET_ALIAS.get(str(v).strip().upper(), str(v).strip().upper())


def _infer_exchange(symbol: str) -> str:
    if symbol.startswith(("5", "6", "688", "689")):
        return "SSE"
    if symbol.startswith(("0", "1", "2", "3")):
        return "SZSE"
    return ""


def _default_currency(market: str) -> str:
    return {"CN": "CNY", "HK": "HKD", "US": "USD"}.get(market, "")


@dataclass(frozen=True)
class UniverseItem:
    asset: Asset
    theme: str
    theme_label: str
    tier: str
    tier_label: str
    sw_industry: str = ""
    note: str = ""
    update_policy: str = "daily"
    priority: str = "B"


def load_universe(path: Path) -> dict[str, Any]:
    """加载分层 universe 配置。"""
    if not path.exists():
        logger.error("universe config not found: %s", path)
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_universe_items(path: Path) -> list[UniverseItem]:
    """将分层 universe 扁平化为资产列表（保留 theme/tier 元数据）。"""
    cfg = load_universe(path)
    themes = cfg.get("themes", {})
    items: list[UniverseItem] = []

    for theme_key, theme_cfg in themes.items():
        theme_label = str(theme_cfg.get("label", theme_key))
        for tier_cfg in theme_cfg.get("tiers", []):
            tier_key = str(tier_cfg.get("key", ""))
            tier_label = str(tier_cfg.get("label", tier_key))
            for a in tier_cfg.get("assets", []):
                symbol = str(a.get("symbol", "")).strip()
                if not symbol or symbol.upper() == "TBD":
                    logger.warning("skip placeholder asset: %s (%s/%s)", symbol, theme_key, tier_key)
                    continue
                market = _norm_market(a.get("market", "CN"))
                exchange = str(a.get("exchange", "") or "") or _infer_exchange(symbol)
                currency = str(a.get("currency", "") or "") or _default_currency(market)
                asset = Asset(
                    symbol=symbol,
                    name=str(a.get("name", symbol)),
                    market=market,
                    exchange=exchange or None,
                    currency=currency or None,
                    category=tier_key,
                )
                items.append(UniverseItem(
                    asset=asset,
                    theme=theme_key,
                    theme_label=theme_label,
                    tier=tier_key,
                    tier_label=tier_label,
                    sw_industry=str(a.get("sw_industry", "")).strip(),
                    note=str(a.get("note", "")).strip(),
                    update_policy=str(a.get("update_policy", "daily")).strip().lower(),
                    priority=str(a.get("priority", "B")).strip().upper(),
                ))

    logger.info("universe loaded: %d assets across %d themes", len(items), len(themes))
    return items


# ── Layer ② 行业确认注入 ─────────────────────────────────────────

def load_latest_confirmation(processed_dir: Path) -> pd.DataFrame:
    """读取最新一份 confirmation_{date}.parquet。"""
    files = sorted(processed_dir.glob("confirmation_*.parquet"))
    if not files:
        return pd.DataFrame()
    latest = files[-1]
    try:
        df = pd.read_parquet(latest)
        logger.info("confirmation loaded: %s (%d industries)", latest.name, len(df))
        return df
    except Exception as e:
        logger.warning("failed to load confirmation %s: %s", latest, e)
        return pd.DataFrame()


def build_confirmation_map(confirmation: pd.DataFrame) -> dict[str, dict[str, Any]]:
    """industry_code → 行业确认信号。"""
    if confirmation.empty or "industry_code" not in confirmation.columns:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for _, r in confirmation.iterrows():
        code = str(r.get("industry_code", "")).strip()
        if not code:
            continue
        out[code] = {
            "sw_industry_name": str(r.get("industry_name", "")),
            "sw_confirm_level": str(r.get("strength_level", "")),
            "sw_drive_pattern": str(r.get("drive_pattern", "")),
            "sw_theme": str(r.get("theme_label", "")),
            "sw_rps15": r.get("RPS15"),
            "sw_theme_key": str(r.get("theme", "")),
        }
    return out


def inject_confirmation(items: list[UniverseItem], processed_dir: Path) -> tuple[list[UniverseItem], dict[str, dict[str, Any]]]:
    """将行业确认信号挂到每个标的。

    Returns:
        (enriched_items, confirm_map)  — confirm_map 由调用方在报告阶段合并
    """
    confirmation = load_latest_confirmation(processed_dir)
    confirm_map = build_confirmation_map(confirmation)
    logger.info("industry confirmation map: %d industries", len(confirm_map))
    return items, confirm_map
