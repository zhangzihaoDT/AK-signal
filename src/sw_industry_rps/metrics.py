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
        return_cols = [
            c for c in df_grouped.columns
            if c.startswith("return_") and c.split("_")[1].isdigit()
        ]
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


def calc_delta_rps15_n(
    df: pd.DataFrame,
    velocity_window: int = 5,
) -> pd.DataFrame:
    """ΔRPS15_5d（Velocity）：RPS15 今日 − RPS15 N 个交易日前。

    观察「趋势位置在上升还是下降」（近期轮动强度），与 ETF Layer① velocity 口径一致。
    仅 Observation 展示，不参与排序/选择，也不改变确认门。
    """
    out = df.copy()
    out = out.sort_values(["industry_code", "trade_date"]).reset_index(drop=True)
    out[f"delta_rps15_{velocity_window}d"] = (
        out.groupby("industry_code")["RPS15"].diff(velocity_window)
    )
    out[f"delta_rps15_{velocity_window}d"] = out[f"delta_rps15_{velocity_window}d"].round(2)
    return out


def calc_acceleration_fields(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["short_term_acceleration"] = (out["RPS5"] - out["RPS15"]).round(2)
    out["medium_term_acceleration"] = (out["RPS10"] - out["RPS15"]).round(2)
    return out


def compute_all_metrics(
    all_industry_df: pd.DataFrame,
    windows: list[int] = None,
    today_window: int = 1,
    velocity_window: int = 5,
) -> pd.DataFrame:
    if windows is None:
        windows = [5, 10, 15]
    result = calc_returns(all_industry_df, windows)
    # 今日热度（RPS1）：today_window 日收益横截面百分位，仅 Observation 展示
    if today_window not in windows:
        today_returns = calc_returns(all_industry_df, [today_window])
        today_col = f"return_{today_window}"
        if today_col in today_returns.columns:
            result = result.merge(
                today_returns[["industry_code", "trade_date", today_col]],
                on=["industry_code", "trade_date"], how="left",
            )
    result = calc_rps_cross_section(result)
    result = calc_delta_rps15(result)
    result = calc_delta_rps15_n(result, velocity_window=velocity_window)
    result = calc_acceleration_fields(result)
    result = result.sort_values(["trade_date", "industry_code"]).reset_index(drop=True)
    return result


# ---------------------------------------------------------------------------
# 一级产业方向聚合（Layer② 第一问「行业轮动往哪里动」）
#
# 镜像 Layer① cross_asset_direction()：把 124 个申万二级行业收敛到申万一级
# 方向。每个方向用 强度（median_rps15）× 速度（median_delta_rps15_5d）×
# 广度（active_ratio）三维描述。纯 Observation 展示，不参与任何确认 Policy。
# ---------------------------------------------------------------------------

# 与 Layer① 共享四态枚举文案，但阈值独立实现（见 _industry_direction_state）。
DIRECTION_STATE_ORDER = ["强势上行", "加速", "横盘", "弱势下行", "—"]


def _industry_direction_state(median_rps15: float | None, active_ratio: float | None) -> str:
    """一级方向状态分类（Layer② 独立阈值，不复制 ETF 口径）。

    一级行业下二级行业数少，active_ratio 易出现 0/33/50/100 离散跳变，
    不能机械套用 Layer① 的 (median, change_5d) 规则。这里用
    （RPS15 强度中位, 内部广度）组合判断方向状态。
    """
    if median_rps15 is None:
        return "—"
    if median_rps15 >= 60 and active_ratio is not None and active_ratio >= 0.5:
        return "强势上行"
    if median_rps15 >= 60:
        return "加速"
    if median_rps15 >= 45:
        return "横盘"
    return "弱势下行"


def cross_industry_direction(
    snapshot: pd.DataFrame,
    observe_threshold: float = 80,
    active_state: set[str] | None = None,
) -> list[dict[str, object]]:
    """① 行业轮动往哪里动：按申万一级行业（parent_industry）聚合。

    每行一个一级方向，字段：
      parent_industry / industry_count / median_rps15 / median_rps1 /
      median_delta_rps15_5d / active_count / active_ratio /
      representative_industry / direction_state

    强度（median_rps15）× 速度（median_delta_rps15_5d）× 广度（active_ratio）。
    仅 Observation 展示，不参与排序/选择，也不改变确认门。
    """
    if snapshot is None or snapshot.empty or "parent_industry" not in snapshot.columns:
        return []

    df = snapshot.copy()
    df = df[df["parent_industry"].notna() & (df["parent_industry"] != "")]
    if df.empty:
        return []

    if active_state is None:
        active_state = {"强势", "观察"}
    if "strength_level" in df.columns:
        df["_active"] = df["strength_level"].astype(str).isin(list(active_state))
    else:
        df["_active"] = False

    rows: list[dict[str, object]] = []
    for parent, sub in df.groupby("parent_industry"):
        n = len(sub)
        rps15 = pd.to_numeric(sub["RPS15"], errors="coerce").dropna()
        if "RPS1" in sub.columns:
            rps1 = pd.to_numeric(sub["RPS1"], errors="coerce").dropna()
        else:
            rps1 = pd.Series(dtype=float)
        if "delta_rps15_5d" in sub.columns:
            delta5 = pd.to_numeric(sub["delta_rps15_5d"], errors="coerce").dropna()
        else:
            delta5 = pd.Series(dtype=float)
        active = int(sub["_active"].sum())

        # 代表行业：该方向下 RPS15 最高的二级行业
        rep = ""
        if not rps15.empty:
            top_row = sub.loc[rps15.idxmax()]
            rep = str(top_row.get("industry_name", "") or "")

        median = round(float(rps15.median()), 2) if not rps15.empty else None
        median_rps1 = round(float(rps1.median()), 2) if not rps1.empty else None
        median_delta = round(float(delta5.median()), 2) if not delta5.empty else None
        active_ratio = round(active / n, 2) if n else 0.0

        rows.append({
            "parent_industry": parent,
            "industry_count": n,
            "median_rps15": median,
            "median_rps1": median_rps1,
            "median_delta_rps15_5d": median_delta,
            "active_count": active,
            "active_ratio": active_ratio,
            "representative_industry": rep,
            "direction_state": _industry_direction_state(median, active_ratio),
        })

    rows.sort(key=lambda r: (r["median_rps15"] is not None, r["median_rps15"] or 0), reverse=True)
    return rows
