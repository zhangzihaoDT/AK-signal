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


def test_expand_constituents_propagates_evidence_stage(tmp_path):
    universe = tmp_path / "universe.yaml"
    universe.write_text("""
observation_groups:
  demo:
    groups:
      g1:
        label: "G1"
        listed_assets:
          - {symbol: "A", name: "A公司", market: CN, evidence_stage: "ORDER"}
          - {symbol: "B", name: "B公司", market: CN, evidence_stage: "SMALL_BATCH", capacity_stage: "BUILDING"}
          - {symbol: "C", name: "C公司", market: CN}
""", encoding="utf-8")
    basket = {"key": "demo", "source": {"type": "observation_group", "key": "demo"}}
    assets = expand_constituents(basket, universe_path=universe)
    by_symbol = {a["symbol"]: a for a in assets}
    assert by_symbol["A"]["evidence_stage"] == "ORDER"
    assert by_symbol["B"]["evidence_stage"] == "SMALL_BATCH"
    assert by_symbol["B"]["capacity_stage"] == "BUILDING"
    assert by_symbol["B"]["revenue_evidence"] == ""
    assert by_symbol["C"]["evidence_stage"] == ""


_EVIDENCE_STAGES = {"VALIDATION", "DESIGN_WIN", "ORDER", "SMALL_BATCH", "MASS_PRODUCTION"}
_REVENUE_EVIDENCE = {"NONE", "CONFIRMED", ""}
_CAPACITY_STAGES = {"NONE", "PLANNING", "BUILDING", "RAMPING", ""}


def test_evidence_stage_follows_canonical_enum():
    """三个正交字段必须是固定枚举：evidence_stage 只表商业化阶段，收入/产能维度独立。"""
    baskets = load_baskets()
    bad: list[str] = []
    for key in ("auto_tier1_ai_infra", "auto_tier1_embodied"):
        for asset in expand_constituents(baskets[key]):
            if asset["evidence_stage"] not in _EVIDENCE_STAGES:
                bad.append(f"{key}/{asset['symbol']} evidence_stage={asset['evidence_stage']!r}")
            if asset["revenue_evidence"] not in _REVENUE_EVIDENCE:
                bad.append(f"{key}/{asset['symbol']} revenue_evidence={asset['revenue_evidence']!r}")
            if asset["capacity_stage"] not in _CAPACITY_STAGES:
                bad.append(f"{key}/{asset['symbol']} capacity_stage={asset['capacity_stage']!r}")
    assert not bad, bad


def test_cross_basket_overlap_detects_common_constituent():
    from src.research.baskets.report import cross_basket_overlap

    results = [
        {"basket": {"key": "b1"}, "constituents": pd.DataFrame([
            {"group": "a", "symbol": "X", "name": "X公司", "evidence_stage": "ORDER"},
            {"group": "a", "symbol": "Y", "name": "Y公司", "evidence_stage": "REVENUE"},
        ]), "contributions": pd.DataFrame([
            {"symbol": "X", "contribution_pct_points": 2.0},
            {"symbol": "Y", "contribution_pct_points": 1.0},
        ])},
        {"basket": {"key": "b2"}, "constituents": pd.DataFrame([
            {"group": "b", "symbol": "X", "name": "X公司", "evidence_stage": "REVENUE"},
            {"group": "b", "symbol": "Z", "name": "Z公司", "evidence_stage": "ORDER"},
        ]), "contributions": pd.DataFrame([
            {"symbol": "X", "contribution_pct_points": 3.0},
            {"symbol": "Z", "contribution_pct_points": 0.5},
        ])},
    ]
    overlap = cross_basket_overlap(results)
    assert list(overlap["symbol"]) == ["X"]
    assert overlap.iloc[0]["basket_a"] == "b1"
    assert overlap.iloc[0]["basket_b"] == "b2"
    assert overlap.iloc[0]["evidence_stage_a"] == "ORDER"
    assert overlap.iloc[0]["evidence_stage_b"] == "REVENUE"
    assert overlap.iloc[0]["contribution_pct_a"] == 2.0
    assert overlap.iloc[0]["contribution_pct_b"] == 3.0


def test_stage_log_matches_current_config():
    """genesis 快照必须等于当前 config 阶段（每次 evidence-driven update 都要同步 log）。"""
    from src.research.baskets.stage_log import config_matches_log

    ok, diffs = config_matches_log()
    assert ok, "\n".join(diffs)


def test_stage_log_apply_entries_and_chain_guard():
    from src.research.baskets.stage_log import apply_stage_log

    log = {
        "genesis": {"b": [{"symbol": "X", "evidence_stage": "ORDER"}]},
        "entries": [
            {"date": "2026-09-01", "basket": "b", "symbol": "X",
             "field": "evidence_stage", "from": "ORDER", "to": "SMALL_BATCH", "evidence": "…"},
        ],
    }
    state = apply_stage_log(log)
    assert state[("b", "X")]["evidence_stage"] == "SMALL_BATCH"

    bad = {
        "genesis": {"b": [{"symbol": "X", "evidence_stage": "ORDER"}]},
        "entries": [
            {"date": "2026-09-01", "basket": "b", "symbol": "X",
             "field": "evidence_stage", "from": "VALIDATION", "to": "SMALL_BATCH", "evidence": "…"},
        ],
    }
    try:
        apply_stage_log(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("expected chain mismatch error")


def test_stage_log_detects_config_drift(tmp_path):
    import yaml

    from src.common.paths import stock_universe_path
    from src.research.baskets.config import load_baskets
    from src.research.baskets.stage_log import config_matches_log

    baskets = load_baskets()
    universe = tmp_path / "universe.yaml"
    raw = yaml.safe_load(stock_universe_path().read_text())
    for asset in raw["observation_groups"]["auto_tier1_embodied"]["groups"]["execution_hardware"]["listed_assets"]:
        if asset["symbol"] == "603009":
            asset["evidence_stage"] = "MASS_PRODUCTION"  # 改了 config 但没追加 log → 应报 drift
    universe.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")

    ok, diffs = config_matches_log(baskets, universe_path=universe)
    assert not ok
    assert any("603009" in d and "evidence_stage" in d for d in diffs)


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
