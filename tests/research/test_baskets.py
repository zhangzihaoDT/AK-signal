"""Research Basket 计算口径测试。"""

from __future__ import annotations

import pandas as pd

from src.research.baskets.calculator import (
    build_nav, contribution_analysis, metrics, quarterly_rebalanced_nav, rolling_analysis,
)
from src.research.baskets.config import basket_config_hash, expand_constituents, load_baskets


def _prices() -> pd.DataFrame:
    return pd.DataFrame([
        {"date": "2025-01-01", "group": "a", "symbol": "A", "close": 100},
        {"date": "2025-01-02", "group": "a", "symbol": "A", "close": 110},
        {"date": "2025-01-01", "group": "b", "symbol": "B", "close": 100},
        {"date": "2025-01-02", "group": "b", "symbol": "B", "close": 90},
    ])


def test_group_equal_is_not_asset_count_weighted():
    benchmark = pd.DataFrame({"date": ["2025-01-01", "2025-01-02"], "close": [100, 100]})
    nav = build_nav(_prices(), benchmark, group_order=["a", "b"], weighting_method="group_equal")
    assert nav.loc[pd.Timestamp("2025-01-02"), "basket"] == 100


def test_metrics_has_excess_and_drawdown():
    nav = pd.DataFrame(
        {"basket": [100.0, 110.0, 90.0], "benchmark": [100.0, 105.0, 105.0]},
        index=pd.to_datetime(["2025-01-01", "2025-01-02", "2025-01-03"]),
    )
    result = metrics(nav)
    assert result["return_pct"] == -10.0
    assert result["benchmark_return_pct"] == 5.0
    assert result["max_drawdown_pct"] < -18.0


def test_basket_config_and_constituents_are_explicit():
    baskets = load_baskets()
    assert {"scania_china_watch", "china_auto_global_ex_oem", "ai_capex"} <= set(baskets)
    assets = expand_constituents(baskets["china_auto_global_ex_oem"])
    assert all(a["group"] != "oem_global" for a in assets)
    assert basket_config_hash(baskets["ai_capex"])


def test_rolling_metrics_and_contributions():
    benchmark = pd.DataFrame({"date": ["2025-01-01", "2025-01-02", "2025-01-03"], "close": [100, 100, 100]})
    nav = build_nav(_prices(), benchmark, group_order=["a", "b"])
    rolling = rolling_analysis(nav, windows=(2,))
    assert "excess_return_2d_pct" in rolling.columns
    assert "sharpe_2d" in rolling.columns
    assert "drawdown_2d_pct" in rolling.columns
    contributions = contribution_analysis(_prices(), ["a", "b"])
    assert round(float(contributions["contribution_pct_points"].sum()), 4) == 0.0
    assert int(contributions["top1"].sum()) == 1
    assert int(contributions["top3"].sum()) == 2


def test_quarterly_rebalance_is_not_all_zero():
    prices = pd.concat([
        _prices(),
        pd.DataFrame([
            {"date": "2025-04-01", "group": "a", "symbol": "A", "close": 120},
            {"date": "2025-04-01", "group": "b", "symbol": "B", "close": 80},
            {"date": "2025-04-02", "group": "a", "symbol": "A", "close": 132},
            {"date": "2025-04-02", "group": "b", "symbol": "B", "close": 88},
        ]),
    ], ignore_index=True)
    benchmark = pd.DataFrame({"date": ["2025-01-01", "2025-01-02", "2025-04-01", "2025-04-02"], "close": [100, 100, 100, 100]})
    nav = quarterly_rebalanced_nav(prices, benchmark, group_order=["a", "b"])
    assert nav["basket"].iloc[-1] != nav["basket"].iloc[0]
