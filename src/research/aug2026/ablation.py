"""Risk Gate Ablation Study（2026-08）— 反事实消融 + 阈值扫描。

问题：drawdown_from_high ≥15% → calc_action=风险警戒 → risk_gate_passed=False，
是否在 2026-08 系统性阻止了趋势已修复的股票进入 selection？

方法（零生产改动）：
  - import 生产函数（engine.calc_action / calc_watch_level / calc_change、
    history.score_row、selection._stock_state），只操纵 drawdown_from_high 输入。
  - 特征全部来自 data/processed/CN_{code}.csv（含 calc_action 全部输入列）。
  - 收益/position 用真实 close（aug2026 面板 / four_stage.evaluate_position）。
  - CF15=production（真实 dd），CF20/CF25/CF30，CF∞=ignore（dd→0）。

产物：
  - counterfactual_ablation_full.csv（51 只 × 16 交易日 × 5 CF）
  - ablation_summary.csv（每股票聚合：first_trend/recommended/days/释放）
  - ablation_curve.csv（CF 阈值扫描：released_winners/losers/incremental）
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from src.common.paths import processed_dir
from src.trend_engine import engine as te
from src.research.expression_regime.history import score_row
from src.selection import selection as sel_module
from src.selection.four_stage import evaluate_position

from . import STUDY_DIR

logger = logging.getLogger("research.aug2026.ablation")

CF_LABELS = {"cf15": 0.15, "cf20": 0.20, "cf25": 0.25, "cf30": 0.30, "cfinf": None}
REPORT_DATES = ["20260803","20260804","20260805","20260806","20260807","20260810","20260811",
                "20260813","20260817","20260819","20260820","20260821","20260824","20260825",
                "20260826","20260827"]


def _f(row: pd.Series, col: str) -> float | None:
    v = row.get(col)
    try:
        if v is None or pd.isna(v):
            return None
        return float(v)
    except Exception:
        return None


def compute_daily_rows(
    code: str,
    market: str,
    df_processed: pd.DataFrame,
    prev_score_by_date: dict[str, int],
) -> list[dict]:
    """对单只股票 8 月每个报告日，算 production + 各 CF 的 action/state。

    prev_score_by_date: {date: prev_day_score}，供 calc_change 用（当日 score 为 cur）。
    """
    rows: list[dict] = []
    df = df_processed.copy()
    df["date"] = pd.to_datetime(df["date"])
    for date_str in REPORT_DATES:
        ts = pd.Timestamp(date_str)
        sub = df[df["date"] == ts]
        if sub.empty:
            continue
        row = sub.iloc[0]
        score = score_row(row)
        wl = te.calc_watch_level(
            score, _f(row, "relative_strength_20d"), _f(row, "ma20"), _f(row, "ma60"), _f(row, "volume_ratio"))
        prev_score = prev_score_by_date.get(date_str)
        change = te.calc_change(score, prev_score, prev_score is not None)
        rs = _f(row, "relative_strength_20d")
        vr = _f(row, "volume_ratio")
        pnm_raw = row.get("price_near_ma20")
        pnm = bool(pnm_raw) if pd.notna(pnm_raw) else None
        dd_real = _f(row, "drawdown_from_high")

        action_prod = te.calc_action(score, wl, rs, vr, pnm, dd_real, change)
        row_base = {
            "code": code,
            "market": market,
            "date": date_str,
            "score": score,
            "watch_level": wl,
            "dd_real": round(dd_real, 4) if dd_real is not None else None,
            "action_prod": action_prod,
            "change": change,
        }
        for cf, dd_th in CF_LABELS.items():
            if cf == "cf15":
                dd_used = dd_real
            elif cf == "cfinf":
                dd_used = 0.0
            else:
                # 阈值扫描：dd 超过阈值才触发风险警戒。
                # dd_real ≥ th → 保留真实 dd（仍 ≥15% 触发规则）；dd_real < th → 传 0（不触发）。
                dd_used = dd_real if (dd_real is not None and dd_real >= dd_th) else 0.0
            action_cf = te.calc_action(score, wl, rs, vr, pnm, dd_used, change)
            row_base[f"action_{cf}"] = action_cf
            row_base[f"dd_{cf}"] = round(dd_used, 4) if dd_used is not None else None
        rows.append(row_base)
    return rows


def _state_for(action: str, score: float, wl: str, theme_confirmed: bool) -> str:
    return sel_module._stock_state(score, wl, action, theme_confirmed)


def run_ablation(
    codes: list[str],
    theme_confirmed_by_date: dict[str, bool],
) -> pd.DataFrame:
    """主入口。返回 detail DataFrame（51 只 × 16 交易日 × 5 CF）。"""
    detail_rows: list[dict] = []
    pd_dir = processed_dir()

    for code in codes:
        path = pd_dir / f"CN_{code}.csv"
        if not path.exists():
            logger.warning("no processed csv: %s", code)
            continue
        df = pd.read_csv(path, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
        df["_score"] = df.apply(score_row, axis=1)
        df["_score_prev"] = df["_score"].shift(1)
        prev_by_date = dict(zip(
            pd.to_datetime(df["date"]).dt.strftime("%Y%m%d"), df["_score_prev"]))
        rows = compute_daily_rows(code, "CN", df, prev_by_date)
        for r in rows:
            confirmed = theme_confirmed_by_date.get(r["date"], False)
            for cf in CF_LABELS:
                action_cf = r[f"action_{cf}"]
                state_cf = _state_for(action_cf, r["score"], r["watch_level"], confirmed)
                risk_gate_cf = action_cf not in ("风险警戒", "剔除观察")
                r[f"state_{cf}"] = state_cf
                r[f"risk_gate_{cf}"] = risk_gate_cf
            state_prod = _state_for(r["action_prod"], r["score"], r["watch_level"], confirmed)
            risk_gate_prod = r["action_prod"] not in ("风险警戒", "剔除观察")
            r["state_prod"] = state_prod
            r["risk_gate_prod"] = risk_gate_prod
            detail_rows.append(r)

    return pd.DataFrame(detail_rows)


def save_ablation(detail: pd.DataFrame) -> None:
    out = STUDY_DIR / "counterfactual_ablation_full.csv"
    detail.to_csv(out, index=False, encoding="utf-8")
    logger.info("saved %s (%d rows)", out, len(detail))
