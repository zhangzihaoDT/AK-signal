from __future__ import annotations

import pandas as pd

from src.sw_industry_rps.metrics import (
    calc_returns,
    calc_rps_cross_section,
    calc_delta_rps15,
    calc_delta_rps15_n,
    calc_acceleration_fields,
    compute_all_metrics,
    cross_industry_direction,
    _industry_direction_state,
)


def test_calc_returns_5_10_15(sample_industry_data):
    result = calc_returns(sample_industry_data)
    industry = result[result["industry_code"] == "801016.SI"].sort_values("trade_date")
    close_series = industry["close"].values
    for i in range(5, len(close_series)):
        expected = close_series[i] / close_series[i - 5] - 1
        assert abs(industry["return_5"].iloc[i] - expected) < 1e-10
    assert "return_10" in result.columns
    assert "return_15" in result.columns


def test_calc_returns_empty():
    df = pd.DataFrame(columns=["trade_date", "close", "industry_code"])
    result = calc_returns(df)
    assert "return_5" in result.columns
    assert result.empty


def test_returns_trading_day_window(sample_industry_data):
    result = calc_returns(sample_industry_data)
    industry = result[result["industry_code"] == "801016.SI"].sort_values("trade_date")
    for i in range(5, len(industry)):
        assert pd.notna(industry["return_5"].iloc[i])
    assert pd.isna(industry["return_5"].iloc[4])


def test_rps_cross_section_rank(sample_industry_data):
    with_returns = calc_returns(sample_industry_data)
    latest = with_returns.groupby("industry_code").last().reset_index()
    dup = pd.concat([latest, latest])
    result = calc_rps_cross_section(
        dup, date_col="trade_date", code_col="industry_code",
        return_cols=["return_5", "return_10", "return_15"],
    )
    for col in ["RPS5", "RPS10", "RPS15"]:
        assert col in result.columns
        vals = result[result["trade_date"] == result["trade_date"].iloc[0]][col]
        assert vals.between(0, 100).all()


def test_rps_0_to_100(sample_industry_data):
    with_returns = calc_returns(sample_industry_data)
    result = calc_rps_cross_section(with_returns)
    for col in ["RPS5", "RPS10", "RPS15"]:
        vals = result[col].dropna()
        assert (vals >= 0).all()
        assert (vals <= 100).all()


def test_rps_higher_return_higher_rank(sample_industry_data):
    with_returns = calc_returns(sample_industry_data)
    latest = with_returns.groupby("trade_date").last().reset_index()
    one_date = latest[latest["trade_date"] == latest["trade_date"].iloc[0]]
    one_date = one_date.sort_values("return_15")
    result = calc_rps_cross_section(one_date)
    result = result.sort_values("return_15")
    rps_vals = result["RPS15"].values
    for i in range(1, len(rps_vals)):
        if pd.notna(rps_vals[i]) and pd.notna(rps_vals[i - 1]):
            assert rps_vals[i] >= rps_vals[i - 1]


def test_rps_ties_average_rank(sample_industry_data):
    with_returns = calc_returns(sample_industry_data)
    latest = with_returns.groupby("trade_date").last().reset_index()
    one_date = latest[latest["trade_date"] == latest["trade_date"].iloc[0]]
    dup = pd.concat([one_date, one_date.iloc[:1]])
    dup = dup.reset_index(drop=True)
    dup["return_15"] = dup["return_15"].fillna(dup["return_15"].iloc[0])
    result = calc_rps_cross_section(dup)
    tied = result[result["industry_code"] == result["industry_code"].iloc[0]]
    assert tied["RPS15"].nunique() <= 1


def test_missing_values_not_affect_others(sample_industry_data):
    with_returns = calc_returns(sample_industry_data)
    first_code = with_returns["industry_code"].unique()[0]
    mask = with_returns["industry_code"] == first_code
    with_returns.loc[mask, "return_15"] = pd.NA
    result = calc_rps_cross_section(with_returns)
    other = result[result["industry_code"] != first_code]
    assert other["RPS15"].notna().any()


def test_delta_rps15(sample_industry_data):
    with_returns = calc_returns(sample_industry_data)
    with_rps = calc_rps_cross_section(with_returns)
    result = calc_delta_rps15(with_rps)
    assert "delta_rps15" in result.columns
    industry = result[result["industry_code"] == "801016.SI"].sort_values("trade_date")
    expected = industry["RPS15"].diff().values
    actual = industry["delta_rps15"].values
    matches = sum(
        1 for i in range(1, len(expected))
        if pd.notna(expected[i]) and pd.notna(actual[i])
        and abs(expected[i] - actual[i]) < 0.01
    )
    assert matches > 0


def test_acceleration_fields(sample_industry_data):
    with_returns = calc_returns(sample_industry_data)
    with_rps = calc_rps_cross_section(with_returns)
    result = calc_acceleration_fields(with_rps)
    assert "short_term_acceleration" in result.columns
    assert "medium_term_acceleration" in result.columns
    valid = result.dropna(subset=["RPS5", "RPS15"])
    if not valid.empty:
        row = valid.iloc[0]
        assert abs(row["short_term_acceleration"] - (row["RPS5"] - row["RPS15"])) < 0.01


def test_compute_all_metrics(sample_industry_data):
    result = compute_all_metrics(sample_industry_data)
    expected_cols = [
        "return_5", "return_10", "return_15",
        "RPS5", "RPS10", "RPS15",
        "delta_rps15",
        "short_term_acceleration", "medium_term_acceleration",
    ]
    for col in expected_cols:
        assert col in result.columns, f"missing {col}"
    assert result["trade_date"].is_monotonic_increasing
    assert not result.empty


def test_compute_all_metrics_includes_today_and_velocity(sample_industry_data):
    result = compute_all_metrics(sample_industry_data)
    assert "return_1" in result.columns
    assert "RPS1" in result.columns
    assert "delta_rps15_5d" in result.columns
    # RPS1 是 0-100 百分位
    vals = result["RPS1"].dropna()
    if not vals.empty:
        assert (vals >= 0).all() and (vals <= 100).all()
    # Δ5 需要 RPS15 有 5 个交易日前史；历史足够时存在
    if result["RPS15"].notna().sum() > 5:
        assert result["delta_rps15_5d"].notna().any()


def test_delta_rps15_5d_window(sample_industry_data):
    with_returns = calc_returns(sample_industry_data)
    with_rps = calc_rps_cross_section(with_returns)
    result = calc_delta_rps15_n(with_rps, velocity_window=5)
    assert "delta_rps15_5d" in result.columns
    industry = result[result["industry_code"] == "801016.SI"].sort_values("trade_date")
    expected = industry["RPS15"].diff(5).values
    actual = industry["delta_rps15_5d"].values
    matches = sum(
        1 for i in range(5, len(expected))
        if pd.notna(expected[i]) and pd.notna(actual[i])
        and abs(expected[i] - actual[i]) < 0.01
    )
    assert matches > 0


# --- 一级产业方向聚合（Layer② 第一问） ---


def _make_snapshot_df():
    return pd.DataFrame([
        {"industry_code": "801016.SI", "industry_name": "种植业", "parent_industry": "农林牧渔",
         "RPS15": 90.0, "RPS1": 80.0, "delta_rps15_5d": 5.0, "strength_level": "强势"},
        {"industry_code": "801015.SI", "industry_name": "渔业", "parent_industry": "农林牧渔",
         "RPS15": 30.0, "RPS1": 20.0, "delta_rps15_5d": -3.0, "strength_level": "弱势"},
        {"industry_code": "801081.SI", "industry_name": "半导体", "parent_industry": "电子",
         "RPS15": 85.0, "RPS1": 75.0, "delta_rps15_5d": 10.0, "strength_level": "观察"},
        {"industry_code": "801082.SI", "industry_name": "元件", "parent_industry": "电子",
         "RPS15": 10.0, "RPS1": 5.0, "delta_rps15_5d": -8.0, "strength_level": "中性"},
    ])


def test_cross_industry_direction_aggregates_by_parent():
    result = cross_industry_direction(_make_snapshot_df())
    assert len(result) == 2
    by_name = {r["parent_industry"]: r for r in result}
    assert "农林牧渔" in by_name and "电子" in by_name
    nongye = by_name["农林牧渔"]
    assert nongye["industry_count"] == 2
    # median of 90/30 = 60
    assert nongye["median_rps15"] == 60.0
    # median of 5/-3 = 1.0
    assert nongye["median_delta_rps15_5d"] == 1.0
    # active: 强势 -> 1 of 2
    assert nongye["active_count"] == 1
    assert nongye["active_ratio"] == 0.5
    # representative = 最高 RPS15 的二级行业
    assert nongye["representative_industry"] == "种植业"


def test_cross_industry_direction_sorted_by_median():
    result = cross_industry_direction(_make_snapshot_df())
    # 电子 median (85+10)/2 = 47.5，农林牧渔 median 60 -> 农林牧渔 在前
    medians = [r["median_rps15"] for r in result]
    assert medians == sorted(medians, reverse=True)


def test_cross_industry_direction_empty():
    assert cross_industry_direction(pd.DataFrame()) == []
    assert cross_industry_direction(pd.DataFrame({"a": [1]})) == []


def test_cross_industry_direction_missing_optional_cols():
    df = _make_snapshot_df().drop(columns=["RPS1", "delta_rps15_5d", "strength_level"])
    result = cross_industry_direction(df)
    assert len(result) == 2
    assert all(r["median_rps1"] is None for r in result)
    assert all(r["median_delta_rps15_5d"] is None for r in result)
    assert all(r["active_count"] == 0 for r in result)


def test_industry_direction_state():
    assert _industry_direction_state(70.0, 0.6) == "强势上行"
    assert _industry_direction_state(70.0, 0.2) == "加速"
    assert _industry_direction_state(50.0, 0.5) == "横盘"
    assert _industry_direction_state(30.0, 0.5) == "弱势下行"
    assert _industry_direction_state(None, 0.5) == "—"
