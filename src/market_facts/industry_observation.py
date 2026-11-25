"""Market-fact summaries for the fixed TDX industry cross-section."""
from __future__ import annotations

from typing import Any

import pandas as pd


def _industry_direction_state(median_rps15: float | None, active_ratio: float | None) -> str:
    """Describe industry direction strength and breadth using the existing thresholds."""
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
    """Aggregate the daily industry cross-section by its registered parent industry."""
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

    rows.sort(key=lambda row: (row["median_rps15"] is not None, row["median_rps15"] or 0), reverse=True)
    return rows


def classify_trend_stage(row: Any) -> str:
    """Classify a single industry row for the Observation display, not a policy gate."""
    def number(value):
        try:
            result = float(value)
            return result if not pd.isna(result) else None
        except (TypeError, ValueError):
            return None

    rps15 = number(row.get("RPS15"))
    rps5 = number(row.get("RPS5"))
    falling = number(row.get("falling_out"))
    if rps15 is None:
        return "—"
    if falling == 1 or (rps15 >= 80 and rps5 is not None and rps5 < 60):
        return "正在退潮"
    if rps5 is not None and rps5 >= 80 and rps15 < 80:
        return "正在启动"
    if rps15 >= 80:
        return "已形成趋势"
    return "—"
