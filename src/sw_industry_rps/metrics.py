from __future__ import annotations

import pandas as pd


def calc_returns(df: pd.DataFrame, windows: list[int] = None) -> pd.DataFrame:
    if windows is None:
        windows = [5, 10, 15]
    out = df.copy()
    out = out.sort_values(["industry_code", "trade_date"]).reset_index(drop=True)
    for w in windows:
        col = f"return_{w}"
        out[col] = (
            out.groupby("industry_code")["close"]
            .transform(lambda x: x / x.shift(w) - 1)
        )
    return out


def calc_rps_cross_section(
    df_grouped: pd.DataFrame,
    date_col: str = "trade_date",
    code_col: str = "industry_code",
    return_cols: list[str] = None,
) -> pd.DataFrame:
    if return_cols is None:
        return_cols = ["return_5", "return_10", "return_15"]
    out = df_grouped.copy()
    for col in return_cols:
        rps_col = f"RPS{col.split('_')[1]}"
        out[rps_col] = out.groupby(date_col)[col].rank(pct=True, ascending=True) * 100
        out[rps_col] = out[rps_col].round(2)
    return out


def calc_delta_rps15(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out = out.sort_values(["industry_code", "trade_date"]).reset_index(drop=True)
    out["delta_rps15"] = out.groupby("industry_code")["RPS15"].diff()
    out["delta_rps15"] = out["delta_rps15"].round(2)
    return out


def calc_acceleration_fields(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["short_term_acceleration"] = (out["RPS5"] - out["RPS15"]).round(2)
    out["medium_term_acceleration"] = (out["RPS10"] - out["RPS15"]).round(2)
    return out


def compute_all_metrics(
    all_industry_df: pd.DataFrame,
    windows: list[int] = None,
) -> pd.DataFrame:
    if windows is None:
        windows = [5, 10, 15]
    result = calc_returns(all_industry_df, windows)
    result = calc_rps_cross_section(result)
    result = calc_delta_rps15(result)
    result = calc_acceleration_fields(result)
    result = result.sort_values(["trade_date", "industry_code"]).reset_index(drop=True)
    return result
