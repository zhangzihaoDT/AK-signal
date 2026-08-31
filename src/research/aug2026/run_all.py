"""Aug2026 研究：一键编排（固定池 + 全市场 + 报告）。"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

import pandas as pd

from src.common.paths import raw_dir

from . import STUDY_DIR, MARKET_DAILY_DIR
from .universe import build_universe_manifest
from .panel import build_fixed_pool_panel, enrich_position_features, build_market_panel, save_panel
from .layers import (
    hs300_aug_return, layer_a_distribution, layer_a_top_bottom,
    layer_b_features, layer_c_portfolios, save_layer,
)
from .report import render_report


def load_labels() -> dict:
    from .universe import load_selection_universe_cn
    return load_selection_universe_cn()


def run_fixed_pool() -> pd.DataFrame:
    m = build_universe_manifest()
    codes = m["expected_universe_cn_stocks"]
    replay = pd.read_parquet(
        "outputs/research/historical_signals_20260731.parquet")
    replay_stock = replay.loc[replay["entity_type"] == "stock"]
    panel = build_fixed_pool_panel(codes, replay_stock)
    panel = enrich_position_features(panel)
    labels = load_labels()
    for col in ["theme", "tier", "tier_label", "sw_industry", "participation", "name"]:
        panel[col] = panel["code"].map(lambda c: labels.get(c, {}).get(col))
    save_panel(panel, "fixed_pool_panel")
    return panel


def run_market_panel() -> pd.DataFrame:
    daily_path = MARKET_DAILY_DIR / "market_daily_20260701_20260828_qfq.parquet"
    if not daily_path.exists():
        raise FileNotFoundError(f"market daily not ready: {daily_path}")
    daily = pd.read_parquet(daily_path)
    panel = build_market_panel(daily)
    save_panel(panel, "market_panel")
    return panel


def run_analysis(panel_fixed: pd.DataFrame, panel_market: pd.DataFrame) -> dict:
    bench = hs300_aug_return()
    provenance = {
        "trade_window": ["2026-07-31", "2026-08-28"],
        "adjust": "qfq",
        "feature_date": "2026-07-31",
        "return_source_fixed_pool": "data/raw/CN_*.csv (qfq) + tx fallback",
        "return_source_market": "akshare stock_zh_a_hist_tx qfq",
        "benchmark": "sh000300 (refreshed to 8/28)",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }

    # Layer A
    fixed_ret = pd.Series(panel_fixed["return_aug"])
    market_ret = pd.Series(panel_market["return_aug"])
    a_fixed = layer_a_distribution(fixed_ret, "fixed_pool(51)", bench)
    a_market = layer_a_distribution(market_ret, "market_all", bench)
    a_market["benchmark_hs300"] = bench
    a_fixed["benchmark_hs300"] = bench
    save_layer("layerA_fixed_pool", a_fixed)
    save_layer("layerA_market", a_market)

    # Layer B
    b_fixed = layer_b_features(panel_fixed, bench)
    save_layer("layerB_fixed_pool", b_fixed)
    # 全市场无标签 → 只有横截面分布，不做特征归因
    b_market = {"note": "全市场无 theme/tier/trend 特征，Layer B 仅固定池可用"}

    # Layer C
    c_fixed = layer_c_portfolios(panel_fixed, bench)
    c_market = layer_c_portfolios(panel_market, bench)
    save_layer("layerC_fixed_pool", c_fixed)
    save_layer("layerC_market", c_market)

    # Top/Bottom 50（全市场）
    tb = layer_a_top_bottom(panel_market, name_col="code", n=50)
    tb["market_top"] = _attach_market_names(tb["top"], panel_market)
    tb["market_bottom"] = _attach_market_names(tb["bottom"], panel_market)
    save_layer("top_bottom_50", tb)

    # Report
    out = render_report(a_fixed, a_market, b_fixed, c_fixed, c_market, tb, provenance)
    print(f"report: {out}")
    return {"a_fixed": a_fixed, "a_market": a_market, "report": str(out), "bench": bench}


def _attach_market_names(records: list[dict], panel: pd.DataFrame) -> list[dict]:
    """全市场日线无名称列，从 market_names.csv（spot 清单）关联名称。"""
    name_map = {}
    try:
        nm = pd.read_csv(STUDY_DIR / "market_names.csv", dtype={"code": str})
        name_map = dict(zip(nm["code"], nm["name"]))
    except Exception:
        pass
    for r in records:
        r["name"] = name_map.get(r["code"], "")
    return records


def main() -> int:
    panel_fixed = run_fixed_pool()
    panel_market = run_market_panel()
    run_analysis(panel_fixed, panel_market)
    return 0


if __name__ == "__main__":
    sys.exit(main())
