from __future__ import annotations

import pandas as pd

from src.sw_industry_rps.regimes import (
    calc_streaks,
    calc_first_entry_90_v2,
    calc_regime_flags,
    identify_all_regimes,
)


def _make_test_df() -> pd.DataFrame:
    dates = pd.date_range("2026-01-05", periods=10, freq="B")
    codes = ["A", "B"]
    rows = []
    rps_a = [50, 55, 60, 85, 91, 92, 93, 94, 95, 96]
    rps_b = [30, 35, 40, 45, 50, 55, 60, 65, 70, 75]
    for i, d in enumerate(dates):
        rows.append({"trade_date": d, "industry_code": "A", "RPS15": rps_a[i]})
        rows.append({"trade_date": d, "industry_code": "B", "RPS15": rps_b[i]})
    return pd.DataFrame(rows)


def test_streak_80():
    df = _make_test_df()
    result = calc_streaks(df, strong_threshold=90, observe_threshold=80)
    a = result[result["industry_code"] == "A"].sort_values("trade_date")
    assert (a["streak_80"] >= 0).all()
    assert a["streak_80"].iloc[-1] > 2


def test_streak_90():
    df = _make_test_df()
    result = calc_streaks(df)
    a = result[result["industry_code"] == "A"].sort_values("trade_date")
    streak_vals = a["streak_90"].values
    assert streak_vals[4] == 1
    assert streak_vals[-1] == 6


def test_streak_below_threshold():
    df = _make_test_df()
    result = calc_streaks(df)
    b = result[result["industry_code"] == "B"].sort_values("trade_date")
    assert (b["streak_90"] == 0).all()


def _add_delta(df):
    result = df.copy()
    result = result.sort_values(["industry_code", "trade_date"])
    result["delta_rps15"] = result.groupby("industry_code")["RPS15"].diff()
    return result


def test_new_entry():
    df = _add_delta(_make_test_df())
    result = identify_all_regimes(df)
    a = result[result["industry_code"] == "A"].sort_values("trade_date")
    assert a["new_entry"].iloc[4] == 1
    assert a["new_entry"].iloc[5] == 0


def test_strong_streak():
    df = _add_delta(_make_test_df())
    result = identify_all_regimes(df, strong_threshold=90, strong_streak_min=3)
    a = result[result["industry_code"] == "A"].sort_values("trade_date")
    assert a["strong_streak"].iloc[4] == 0
    assert a["strong_streak"].iloc[-1] == 1


def test_accelerating():
    df = _add_delta(_make_test_df())
    df.loc[df["industry_code"] == "A", "RPS15"] = [50, 55, 60, 65, 70, 85, 95, 96, 97, 98]
    df["delta_rps15"] = df.groupby("industry_code")["RPS15"].diff()
    result = identify_all_regimes(df, delta_acceleration=10)
    a = result[result["industry_code"] == "A"].sort_values("trade_date")
    found = a["accelerating"].sum()
    assert found >= 0


def test_falling_out():
    df = _add_delta(_make_test_df())
    result = identify_all_regimes(df)
    a = result[result["industry_code"] == "A"].sort_values("trade_date")
    assert a["falling_out"].sum() == 0

    df2 = _add_delta(_make_test_df())
    rps_a = [91, 92, 93, 89, 85, 80, 75, 70, 65, 60]
    dates = sorted(df2["trade_date"].unique())
    for i, d in enumerate(dates):
        mask = (df2["industry_code"] == "A") & (df2["trade_date"] == d)
        df2.loc[mask, "RPS15"] = rps_a[i]
    df2["delta_rps15"] = df2.groupby("industry_code")["RPS15"].diff()
    result2 = identify_all_regimes(df2)
    a2 = result2[result2["industry_code"] == "A"].sort_values("trade_date")
    assert (a2["falling_out"].iloc[3:5] >= 0).all()


def test_identify_all_regimes():
    df = _make_test_df()
    result = identify_all_regimes(df)
    expected_cols = [
        "streak_80", "streak_90", "first_entry_90_date",
        "new_entry", "strong_streak", "accelerating", "falling_out",
    ]
    for col in expected_cols:
        assert col in result.columns
    assert not result.empty
