from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.market_facts import etf_observation as facts

EQUITY = {"A", "B", "C"}
ALL_CODES = ["A", "B", "C", "M"]


@pytest.fixture(autouse=True)
def published_classification(monkeypatch):
    def item(value):
        return dict(value=value, status='pending' if value == '待核实' else 'inferred',
                    as_of='2020-01-01', scope='mandate', source_ids=['fixture'], reason='published fixture')
    document = {'classification_version': 'fixture-v1', 'published_at': '2020-01-01T00:00:00+00:00',
                'records': [{'fund_code': c, 'investment_market': item('待核实' if c == 'A' else '境内'),
                             'investment_direction': item('货币' if c == 'M' else '科技')} for c in ALL_CODES]}
    monkeypatch.setattr(facts.classification_facts, 'load_input',
                        lambda: {'document': document, 'sha256': 'fixture', 'reason': ''})
    return document


def _combined_df() -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-05", periods=40)
    paths = {
        "A": np.concatenate([np.linspace(100, 101, 25), np.linspace(101, 120, 15)]),
        "B": np.concatenate([np.linspace(100, 118, 25), np.linspace(118, 118, 15)]),
        "C": np.linspace(100, 100, 40),
        "M": np.linspace(1.0, 1.0, 40),
    }
    amounts = {
        "A": np.linspace(1e8, 5e8, 40),
        "B": np.linspace(5e8, 6e8, 40),
        "C": np.linspace(1e7, 1e7, 40),
        "M": np.linspace(2e9, 2e9, 40),
    }
    frames = []
    for code in ALL_CODES:
        frame = pd.DataFrame({"date": dates, "close": paths[code], "amount": amounts[code]})
        frame["fund_code"] = code
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def _master_df() -> pd.DataFrame:
    return pd.DataFrame({
        "fund_code": ALL_CODES,
        "fund_name": ["测试AIETF", "测试电力ETF", "测试ETF", "测试货币ETF"],
        "asset_bucket": ["industry", "industry", "industry", "money_market"],
    })


def _ref_rps(combined: pd.DataFrame, window: int, codes: list[str]) -> pd.Series:
    pivot = combined.pivot_table(index="date", columns="fund_code", values="close", aggfunc="last").sort_index()
    returns = pivot[codes] / pivot[codes].shift(window) - 1
    return (returns.rank(axis=1, pct=True, ascending=True) * 100).iloc[-1]


def test_observation_columns_and_classification_gaps_are_explicit():
    master = _master_df()
    master["tracking_index"] = ["", "电力指数", "", ""]
    master["tracking_index_code"] = ["", "931991", "", ""]
    result = facts.compute_rotation_metrics(_combined_df(), master).set_index("fund_code")
    assert result.loc["A", "tracking_index_status"] == "missing"
    assert result.loc["B", "tracking_index_status"] == "present_unverified"
    assert result.loc["A", "classification_status"] == "unclassified"
    assert result.loc["A", "investment_market"] == "待核实"
    assert result.loc["A", "investment_direction"] == "科技"
    for column in ("rps1", "rps5", "delta_rps15", "liquidity"):
        assert pd.to_numeric(result[column], errors="coerce").notna().any()


@pytest.mark.parametrize("window,column", [(1, "rps1"), (5, "rps5")])
def test_short_window_rps_matches_cross_section(window, column):
    result = facts.compute_rotation_metrics(_combined_df(), _master_df()).set_index("fund_code")
    reference = _ref_rps(_combined_df(), window, sorted(EQUITY))
    for code in sorted(EQUITY):
        assert result.loc[code, column] == pytest.approx(reference[code], abs=1e-6)
    assert pd.isna(result.loc["M", column])


def test_delta_rps15_and_liquidity_match_their_existing_definitions():
    combined = _combined_df()
    result = facts.compute_rotation_metrics(combined, _master_df()).set_index("fund_code")
    closes = combined.pivot_table(index="date", columns="fund_code", values="close", aggfunc="last").sort_index()
    equity_returns = closes[sorted(EQUITY)] / closes[sorted(EQUITY)].shift(15) - 1
    rps15 = equity_returns.rank(axis=1, pct=True, ascending=True) * 100
    delta = rps15.iloc[-1] - rps15.shift(5).iloc[-1]
    for code in sorted(EQUITY):
        assert result.loc[code, "delta_rps15"] == pytest.approx(delta[code], abs=1e-6)
    amount = combined.pivot_table(index="date", columns="fund_code", values="amount", aggfunc="last").sort_index()
    expected_liquidity = amount.iloc[-5:].mean().rank(pct=True, ascending=True) * 100
    for code in ALL_CODES:
        assert result.loc[code, "liquidity"] == pytest.approx(expected_liquidity[code], abs=1e-6)


def test_defensive_bucket_excluded_from_rps_but_retains_liquidity():
    result = facts.compute_rotation_metrics(_combined_df(), _master_df()).set_index("fund_code")
    assert pd.isna(result.loc["M", "rps1"])
    assert pd.isna(result.loc["M", "rps5"])
    assert pd.isna(result.loc["M", "delta_rps15"])
    assert pd.notna(result.loc["M", "liquidity"])


def test_cross_asset_direction_uses_published_classification(published_classification):
    published_classification["records"][0]["investment_market"].update(value="境内", status="inferred")
    master = _master_df()
    master["fund_name"] = ["A股科技ETF", "A股AIETF", "A股芯片ETF", "A股货币ETF"]
    result = facts.compute_rotation_metrics(_combined_df(), master)
    directions = {row["direction"]: row for row in facts.cross_asset_direction(result)}
    assert set(directions) == {"境内 · 科技", "境内 · 货币"}
    equity = result[result["fund_code"].isin(sorted(EQUITY))]
    assert directions["境内 · 科技"]["median_rps5"] == pytest.approx(round(float(equity["rps5"].median()), 1))
    assert directions["境内 · 科技"]["median_rps1"] == pytest.approx(round(float(equity["rps1"].median()), 1))
    assert directions["境内 · 货币"]["median_rps5"] is None


def test_single_day_anomalies_remain_flagged_and_excluded_from_rankings():
    dates = pd.bdate_range("2026-01-05", periods=40)
    frames = []
    for code, close in [
        ("A", np.linspace(100, 110, 40)),
        ("B", np.concatenate([np.linspace(100, 105, 36), [105, 52.5, 53, 53.5]])),
        ("C", np.concatenate([np.linspace(100, 100, 39), [50]])),
    ]:
        frames.append(pd.DataFrame({"date": dates, "close": close, "amount": np.linspace(1e8, 2e8, 40), "fund_code": code}))
    master = pd.DataFrame({"fund_code": ["A", "B", "C"], "fund_name": ["AETF", "BETF", "CETF"],
                           "asset_bucket": ["industry"] * 3})
    result = facts.compute_rotation_metrics(pd.concat(frames, ignore_index=True), master).set_index("fund_code")
    assert result.loc["B", "data_quality_flag"] == "corporate_action"
    assert result.loc["C", "data_quality_flag"] == "corporate_action"
    assert result.loc["A", "data_quality_flag"] == ""
    assert pd.isna(result.loc["C", "rps15"]) and pd.isna(result.loc["B", "rps15"])
    assert pd.notna(result.loc["B", "rps1"]) and pd.isna(result.loc["C", "rps1"])
