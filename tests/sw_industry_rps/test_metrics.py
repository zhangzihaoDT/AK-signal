from __future__ import annotations

import pandas as pd

from src.sw_industry_rps.metrics import (
    calc_returns,
    calc_rps_cross_section,
    calc_delta_rps15,
    calc_acceleration_fields,
    compute_all_metrics,
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
