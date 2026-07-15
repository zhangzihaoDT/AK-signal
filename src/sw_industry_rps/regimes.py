from __future__ import annotations

import pandas as pd


def calc_streaks(df: pd.DataFrame, strong_threshold: int = 90, observe_threshold: int = 80) -> pd.DataFrame:
    out = df.copy()
    out = out.sort_values(["industry_code", "trade_date"]).reset_index(drop=True)

    out["streak_80"] = out.groupby("industry_code")["RPS15"].transform(
        lambda s: s.ge(observe_threshold).groupby((s.lt(observe_threshold)).cumsum()).cumsum()
    )
    out["streak_90"] = out.groupby("industry_code")["RPS15"].transform(
        lambda s: s.ge(strong_threshold).groupby((s.lt(strong_threshold)).cumsum()).cumsum()
    )
    return out


def calc_first_entry_90(df: pd.DataFrame, strong_threshold: int = 90) -> pd.DataFrame:
    out = df.copy()
    out = out.sort_values(["industry_code", "trade_date"]).reset_index(drop=True)

    def _first_entry(grp: pd.Series) -> pd.Series:
        dates = grp.index
        first_above = grp[grp >= strong_threshold]
        if first_above.empty:
            return pd.Series([None] * len(grp), index=grp.index)
        first_date = grp.index[0] if isinstance(grp.index, pd.DatetimeIndex) else grp.index[0]
        return pd.Series([None] * len(grp), index=grp.index)

    out = out.sort_values(["industry_code", "trade_date"])
    codes = out["industry_code"].unique()
    entry_dates: list[pd.Timestamp | None] = []
    for code in codes:
        mask = out["industry_code"] == code
        sub = out.loc[mask].copy()
        first_above = sub.loc[sub["RPS15"] >= strong_threshold, "trade_date"]
        if first_above.empty:
            entry_dates.extend([None] * mask.sum())
        else:
            first_dt = first_above.iloc[0]
            dates_in_group = out.loc[mask, "trade_date"]
            entry_dates.extend(first_dt if d >= first_dt else None for d in dates_in_group)
    out["first_entry_90_date"] = entry_dates
    out = out.reset_index(drop=True)
    return out


def calc_first_entry_90_v2(df: pd.DataFrame, strong_threshold: int = 90) -> pd.DataFrame:
    out = df.copy()
    out = out.sort_values(["industry_code", "trade_date"]).reset_index(drop=True)

    out = out.assign(
        _above=out["RPS15"] >= strong_threshold,
    )
    group_key = out.groupby("industry_code")["_above"].transform(
        lambda s: s.astype(int).cumsum().where(s)
    )
    out["_entry_group"] = group_key
    first_dates = out[out["_above"]].groupby(["industry_code", "_entry_group"])["trade_date"].transform("first")
    out["first_entry_90_date"] = first_dates
    out = out.drop(columns=["_above", "_entry_group"])
    return out


def calc_regime_flags(
    df: pd.DataFrame,
    strong_threshold: int = 90,
    strong_streak_min: int = 3,
    delta_acceleration: float = 10.0,
) -> pd.DataFrame:
    out = df.copy()
    out = out.sort_values(["industry_code", "trade_date"]).reset_index(drop=True)

    rps15_prev = out.groupby("industry_code")["RPS15"].shift(1)

    out["new_entry"] = (
        (out["RPS15"] >= strong_threshold) & (rps15_prev < strong_threshold)
    ).astype(int)

    out["strong_streak"] = (
        (out["RPS15"] >= strong_threshold) & (out["streak_90"] >= strong_streak_min)
    ).astype(int)

    out["accelerating"] = (
        (out["RPS15"] >= 80) & (out["delta_rps15"] >= delta_acceleration)
    ).astype(int)

    out["falling_out"] = (
        (out["RPS15"] < strong_threshold) & (rps15_prev >= strong_threshold)
    ).astype(int)

    out = out.drop(columns=["_rps15_prev"], errors="ignore")
    return out


def identify_all_regimes(
    df: pd.DataFrame,
    strong_threshold: int = 90,
    observe_threshold: int = 80,
    strong_streak_min: int = 3,
    delta_acceleration: float = 10.0,
) -> pd.DataFrame:
    out = df.copy()
    if "delta_rps15" not in out.columns:
        out = out.sort_values(["industry_code", "trade_date"])
        out["delta_rps15"] = out.groupby("industry_code")["RPS15"].diff()
    out = calc_streaks(out, strong_threshold, observe_threshold)
    out = calc_first_entry_90_v2(out, strong_threshold)
    out = calc_regime_flags(out, strong_threshold, strong_streak_min, delta_acceleration)
    return out
