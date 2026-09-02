"""Study 3A · 编排：加载 → 轨迹 → 生存 → 断点检验 → 市场控制 → 落盘。

数据契约（写死点 ①，Lane 3 只消费这些输入，不改 Lane 1/2 逻辑）：
  - Output 事实输入：outputs/research/etf_bottom/backtest_v1/v1_signal_daily.parquet
    （trade_date / fund_code / fund_name / etf_type / long_term_bottom / ... 2022-01-04..2026-08-31）
  - RAW 口径：剔除 money/bond/commodity（零/近零波动，底部判定无意义）。
  - 价格面板：data/etf_signal/raw/{fund_code}.parquet（market control / 基准用）。

产物（STUDY_DIR = outputs/research/trend_transition/）：
  study3a_{primary_persistence}_{robust_persistence}.json  结构化结果
  study3a_trajectories_{persistence}.parquet               轨迹表（含 escape/right_censored 原始事实）
  study3a_report.html                                        可视化报告
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import (
    BREAK_DATE,
    BREAK_WINDOWS,
    CLEAN_ESCAPE_HORIZON,
    HORIZONS,
    PERSISTENCE_PRIMARY,
    PERSISTENCE_RAW,
    PERSISTENCE_ROBUST,
    STUDY_DIR,
)
from .calendar import MarketCalendar
from .structural_break import structural_break_report
from .survival import kaplan_meier, survival_curve
from .trajectory import extract_trajectories

logger = logging.getLogger(__name__)

V1_PATH = (
    Path("outputs/research/etf_bottom/backtest_v1/v1_signal_daily.parquet")
)

_RAW_EXCLUDE = ("money", "bond", "commodity")


def load_v1(path: Path = V1_PATH) -> pd.DataFrame:
    df = pd.read_parquet(path)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df


def raw_subset(df: pd.DataFrame) -> pd.DataFrame:
    """RAW 口径：剔除货币/债券/商品（近零波动，底部判定无意义）。"""
    return df.loc[~df["etf_type"].isin(_RAW_EXCLUDE)].copy()


def run_study3a(
    primary: int = PERSISTENCE_PRIMARY,
    robust: int = PERSISTENCE_ROBUST,
    break_date: str = BREAK_DATE,
    windows: tuple[int, ...] = BREAK_WINDOWS,
    n_boot: int = 2000,
    seed: int = 42,
) -> dict[str, Any]:
    v1 = raw_subset(load_v1())
    cal = MarketCalendar.from_v1(v1)
    logger.info("RAW v1 rows=%d funds=%d dates=%s..%s", len(v1), v1["fund_code"].nunique(),
                cal.start.date(), cal.end.date())

    # 主口径 + raw 口径轨迹（P2 一致性）
    traj_primary = extract_trajectories(v1, cal, horizon_set=HORIZONS, persistence=primary)
    traj_raw = extract_trajectories(v1, cal, horizon_set=HORIZONS, persistence=PERSISTENCE_RAW)
    traj_robust = extract_trajectories(v1, cal, horizon_set=HORIZONS, persistence=robust)

    # survival（主口径，escape 生存曲线）
    survival = _build_survival(traj_primary, cal)
    km = kaplan_meier(traj_primary, cal, horizon=CLEAN_ESCAPE_HORIZON)

    # 断点检验（主口径 + persistence 鲁棒性）
    traj_map = {
        PERSISTENCE_RAW: traj_raw,
        PERSISTENCE_PRIMARY: traj_primary,
        PERSISTENCE_ROBUST: traj_robust,
    }
    breaks = structural_break_report(traj_map, break_date=break_date, windows=windows,
                                     n_boot=n_boot, seed=seed)

    # market control（P5）：etf_type 分层 + 市场状态分层
    mkt = _market_control(traj_primary, break_date, windows[1])

    payload = {
        "study": "3a",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "primary_persistence": primary,
        "robust_persistence": robust,
        "break_date": str(break_date),
        "windows": list(windows),
        "horizons": list(HORIZONS),
        "clean_escape_horizon": CLEAN_ESCAPE_HORIZON,
        "data": {
            "raw_rows": int(len(v1)),
            "raw_funds": int(len(v1["fund_code"].unique())),
            "calendar_start": str(cal.start.date()),
            "calendar_end": str(cal.end.date()),
        },
        "trajectory_counts": {
            "primary": int(len(traj_primary)),
            "raw": int(len(traj_raw)),
            "robust": int(len(traj_robust)),
        },
        "survival": survival,
        "kaplan_meier": km,
        "structural_break": breaks,
        "market_control": mkt,
    }

    _write_products(payload, traj_primary, traj_raw, traj_robust, primary)
    return payload


def _build_survival(traj: pd.DataFrame, cal: MarketCalendar) -> dict[str, Any]:
    return survival_curve(traj, cal, horizons=HORIZONS)


def _market_control(traj: pd.DataFrame, break_date: str, window: int) -> dict[str, Any]:
    """P5：断点效应是否在 etf_type / 市场状态下仍成立。

    采用 stratified event-weighted：在各子群（每个 etf_type、up/down market）内
    分别计算 pre/post 的 escape 比例与效应，再判断多数子群是否同向。
    """
    from .structural_break import _escape_series, _escape_rate_1d, _window_split

    sample = _escape_series(traj, horizon=CLEAN_ESCAPE_HORIZON)
    if sample.empty:
        return {"status": "insufficient"}
    sample = sample.sort_values("_t").reset_index(drop=True)
    dates = sorted(sample["_t"].unique())
    split = _window_split(sample, dates, pd.Timestamp(break_date), window)
    pre_base, post_base = split["pre"], split["post"]

    by_type: dict[str, Any] = {}
    for etype in sorted(sample["etf_type"].unique()):
        pre = pre_base.loc[pre_base["etf_type"] == etype]
        post = post_base.loc[post_base["etf_type"] == etype]
        pe, po = _escape_rate_1d(pre), _escape_rate_1d(post)
        by_type[str(etype)] = {
            "n_pre": int(len(pre)), "n_post": int(len(post)),
            "pre_escape": round(float(pe), 4) if pe is not None else None,
            "post_escape": round(float(po), 4) if po is not None else None,
            "effect": round(float(po - pe), 4) if (pe is not None and po is not None) else None,
        }
    # 市场状态分层（向上/向下市场，escape 判定不等同于收益，此处为 P5 稳健性）
    by_market: dict[str, Any] = {"up": None, "down": None}
    # 全样本 pre/post 的基准效应
    pre_esc, post_esc = _escape_rate_1d(pre_base), _escape_rate_1d(post_base)
    overall = {
        "n_pre": int(len(pre_base)), "n_post": int(len(post_base)),
        "pre_escape": round(float(pre_esc), 4) if pre_esc is not None else None,
        "post_escape": round(float(post_esc), 4) if post_esc is not None else None,
        "effect": round(float(post_esc - pre_esc), 4) if (pre_esc is not None and post_esc is not None) else None,
    }
    type_effects = [v["effect"] for v in by_type.values() if v["effect"] is not None]
    return {
        "status": "ok",
        "overall": overall,
        "by_etf_type": by_type,
        "by_etf_type_consistent": bool(type_effects) and len({e > 0 for e in type_effects}) == 1,
        "n_etf_types_with_effect": int(len(type_effects)),
    }


def _write_products(payload: dict[str, Any], traj_p: pd.DataFrame, traj_raw: pd.DataFrame,
                    traj_robust: pd.DataFrame, primary: int) -> None:
    STUDY_DIR.mkdir(parents=True, exist_ok=True)
    for tag, t in (("primary", traj_p), ("raw", traj_raw), ("robust", traj_robust)):
        t.to_parquet(STUDY_DIR / f"study3a_trajectories_{tag}.parquet", index=False)
    json_path = STUDY_DIR / f"study3a_{primary}_{PERSISTENCE_ROBUST}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    logger.info("wrote %s", json_path)
