"""Risk Gate Ablation — 聚合分析（A1 收尾）+ 收益口径（A2）。"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from src.research.aug2026.universe import load_selection_universe_cn
from src.research.aug2026 import STUDY_DIR

logger = logging.getLogger("research.aug2026.ablation_agg")

# 报告日到交易日顺序
REPORT_DATES = ["20260803","20260804","20260805","20260806","20260807","20260810","20260811",
                "20260813","20260817","20260819","20260820","20260821","20260824","20260825",
                "20260826","20260827"]
_DATE_ORD = {d: i for i, d in enumerate(REPORT_DATES)}


def _first_date(rows: pd.DataFrame, state_col: str) -> str | None:
    sub = rows[rows[state_col] == "RECOMMENDED"]
    if sub.empty:
        return None
    return sub["date"].min()


def _first_trend_qualified(rows: pd.DataFrame) -> str | None:
    """首次「趋势合格」（score≥70 ∧ S/A，忽略 action 风险门）。"""
    sub = rows[(rows["score"] >= 70) & (rows["watch_level"].isin(["S", "A"]))]
    if sub.empty:
        return None
    return sub["date"].min()


def _norm_date(v) -> str:
    return str(int(v)) if v is not None else None


def summarize_stock(detail: pd.DataFrame, cf: str = "cfinf") -> pd.DataFrame:
    """每股票聚合：first_trend_qualified / first_rec_prod / first_rec_cf / days_trend_to_recommend。

    返回行含 released（CF 下被推荐而 production 未推荐）标记。
    """
    out_rows: list[dict] = []
    for code, g in detail.groupby("code"):
        g = g.sort_values("date")
        ftq = _norm_date(_first_trend_qualified(g))
        frp = _norm_date(_first_date(g, "state_prod"))
        frc = _norm_date(_first_date(g, f"state_{cf}"))
        days = None
        if ftq and frc and frc >= ftq and _DATE_ORD[frc] >= _DATE_ORD[ftq]:
            days = _DATE_ORD[frc] - _DATE_ORD[ftq]
        released = (frp is None) and (frc is not None)
        out_rows.append({
            "code": code,
            "first_trend_qualified_date": ftq,
            "first_rec_prod_date": frp,
            f"first_rec_{cf}_date": frc,
            "days_trend_to_recommend_cf": days,
            "released_by_ablation": released,
        })
    return pd.DataFrame(out_rows)


def released_detail(
    detail: pd.DataFrame,
    cf: str = "cfinf",
    released_codes: set[str] | None = None,
) -> pd.DataFrame:
    """被消融释放（production 未推荐、CF 推荐）的明细行。"""
    if released_codes is not None:
        mask = detail["code"].isin(released_codes)
    else:
        mask = detail["state_prod"] != "RECOMMENDED"
    return detail[mask].copy()


def incremental_stats(
    summary: pd.DataFrame,
    panel: pd.DataFrame,
    cf: str = "cfinf",
) -> dict:
    """对比 released vs not-released 的 8 月收益，量化机会成本。"""
    panel_by = panel.set_index("code")["return_aug"].to_dict()
    out = {"cf": cf}
    released = summary[summary["released_by_ablation"]]["code"].tolist()
    not_rel = summary[~summary["released_by_ablation"]]["code"].tolist()
    for label, codes in [("released", released), ("not_released", not_rel)]:
        rets = [panel_by.get(c) for c in codes if panel_by.get(c) is not None]
        if rets:
            out[f"{label}_n"] = len(rets)
            out[f"{label}_mean_ret"] = round(float(np.mean(rets)), 4)
            out[f"{label}_median_ret"] = round(float(np.median(rets)), 4)
            out[f"{label}_win_rate"] = round(float(np.mean([r > 0 for r in rets])), 4)
            out[f"{label}_hit5"] = round(float(np.mean([r > 0.05 for r in rets])), 4)
            out[f"{label}_max_loss"] = round(float(min(rets)), 4)
        else:
            out[f"{label}_n"] = 0
    return out


def threshold_curve(detail: pd.DataFrame) -> pd.DataFrame:
    """CF15/20/25/30/∞ 阈值扫描：released 数量 + 收益对比。"""
    panel = pd.read_parquet(STUDY_DIR / "fixed_pool_panel.parquet")
    panel_by = panel.set_index("code")["return_aug"].to_dict()
    rows = []
    for cf in ["cf15", "cf20", "cf25", "cf30", "cfinf"]:
        rec = detail[detail[f"state_{cf}"] == "RECOMMENDED"]
        rec_codes = set(rec["code"])
        prod_codes = set(detail[detail["state_prod"] == "RECOMMENDED"]["code"])
        released = rec_codes - prod_codes
        rel_rets = [panel_by.get(c) for c in released if panel_by.get(c) is not None]
        n_win = sum(1 for r in rel_rets if r > 0)
        n_hit5 = sum(1 for r in rel_rets if r > 0.05)
        rows.append({
            "cf": cf,
            "n_recommended_days": int(len(rec)),
            "n_unique_stocks": int(len(rec_codes)),
            "n_released_stocks": int(len(released)),
            "released_mean_ret": round(float(np.mean(rel_rets)), 4) if rel_rets else None,
            "released_median_ret": round(float(np.median(rel_rets)), 4) if rel_rets else None,
            "released_win_rate": round(float(n_win / len(rel_rets)), 4) if rel_rets else None,
            "released_hit5": round(float(n_hit5 / len(rel_rets)), 4) if rel_rets else None,
            "released_max_loss": round(float(min(rel_rets)), 4) if rel_rets else None,
        })
    return pd.DataFrame(rows)
