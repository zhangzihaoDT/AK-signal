"""
获取观察组 / 主题标的近 N 日股价（Observation 层，在线抓取，落盘供后续计算）。

读取 config/stock_universe.yaml：
  --source group → observation_groups.<key>.groups[].listed_assets
  --source theme → themes.<key>.tiers[].assets

CN 复用 src.common.market_data.fetch_cn_daily（em → sina → tx 多源切换 + 节流 + 退避，qfq 前复权）；
HK 用 akshare stock_hk_daily（节流 + 日期过滤）。

产物：
  data/raw/observation_groups/<key>/<market>_<symbol>.csv  观察组每标的日线（date,open,high,low,close,volume）
  data/raw/themes/<key>/<market>_<symbol>.csv              主题每标的日线（同结构）
  data/raw/<source>s/<key>/<key>_prices.parquet            合并长表（group,label,symbol,name,date,open,high,low,close,volume）

focus_entities（未上市实体）不抓取，仅保留在 assets 中已有代码的标的。
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common.market_data import fetch_cn_daily  # noqa: E402
from src.common.paths import project_root  # noqa: E402

logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(message)s")
logger = logging.getLogger("fetch_watchlist_prices")


def load_section(cfg_path: Path, source: str, key: str) -> dict:
    cfg = yaml.safe_load(open(cfg_path, encoding="utf-8")) or {}
    root = cfg.get("observation_groups" if source == "group" else "themes") or {}
    section = root.get(key)
    if not section:
        raise SystemExit(f"{source}.{key} not found in {cfg_path}")
    return section


def collect_assets(section: dict, source: str, skip_tiers: set[str] | None = None) -> list[dict]:
    """展开 groups（group 源）或 tiers（theme 源）下的 assets。

    skip_tiers: 需要跳过的 tier/group 键（如 ETF 层 theme_etf/sub_industry_etf）。
    """
    containers = section.get("groups") if source == "group" else section.get("tiers")
    if not containers:
        raise SystemExit("no groups/tiers in section")
    if isinstance(containers, dict):
        container_items = containers.items()
    else:
        container_items = [(str(c.get("key", i)), c) for i, c in enumerate(containers)]
    assets: list[dict] = []
    for ck, cv in container_items:
        if skip_tiers and str(ck) in skip_tiers:
            continue
        raw_assets = cv.get("listed_assets") if source == "group" else cv.get("assets")
        for a in (raw_assets or []):
            sym = str(a.get("symbol", "")).strip()
            if not sym:
                continue
            assets.append({
                "symbol": sym,
                "name": str(a.get("name", sym)),
                "group": str(ck),
                "group_label": str(cv.get("label", ck)),
                "market": str(a.get("market", "CN")),
            })
    return assets


def fetch_hk_daily(symbol: str, start_date: str, end_date: str) -> dict | None:
    """HK 个股日线（stock_hk_daily），返回 {date,open,high,low,close,volume} 列表。"""
    import akshare as ak
    import pandas as pd

    sym = str(symbol).strip()
    if sym.isdigit() and len(sym) < 5:
        sym = sym.zfill(5)
    time.sleep(1.5)
    df = ak.stock_hk_daily(symbol=sym)
    if df is None or df.empty:
        return None
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
        return None
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for c in ["open", "high", "low", "close", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["date", "close"])
    df = df[(df["date"] >= pd.to_datetime(start_date)) & (df["date"] <= pd.to_datetime(end_date))]
    df = df.sort_values("date").reset_index(drop=True)
    return df[["date", "open", "high", "low", "close", "volume"]].to_dict("records")


def fetch_prices(assets: list[dict], start_date: str, end_date: str) -> list[dict]:
    out: list[dict] = []
    for a in assets:
        sym = a["symbol"]
        market = a["market"].upper()
        logger.info("  fetching %s %s (%s) ...", sym, a["name"], market)
        if market == "HK":
            rows = fetch_hk_daily(sym, start_date, end_date)
            for r in (rows or []):
                out.append({**{k: a[k] for k in ("group", "group_label", "symbol", "name", "market")},
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
                out.append({**{k: a[k] for k in ("group", "group_label", "symbol", "name", "market")},
                            "date": r["date"], "open": r["open"], "high": r["high"],
                            "low": r["low"], "close": r["close"], "volume": r["volume"]})
            logger.info("  %s: %d rows (%s .. %s)", sym, len(df),
                        df["date"].iloc[0].date(), df["date"].iloc[-1].date())
    return out


def main() -> None:
    import pandas as pd

    parser = argparse.ArgumentParser(description="获取观察组/主题标的近 N 日股价")
    parser.add_argument("--key", default="scania_china_watch")
    parser.add_argument("--source", choices=["group", "theme"], default="group",
                        help="group=observation_groups / theme=themes")
    parser.add_argument("--days", type=int, default=365,
                        help="近 N 自然日（默认 365）")
    parser.add_argument("--skip-tiers", default="",
                        help="逗号分隔的 tier/group 键（跳过，如 theme_etf,sub_industry_etf）")
    parser.add_argument("--out", type=Path, default=None,
                        help="输出目录（默认 data/raw/<source>s/<key>）")
    args = parser.parse_args()

    root = project_root()
    cfg_path = root / "config" / "stock_universe.yaml"
    section = load_section(cfg_path, args.source, args.key)
    skip = {s.strip() for s in args.skip_tiers.split(",") if s.strip()}
    assets = collect_assets(section, args.source, skip)
    if not assets:
        raise SystemExit("no assets in section")

    end_date = pd.Timestamp.today().strftime("%Y%m%d")
    start_date = (pd.Timestamp.today() - pd.Timedelta(days=args.days)).strftime("%Y%m%d")
    logger.info("%s=%s | %d assets | %s .. %s", args.source, args.key, len(assets), start_date, end_date)

    rows = fetch_prices(assets, start_date, end_date)
    if not rows:
        raise SystemExit("no price rows fetched")

    out_dir = args.out or (root / "data" / "raw" / ("observation_groups" if args.source == "group" else "themes") / args.key)
    out_dir.mkdir(parents=True, exist_ok=True)

    df = pd.DataFrame(rows).sort_values(["symbol", "date"]).reset_index(drop=True)
    for sym, sub in df.groupby("symbol", sort=True):
        market = sub["market"].iloc[0]
        sub.drop(columns=["group", "group_label", "symbol", "name", "market"]).to_csv(
            out_dir / f"{market}_{sym}.csv", index=False, encoding="utf-8")
    parquet_path = out_dir / f"{args.key}_prices.parquet"
    df.to_parquet(parquet_path, index=False)

    logger.info("saved: %s (%d rows) | %s", out_dir, len(df), parquet_path)


if __name__ == "__main__":
    main()
