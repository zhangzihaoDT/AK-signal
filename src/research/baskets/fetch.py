"""Research Basket 数据抓取（Observation 层，在线）。

从 `config/research_baskets.yaml` 声明的篮子来源出发，按 (type, key) 去重后抓取
主题 / 观察组标的行情，落盘供纯离线的篮子计算消费：

  data/raw/themes/<key>/<market>_<symbol>.csv              主题每标的日线
  data/raw/observation_groups/<key>/<market>_<symbol>.csv  观察组每标的日线
  data/raw/<source>/<key>/<key>_prices.parquet             合并长表

CN 复用 `src.common.market_data.fetch_cn_daily`（em → sina → tx 多源切换 + 节流 +
退避，qfq 前复权）；HK 用 akshare `stock_hk_daily`。默认跳过 ETF tier
（theme_etf / sub_industry_etf / watch_etf），只抓股票来源。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import yaml

from src.common.market_data import fetch_cn_daily
from src.common.paths import project_root, raw_dir, research_observations_path, selection_universe_path

logger = logging.getLogger("basket_fetch")

DEFAULT_SKIP_TIERS = {"theme_etf", "sub_industry_etf", "watch_etf"}

_SOURCE_DIR = {"theme": "themes", "observation_group": "observation_groups"}
_SOURCE_FILE = {"theme": selection_universe_path, "observation_group": research_observations_path}
_SOURCE_ROOT = {"theme": "themes", "observation_group": "observation_groups"}


def source_keys(baskets: dict[str, dict[str, Any]] | None = None) -> list[tuple[str, str]]:
    """篮子配置里所有来源，按 (source_type, source_key) 去重并保持顺序。"""
    if baskets is None:
        from .config import load_baskets
        baskets = load_baskets()
    seen: list[tuple[str, str]] = []
    for basket in baskets.values():
        source = basket["source"]
        pair = (str(source["type"]), str(source["key"]))
        if pair not in seen:
            seen.append(pair)
    return seen


def _load_section(source_type: str, key: str) -> dict[str, Any]:
    path = _SOURCE_FILE[source_type]()
    cfg = yaml.safe_load(open(path, encoding="utf-8")) or {}
    root = cfg.get(_SOURCE_ROOT[source_type]) or {}
    section = root.get(key)
    if not section:
        raise SystemExit(f"{source_type}.{key} not found in {path}")
    return section


def _collect_assets(section: dict[str, Any], source_type: str, skip_tiers: set[str]) -> list[dict[str, Any]]:
    """展开 groups（观察组）/ tiers（主题）下的 assets。"""
    containers = section.get("groups") if source_type == "observation_group" else section.get("tiers")
    if not containers:
        raise SystemExit("no groups/tiers in section")
    if isinstance(containers, dict):
        container_items = containers.items()
    else:
        container_items = [(str(c.get("key", i)), c) for i, c in enumerate(containers)]
    assets: list[dict[str, Any]] = []
    for ck, cv in container_items:
        if str(ck) in skip_tiers:
            continue
        raw_assets = cv.get("listed_assets") if source_type == "observation_group" else cv.get("assets")
        for a in (raw_assets or []):
            symbol = str(a.get("symbol", "")).strip()
            if not symbol:
                continue
            assets.append({
                "symbol": symbol,
                "name": str(a.get("name", symbol)),
                "group": str(ck),
                "group_label": str(cv.get("label", ck)),
                "market": str(a.get("market", "CN")),
            })
    return assets


def _fetch_hk_daily(symbol: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
    """HK 个股日线（stock_hk_daily）。"""
    import akshare as ak
    import pandas as pd

    sym = str(symbol).strip()
    if sym.isdigit() and len(sym) < 5:
        sym = sym.zfill(5)
    time.sleep(1.5)
    df = ak.stock_hk_daily(symbol=sym)
    if df is None or df.empty:
        return []
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    rename = {}
    for canon, cands in {"date": ["date", "日期"], "open": ["open", "开盘"],
                         "high": ["high", "最高"], "low": ["low", "最低"],
                         "close": ["close", "收盘"], "volume": ["volume", "成交量"]}.items():
        picked = next((c for c in cands if c in df.columns), None)
        if picked:
            rename[picked] = canon
    df = df.rename(columns=rename)
    if "date" not in df.columns or "close" not in df.columns:
        return []
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for c in ["open", "high", "low", "close", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["date", "close"])
    df = df[(df["date"] >= pd.to_datetime(start_date)) & (df["date"] <= pd.to_datetime(end_date))]
    df = df.sort_values("date").reset_index(drop=True)
    return df[["date", "open", "high", "low", "close", "volume"]].to_dict("records")


def _fetch_prices(assets: list[dict[str, Any]], start_date: str, end_date: str) -> list[dict[str, Any]]:
    import pandas as pd

    out: list[dict[str, Any]] = []
    meta = ("group", "group_label", "symbol", "name", "market")
    for a in assets:
        sym = a["symbol"]
        market = a["market"].upper()
        logger.info("  fetching %s %s (%s) ...", sym, a["name"], market)
        if market == "HK":
            rows = _fetch_hk_daily(sym, start_date, end_date)
            for r in rows:
                out.append({**{k: a[k] for k in meta},
                            "date": r["date"], "open": r["open"], "high": r["high"],
                            "low": r["low"], "close": r["close"], "volume": r["volume"]})
            if rows:
                logger.info("  %s: %d rows (%s .. %s)", sym, len(rows),
                            rows[0]["date"].date(), rows[-1]["date"].date())
            else:
                logger.warning("  no data for %s", sym)
        else:
            df = fetch_cn_daily(sym, start_date=start_date, end_date=end_date)
            if df is None or df.empty:
                logger.warning("  no data for %s", sym)
                continue
            for _, r in df.iterrows():
                out.append({**{k: a[k] for k in meta},
                            "date": r["date"], "open": r["open"], "high": r["high"],
                            "low": r["low"], "close": r["close"], "volume": r["volume"]})
            logger.info("  %s: %d rows (%s .. %s)", sym, len(df),
                        df["date"].iloc[0].date(), df["date"].iloc[-1].date())
    return out


def fetch_source_prices(
    source_type: str,
    key: str,
    *,
    days: int = 400,
    skip_tiers: set[str] | None = None,
    out_root: Path | None = None,
) -> Path:
    """抓取单个来源的全部标的行情，返回合并长表 parquet 路径。"""
    import pandas as pd

    if source_type not in _SOURCE_DIR:
        raise ValueError(f"unsupported source type: {source_type}")
    skip = DEFAULT_SKIP_TIERS if skip_tiers is None else skip_tiers
    section = _load_section(source_type, key)
    assets = _collect_assets(section, source_type, skip)
    if not assets:
        raise SystemExit(f"no assets in {source_type}.{key}")

    end_date = pd.Timestamp.today().strftime("%Y%m%d")
    start_date = (pd.Timestamp.today() - pd.Timedelta(days=days)).strftime("%Y%m%d")
    logger.info("%s=%s | %d assets | %s .. %s", source_type, key, len(assets), start_date, end_date)

    rows = _fetch_prices(assets, start_date, end_date)
    if not rows:
        raise SystemExit(f"no price rows fetched for {source_type}.{key}")

    out_dir = (out_root or raw_dir()) / _SOURCE_DIR[source_type] / key
    out_dir.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows).sort_values(["symbol", "date"]).reset_index(drop=True)
    for sym, sub in df.groupby("symbol", sort=True):
        market = sub["market"].iloc[0]
        sub.drop(columns=["group", "group_label", "symbol", "name", "market"]).to_csv(
            out_dir / f"{market}_{sym}.csv", index=False, encoding="utf-8")
    parquet_path = out_dir / f"{key}_prices.parquet"
    df.to_parquet(parquet_path, index=False)
    logger.info("saved: %s (%d rows) | %s", out_dir, len(df), parquet_path)
    return parquet_path


def fetch_all_basket_sources(
    *,
    days: int = 400,
    baskets: dict[str, dict[str, Any]] | None = None,
    skip_tiers: set[str] | None = None,
) -> list[Path]:
    """抓取所有篮子来源（按 (type, key) 去重）。"""
    paths: list[Path] = []
    for source_type, key in source_keys(baskets):
        paths.append(fetch_source_prices(source_type, key, days=days, skip_tiers=skip_tiers))
    return paths
