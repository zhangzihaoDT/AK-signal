"""Study 2E — Repair Structure Validation。

不新增变量，只验证 price_pos_120 这一个 surviving signal 的结构。
复用 context_replication_events.parquet 已增强字段，三个问题：

Q1 — Composition effect：DEEP / RECOVERING 内部各自 pos120 quintile 是否仍成立？
     判定：若 state 内部无信号而全样本有 → pos120 效果主要是 bottom_state composition。
Q2 — Interaction effect：pos120 × pos60 二维分桶（3×3 主 + 2×2 robustness）。
     判定：理想结构「pos60 低（仍在底）+ pos120 较高（已修复）」应同时领先 mean 与 median。
Q3 — Date weighting：entry_date × state × pos120 quintile 组内 median → 每日期一票。
     与 event-weighted / ETF-balanced 三档对照，验证结果是否由同期批量事件撑起。

四假设 adjudication：
  CONTINUOUS_SIGNAL / COMPOSITION_EFFECT / INTERACTION_STRUCTURE / 无稳定信号
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.common.paths import config_dir

from . import STUDY_DIR

logger = logging.getLogger(__name__)

OUTCOME = "excess_vs_etf_market_120d"
STATES = ["DEEP_BOTTOM", "RECOVERING_FROM_BOTTOM"]


def _load() -> pd.DataFrame:
    ev = pd.read_parquet(STUDY_DIR / "context_replication_events.parquet")
    ev = ev[ev[OUTCOME].notna()].copy()
    ev["_date"] = ev["entry_date"].astype(str).str[:10]
    return ev


def _qcut_col(df: pd.DataFrame, col: str, n: int) -> pd.Series:
    return pd.qcut(df[col], n, labels=[f"Q{i}" for i in range(1, n + 1)], duplicates="drop")


def _qcut_cut_points(df: pd.DataFrame, col: str, n: int) -> list[float]:
    """返回 qcut(n) 的边界点（含两端），供外部原样读取、不复算。"""
    bins = pd.qcut(df[col], n, duplicates="drop")
    edges = sorted({c.left for c in bins.cat.categories} | {c.right for c in bins.cat.categories})
    return [round(float(x), 4) for x in edges]


def discovery_domain(df: pd.DataFrame) -> dict:
    """2E discovery universe 定义（原样记录，供后续评估复用）。

    注意：universe 是「长期底部 entry」（DEEP+RECOVERING，pos120≤20 且 pos360≤20），
    因此 pos120 的 discovery 域上限即 20（long_term_bottom 定义），并非全市场。
    """
    return {
        "source": "context_replication_events.parquet",
        "n_entries": int(len(df)),
        "n_etfs": int(df["fund_code"].nunique()),
        "n_dates": int(df["_date"].nunique()),
        "state_filter": ["DEEP_BOTTOM", "RECOVERING_FROM_BOTTOM"],
        "long_term_bottom_definition": "price_pos_120<=20 and price_pos_360<=20",
        "cut_points": {
            "price_pos_120": _qcut_cut_points(df, "price_pos_120", 3),
            "price_pos_60": _qcut_cut_points(df, "price_pos_60", 3),
        },
    }


def frozen_cutpoint_drift(df: pd.DataFrame, tolerance: float = 0.05) -> dict:
    """比较本次重算 cut points 与 frozen V1；绝不覆盖 frozen spec。"""
    import yaml

    spec_path = config_dir() / "research" / "repair_retest_v1.yaml"
    with spec_path.open(encoding="utf-8") as f:
        spec = yaml.safe_load(f) or {}
    latest = discovery_domain(df)["cut_points"]
    frozen = {
        "price_pos_120": [
            spec["features"]["price_pos_120"]["domain_lower"],
            spec["features"]["price_pos_120"]["q1_upper"],
            spec["features"]["price_pos_120"]["q2_upper"],
            spec["features"]["price_pos_120"]["domain_upper"],
        ],
        "price_pos_60": [
            spec["features"]["price_pos_60"]["domain_lower"],
            spec["features"]["price_pos_60"]["q1_upper"],
            spec["features"]["price_pos_60"]["q2_upper"],
            spec["features"]["price_pos_60"]["domain_upper"],
        ],
    }
    rows = {}
    within = True
    for feature in frozen:
        deltas = [round(float(a) - float(b), 4) for a, b in zip(latest[feature], frozen[feature])]
        rows[feature] = {"frozen": frozen[feature], "latest": latest[feature], "delta": deltas}
        if any(abs(d) > tolerance for d in deltas):
            within = False
    return {
        "source": str(spec_path.relative_to(config_dir().parent)),
        "tolerance": tolerance,
        "status": "DRIFT_WITHIN_TOLERANCE" if within else "DRIFT_OUTSIDE_TOLERANCE",
        "features": rows,
    }


def _cell_stats(g: pd.DataFrame) -> dict:
    v = pd.to_numeric(g[OUTCOME], errors="coerce").dropna()
    if v.empty:
        return {"n": 0, "mean": None, "median": None}
    return {
        "n": int(len(v)),
        "mean": round(float(v.mean()), 4),
        "median": round(float(v.median()), 4),
    }


def q1_composition(df: pd.DataFrame) -> dict:
    """Q1：DEEP / RECOVERING 内部 pos120 quintile。"""
    out = {}
    for st in STATES:
        sub = df[df["state"] == st]
        sub = sub.copy()
        sub["_q"] = _qcut_col(sub, "price_pos_120", 5)
        rows = []
        for q in ["Q1", "Q2", "Q3", "Q4", "Q5"]:
            g = sub[sub["_q"] == q]
            rows.append({"quintile": q, ** _cell_stats(g),
                         "pos120_mean": round(float(g["price_pos_120"].mean()), 2) if len(g) else None})
        out[st] = {"n": int(len(sub)), "quintiles": rows}
    # 全样本对照
    allsub = df.copy()
    allsub["_q"] = _qcut_col(allsub, "price_pos_120", 5)
    out["ALL"] = {"n": int(len(allsub)),
                  "quintiles": [{"quintile": q, **_cell_stats(allsub[allsub["_q"] == q])} for q in ["Q1", "Q2", "Q3", "Q4", "Q5"]]}
    return out


def q2_interaction(df: pd.DataFrame, grid: int = 3) -> dict:
    """Q2：pos120 × pos60 二维分桶。grid=3 主结果，grid=2 robustness。"""
    sub = df.copy()
    sub["_r"] = _qcut_col(sub, "price_pos_120", grid)   # row: pos120
    sub["_c"] = _qcut_col(sub, "price_pos_60", grid)     # col: pos60
    labels = [f"Q{i}" for i in range(1, grid + 1)]
    matrix = {}
    for r in labels:
        for c in labels:
            g = sub[(sub["_r"] == r) & (sub["_c"] == c)]
            matrix[f"{r}_{c}"] = {"pos120_quintile": r, "pos60_quintile": c, **_cell_stats(g)}
    # 目标格子：pos60 低 + pos120 高
    target = matrix.get(f"Q{grid}_Q1")  # pos120 Q{grid}=最高, pos60 Q1=最低
    return {"grid": grid, "matrix": matrix, "target_cell": target}


def q3_date_weighted(df: pd.DataFrame) -> dict:
    """Q3：entry_date × state × pos120 quintile → 每日期一票（median outcome）。"""
    sub = df.copy()
    sub["_q"] = _qcut_col(sub, "price_pos_120", 5)
    out = {}
    for st in ["ALL"] + STATES:
        s = sub if st == "ALL" else sub[sub["state"] == st]
        rows = []
        for q in ["Q1", "Q2", "Q3", "Q4", "Q5"]:
            g = s[s["_q"] == q]
            # 每日期先取 median outcome
            daily = g.groupby("_date")[OUTCOME].median()
            rows.append({
                "quintile": q,
                "n_dates": int(len(daily)),
                "n_entries": int(len(g)),
                "date_weighted_median": round(float(daily.median()), 4) if len(daily) else None,
                "date_weighted_mean": round(float(daily.mean()), 4) if len(daily) else None,
            })
        out[st] = {"n_dates_total": int(s["_date"].nunique()), "quintiles": rows}

    # target 结构（pos60 低 × pos120 高）的 date-weighted（每日期一票 median）
    sub = sub.copy()
    sub["_r3"] = _qcut_col(sub, "price_pos_120", 3)  # row: pos120
    sub["_c3"] = _qcut_col(sub, "price_pos_60", 3)    # col: pos60
    target = sub[(sub["_c3"] == "Q1") & (sub["_r3"] == "Q3")]  # pos60 低 + pos120 高
    all_daily = sub.groupby("_date")[OUTCOME].median()
    target_daily = target.groupby("_date")[OUTCOME].median()
    out["_target_date_weighted"] = {
        "target_n_entries": int(len(target)),
        "target_n_dates": int(len(target_daily)),
        "target_median": round(float(target_daily.median()), 4) if len(target_daily) else None,
        "all_median": round(float(all_daily.median()), 4) if len(all_daily) else None,
    }
    return out


def run_repair(study_dir: Path | None = None) -> dict:
    study_dir = study_dir or STUDY_DIR
    study_dir.mkdir(parents=True, exist_ok=True)

    df = _load()
    q1 = q1_composition(df)
    q2_3 = q2_interaction(df, grid=3)
    q2_2 = q2_interaction(df, grid=2)
    q3 = q3_date_weighted(df)
    adjudication = _adjudicate(q1, q2_3, q2_2, q3)
    drift = frozen_cutpoint_drift(df)

    payload = {
        "study": "Study 2E Repair Structure Validation",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_entries": int(len(df)),
        "n_etfs": int(df["fund_code"].nunique()),
        "n_dates": int(df["_date"].nunique()),
        "discovery_universe": discovery_domain(df),
        "frozen_cutpoint_drift": drift,
        "q1_composition": q1,
        "q2_interaction_3x3": q2_3,
        "q2_interaction_2x2": q2_2,
        "q3_date_weighted": q3,
        "adjudication": adjudication,
    }
    out = study_dir / "repair_structure.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    logger.info("study 2E repair structure -> %s", out)
    return payload


def _adjudicate(q1: dict, q2_3: dict, q2_2: dict, q3: dict) -> dict:
    """四假设裁决：CONTINUOUS / COMPOSITION / INTERACTION / 无稳定信号。

    INTERACTION 需三档全过：3×3 与 2×2 的 target 格 mean+median 领先，且 date-weighted 仍领先。
    """
    # Q1：state 内部 pos120 方向（Q1=最低 pos120, Q5=最高；方向：高→好 = Q5-Q1 为正）
    state_directions = []
    for st in STATES:
        qs = q1[st]["quintiles"]
        q1v, q5v = qs[0]["mean"], qs[-1]["mean"]
        if q1v is not None and q5v is not None:
            state_directions.append((st, q5v - q1v))
    allqs = q1["ALL"]["quintiles"]
    all_dir = allqs[-1]["mean"] - allqs[0]["mean"] if (allqs[-1]["mean"] is not None and allqs[0]["mean"] is not None) else None

    # Q2：target 格（pos60 低 + pos120 高）在 3×3 与 2×2 是否同时领先 mean 与 median
    t3 = q2_3["target_cell"] or {}
    t2 = q2_2["target_cell"] or {}
    all_means = [m["mean"] for m in q2_3["matrix"].values() if m.get("mean") is not None]
    all_meds = [m["median"] for m in q2_3["matrix"].values() if m.get("median") is not None]
    t3_lead_mean = t3.get("mean") is not None and t3["mean"] >= max(all_means)
    t3_lead_med = t3.get("median") is not None and t3["median"] >= max(all_meds)
    m2 = [m["mean"] for m in q2_2["matrix"].values() if m.get("mean") is not None]
    md2 = [m["median"] for m in q2_2["matrix"].values() if m.get("median") is not None]
    t2_lead_mean = t2.get("mean") is not None and t2["mean"] >= max(m2)
    t2_lead_med = t2.get("median") is not None and t2["median"] >= max(md2)

    # Q3：target 格在 date-weighted（每日期一票 median）是否仍领先
    # 全样本 target 格 date-weighted 中位 vs 全样本 date-weighted 中位
    target_dw = q3.get("_target_date_weighted", {})
    t3_dw_median = target_dw.get("target_median")
    t3_dw_all = target_dw.get("all_median")
    t3_date_lead = (t3_dw_median is not None and t3_dw_all is not None and t3_dw_median > t3_dw_all)

    # 判定
    state_consistent = all(d >= 0 for _, d in state_directions) and len(state_directions) == 2
    interaction_supported = t3_lead_mean and t3_lead_med and t2_lead_mean and t2_lead_med and t3_date_lead
    if interaction_supported:
        verdict = "INTERACTION_STRUCTURE"
        summary = "中期修复 × 短期再探底（低 pos60 + 高 pos120）在 mean/median、3×3/2×2、date-weighted 均领先 → Repair-Retest 结构成立"
    elif state_consistent and (all_dir or 0) > 0:
        verdict = "CONTINUOUS_SIGNAL"
        summary = "state 内部 pos120 方向一致且全样本正 → pos120 是独立连续信号"
    elif not state_consistent and (all_dir or 0) > 0:
        verdict = "COMPOSITION_EFFECT"
        summary = "全样本正但 state 内部方向不一致/减弱 → 效果主要来自状态构成，非 pos120 连续值"
    else:
        verdict = "NO_STABLE_SIGNAL"
        summary = "未发现足够稳定、可用于决策的底部结构信号"

    return {
        "verdict": verdict,
        "summary": summary,
        "evidence": {
            "q1_state_directions": state_directions,
            "q1_all_direction": all_dir,
            "q2_3x3_target_lead_mean": t3_lead_mean,
            "q2_3x3_target_lead_median": t3_lead_med,
            "q2_2x2_target_lead_mean": t2_lead_mean,
            "q2_2x2_target_lead_median": t2_lead_med,
            "q3_target_date_weighted_median": t3_dw_median,
            "q3_all_date_weighted_median": t3_dw_all,
            "q3_target_date_lead": t3_date_lead,
        },
    }
