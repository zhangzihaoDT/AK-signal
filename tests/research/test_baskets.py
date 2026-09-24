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


def test_nav_benchmark_anchored_at_common_start():
    """基准若含更早历史，也必须与篮子一起从共同首个交易日起等于 100（不能从 2002 归一化）。"""
    prices = pd.DataFrame([
        {"date": "2025-01-02", "group": "a", "symbol": "A", "close": 100},
        {"date": "2025-01-03", "group": "a", "symbol": "A", "close": 110},
    ])
    benchmark = pd.DataFrame({
        "date": ["2002-01-01", "2025-01-02", "2025-01-03"],
        "close": [50.0, 4000.0, 4400.0],
    })
    nav = build_nav(prices, benchmark, group_order=["a"])
    assert nav["basket"].iloc[0] == 100.0
    assert nav["benchmark"].iloc[0] == 100.0
    assert round(nav["benchmark"].iloc[-1], 4) == 110.0


def test_last_complete_trading_day_cutoff():
    from datetime import datetime

    from src.research.baskets.calculator import last_complete_trading_day

    dates = ["2026-09-23", "2026-09-24"]
    # 盘中：当日未收盘 → 退回上一交易日
    assert last_complete_trading_day(dates, now=datetime(2026, 9, 24, 11, 0)) == pd.Timestamp("2026-09-23")
    # 收盘后：当日完整
    assert last_complete_trading_day(dates, now=datetime(2026, 9, 24, 16, 0)) == pd.Timestamp("2026-09-24")
    # 次日：max < today → 完整
    assert last_complete_trading_day(dates, now=datetime(2026, 9, 25, 9, 0)) == pd.Timestamp("2026-09-24")
    # 只有当日一根、且未收盘 → 无更早日期
    assert last_complete_trading_day(["2026-09-24"], now=datetime(2026, 9, 24, 11, 0)) is None


def test_window_truncated_to_last_complete_day(tmp_path, monkeypatch):
    """盘中运行：当日未收盘 bar 被排除，nav 结束于上一完整交易日。"""
    import src.research.baskets.calculator as calc
    from src.research.baskets.calculator import calculate_basket

    dates = pd.to_datetime(["2026-09-23", "2026-09-24"])
    prices = pd.DataFrame({"group": "a", "symbol": "A", "name": "A公司",
                           "date": dates, "close": [100.0, 120.0]})
    price_path = tmp_path / "demo_prices.parquet"
    prices.to_parquet(price_path, index=False)
    pd.DataFrame({"date": dates, "close": [100.0, 110.0]}).to_csv(
        tmp_path / "_benchmark_sh000300.csv", index=False)
    universe = tmp_path / "universe.yaml"
    universe.write_text("""
themes:
  demo:
    tiers:
      - key: a
        label: "A组"
        assets: [{symbol: "A", name: "A公司", market: CN}]
""", encoding="utf-8")
    baskets = tmp_path / "baskets.yaml"
    baskets.write_text("""
baskets:
  demo:
    label: "Demo"
    source: {type: theme, key: demo, include_groups: [a]}
    weighting: {method: group_equal, rebalance: buy_and_hold}
    benchmark: sh000300
""", encoding="utf-8")

    monkeypatch.setattr(calc, "_price_path", lambda basket: price_path)
    monkeypatch.setattr(calc, "raw_dir", lambda: tmp_path)

    from datetime import datetime
    result = calculate_basket(
        "demo", basket_config=baskets, universe_path=universe,
        as_of_now=datetime(2026, 9, 24, 11, 0))
    assert result["nav"].index.max() == pd.Timestamp("2026-09-23")
    assert result["quarterly_nav"].index.max() == pd.Timestamp("2026-09-23")


def test_quarterly_window_independent_of_main(tmp_path, monkeypatch):
    """quarterly_start_date 独立于买入持有窗口：nav 用 --start，季度净值可取更早起点。"""
    import src.research.baskets.calculator as calc
    from src.research.baskets.calculator import calculate_basket

    dates = pd.bdate_range("2024-08-01", "2024-12-31")
    prices = pd.DataFrame({
        "group": "a", "symbol": "A", "name": "A公司",
        "date": dates, "close": [100.0 + i for i in range(len(dates))],
    })
    price_path = tmp_path / "demo_prices.parquet"
    prices.to_parquet(price_path, index=False)

    bench = pd.DataFrame({"date": dates, "close": [100.0 + 0.5 * i for i in range(len(dates))]})
    bench.to_csv(tmp_path / "_benchmark_sh000300.csv", index=False)

    universe = tmp_path / "universe.yaml"
    universe.write_text("""
themes:
  demo:
    tiers:
      - key: a
        label: "A组"
        assets: [{symbol: "A", name: "A公司", market: CN}]
""", encoding="utf-8")
    baskets = tmp_path / "baskets.yaml"
    baskets.write_text("""
baskets:
  demo:
    label: "Demo"
    source: {type: theme, key: demo, include_groups: [a]}
    weighting: {method: group_equal, rebalance: buy_and_hold}
    benchmark: sh000300
""", encoding="utf-8")

    monkeypatch.setattr(calc, "_price_path", lambda basket: price_path)
    monkeypatch.setattr(calc, "raw_dir", lambda: tmp_path)

    result = calculate_basket(
        "demo", start_date="2024-10-01", quarterly_start_date="2024-08-01",
        basket_config=baskets, universe_path=universe)
    assert result["nav"].index.min() >= pd.Timestamp("2024-10-01")
    assert result["quarterly_nav"].index.min() == dates[0]
    assert result["quarterly_nav"]["basket"].iloc[0] == 100.0
    assert result["quarterly_nav"]["benchmark"].iloc[0] == 100.0


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
    assert {"scania_china_watch", "china_auto_global", "china_auto_global_ex_oem",
            "ai_capex", "memory_and_storage"} <= set(baskets)
    assets = expand_constituents(baskets["china_auto_global_ex_oem"])
    assert all(a["group"] != "oem_global" for a in assets)
    assert any(a["group"] == "oem_global" for a in expand_constituents(baskets["china_auto_global"]))
    mem = expand_constituents(baskets["memory_and_storage"])
    assert {a["group"] for a in mem} == {"memory_and_storage"}
    assert {"603986", "688449", "301308", "688525", "688766"} <= {a["symbol"] for a in mem}
    assert basket_config_hash(baskets["ai_capex"])


def test_theme_baskets_exclude_etf_tiers():
    """主题篮子只含股票 tier，不应混入 ETF 产品层（否则 fetch 跳过 ETF 后 run 会缺组）。"""
    etf_tiers = {"theme_etf", "sub_industry_etf", "watch_etf"}
    for key in ("ai_capex", "memory_and_storage", "china_auto_global", "china_auto_global_ex_oem"):
        groups = {a["group"] for a in expand_constituents(load_baskets()[key])}
        assert groups and not (groups & etf_tiers), (key, groups)


def test_fetch_source_keys_dedup():
    """按 (type, key) 去重：多篮子共享同一来源只抓一次。"""
    from src.research.baskets.fetch import source_keys

    keys = source_keys()
    assert len(keys) == len(set(keys))
    assert sum(1 for k in keys if k == ("theme", "ai_infrastructure")) == 1
    assert ("theme", "ai_infrastructure") in keys
    assert ("theme", "china_auto_global") in keys
    assert ("observation_group", "scania_china_watch") in keys


def test_fetch_collect_skips_etf_tiers():
    """collect_assets 默认跳过 ETF tier（纯配置读取，不联网）。"""
    from src.research.baskets.fetch import DEFAULT_SKIP_TIERS, _collect_assets, _load_section

    for key in ("ai_infrastructure", "china_auto_global"):
        section = _load_section("theme", key)
        assets = _collect_assets(section, "theme", DEFAULT_SKIP_TIERS)
        assert assets
        assert all(a["group"] not in DEFAULT_SKIP_TIERS for a in assets)


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

    from src.common.paths import research_observations_path
    from src.research.baskets.config import load_baskets
    from src.research.baskets.stage_log import config_matches_log

    baskets = load_baskets()
    universe = tmp_path / "research_observations.yaml"
    raw = yaml.safe_load(research_observations_path().read_text())
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
