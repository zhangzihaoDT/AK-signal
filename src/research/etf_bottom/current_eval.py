"""当前关注 ETF 的 Repair-Retest V1 评估（读 2E 冻结产物，不重新解释）。

分类逻辑（用户锁定）：
  1. reliable?  no → UNRELIABLE（折算污染/历史不足）
  2. current_long_term_bottom?  no → OUT_OF_DOMAIN
     （long_term_bottom = price_pos_120<=20 且 price_pos_360<=20；否则已离开 2E discovery domain）
  3. else → 应用 2E 冻结 cut points（从 repair_structure.json 原样读取）
            → TARGET（pos60 Q1 × pos120 Q3）/ IN_DOMAIN_NON_TARGET

然后附：2A self-history evidence + 跨年份稳健性（2D 口径）。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.common.paths import config_dir, etf_signal_raw_dir, selection_universe_path

from . import STUDY_DIR
from .price_map import compute_row

logger = logging.getLogger(__name__)

AS_OF = "2026-08-28"
CORP_ACTION_RET = 0.20


def load_watch_etfs() -> pd.DataFrame:
    """从 selection_universe.yaml 读取关注的主题 ETF 资产。

    覆盖 theme_etf / sub_industry_etf / watch_etf（monitor_only 也是关注对象，
    仅不参与候选；current-eval 需对其完整落到同一张状态表）。
    """
    import yaml
    with open(selection_universe_path(), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    rows = []
    for theme_key, theme in cfg["themes"].items():
        for tier in theme["tiers"]:
            if tier["key"] not in ("theme_etf", "sub_industry_etf", "watch_etf"):
                continue
            participation = str(tier.get("participation", "tradeable"))
            for a in tier["assets"]:
                sym = str(a.get("symbol", "")).zfill(6)
                rows.append({
                    "fund_code": sym,
                    "fund_name": a.get("name", ""),
                    "theme": theme_key,
                    "tier": tier["key"],
                    "participation": participation,
                })
    df = pd.DataFrame(rows).drop_duplicates("fund_code").reset_index(drop=True)
    logger.info("watch ETF assets: %d", len(df))
    return df


def load_rule_spec() -> dict[str, Any]:
    """加载 Repair-Retest V1 唯一规则真源。"""
    import yaml
    path = config_dir() / "research" / "repair_retest_v1.yaml"
    with path.open(encoding="utf-8") as f:
        spec = yaml.safe_load(f) or {}
    if spec.get("rule_id") != "REPAIR_RETEST_V1" or spec.get("status") != "FROZEN_RESEARCH_HYPOTHESIS":
        raise ValueError(f"invalid frozen rule spec: {path}")
    return spec


def load_frozen_cutpoints() -> tuple[list[float], list[float]]:
    """兼容调用接口：从 frozen YAML 读取 pos120/pos60 cut points。"""
    spec = load_rule_spec()
    features = spec["features"]
    return (
        [features["price_pos_120"]["domain_lower"], features["price_pos_120"]["q1_upper"],
         features["price_pos_120"]["q2_upper"], features["price_pos_120"]["domain_upper"]],
        [features["price_pos_60"]["domain_lower"], features["price_pos_60"]["q1_upper"],
         features["price_pos_60"]["q2_upper"], features["price_pos_60"]["domain_upper"]],
    )


def tertile(value: float, edges: list[float]) -> str:
    """按边界点分桶：edges=[lo, m1, m2, hi]，Q1< m1, Q2 m1~m2, Q3> m2。"""
    if value < edges[1]:
        return "Q1"
    if value < edges[2]:
        return "Q2"
    return "Q3"


def _corp_action_dates(d: pd.DataFrame) -> list:
    rets = d["close"].pct_change().abs()
    return d.loc[rets >= CORP_ACTION_RET, "date"].dt.date.tolist()


def eval_one(code: str, name: str, theme: str, cut120: list[float], cut60: list[float],
             tier: str = "", participation: str = "tradeable") -> dict[str, Any]:
    """单只评估：reliable → domain → target 三级。"""
    d = pd.read_parquet(f"data/etf_signal/raw/{code}.parquet",
                        columns=["date", "close"]).sort_values("date").reset_index(drop=True)
    asof = pd.Timestamp(AS_OF)
    r = compute_row(code, "", "", d, asof)

    base = {"fund_code": code, "fund_name": name, "theme": theme, "tier": tier, "participation": participation}

    # 1) reliable
    full_360 = r.get("full_360_sample", False)
    unreliable_360 = r.get("unreliable_360", False)
    if not full_360 or unreliable_360:
        return {
            **base,
            "reliable": False, "stage": "UNRELIABLE",
            "reason": "360D 历史不足或折算污染",
            "corp_action_dates": _corp_action_dates(d),
            "p60": None, "p120": None, "p360": None,
        }

    p60, p120, p360 = r.get("price_pos_60"), r.get("price_pos_120"), r.get("price_pos_360")

    # 2) domain：long_term_bottom（pos120<=20 且 pos360<=20）
    if not (p120 is not None and p360 is not None and p120 <= 20.0 and p360 <= 20.0):
        return {
            **base,
            "reliable": True, "stage": "OUT_OF_DOMAIN",
            "reason": f"已离开 2E discovery domain（p120={p120:.1f}, p360={p360:.1f}，需 p120<=20 且 p360<=20）",
            "p60": p60, "p120": p120, "p360": p360,
        }

    # 3) target：应用 2E 冻结 cut points（pos60 Q1 × pos120 Q3）
    q60 = tertile(p60, cut60)
    q120 = tertile(p120, cut120)
    is_target = (q60 == "Q1") and (q120 == "Q3")
    return {
        **base,
        "reliable": True, "stage": "TARGET" if is_target else "IN_DOMAIN_NON_TARGET",
        "reason": f"p60={p60:.1f} q60={q60}, p120={p120:.1f} q120={q120}"
                  + ("" if is_target else f"（target=pos60 Q1×pos120 Q3，cut: p60<{cut60[1]} 且 p120>{cut120[2]}）"),
        "p60": p60, "p120": p120, "p360": p360,
        "q60": q60, "q120": q120,
    }


def attach_history(code: str) -> dict[str, Any]:
    """附 2A self-history + 跨年份稳健性 + payoff（读 state_odds_events）。

    evidence_label（严格口径，用户锁定）：
      1) n < 2                        → INSUFFICIENT_HISTORY
      2) pooled median_120d <= 0      → NEGATIVE_HISTORY（整体负优先，避免多年偏负误标 YEAR_DEPENDENT）
      3) 有数据年份数 >= 2 且所有年份 median > 0 → CROSS_YEAR_SUPPORTED
      4) 其余（>=2 年、pooled>0、非全正）      → YEAR_DEPENDENT

    payoff_ratio = positive_median / abs(negative_median)；n_negative=0 → None（不写无穷）。
    """
    ev = pd.read_parquet(STUDY_DIR / "state_odds_events.parquet")
    sub = ev[(ev["fund_code"] == code) & ev["state"].isin(["DEEP_BOTTOM", "RECOVERING_FROM_BOTTOM"])].copy()
    sub = sub[sub["ret_120"].notna()]
    if sub.empty:
        return {"history": {"n": 0, "median_120d": None, "mean_120d": None, "win_rate": None,
                            "n_positive": 0, "n_negative": 0,
                            "positive_median": None, "negative_median": None, "payoff_ratio": None,
                            "support": "无历史先例（证据不足）", "evidence_label": "INSUFFICIENT_HISTORY",
                            "by_year": {}}}
    r = sub["ret_120"]
    n = len(r)
    med = float(r.median())
    mean = float(r.mean())
    win = float((r > 0).mean())
    pos = r[r > 0]
    neg = r[r < 0]
    pos_med = float(pos.median()) if len(pos) else None
    neg_med = float(neg.median()) if len(neg) else None
    payoff = (pos_med / abs(neg_med)) if (pos_med is not None and neg_med is not None and neg_med < 0) else None

    if n >= 2 and med > 0 and win >= 0.5:
        support = "历史支持"
    elif n >= 2:
        support = "历史不支持"
    else:
        support = "证据不足"

    sub = sub.copy()
    sub["year"] = sub["entry_date"].astype(str).str[:4]
    by_year = {}
    for y, g in sub.groupby("year"):
        by_year[str(y)] = {"n": int(len(g)), "median": round(float(g["ret_120"].median()), 4),
                           "win": round(float((g["ret_120"] > 0).mean()), 4)}
    by_year_sorted = dict(sorted(by_year.items()))
    year_medians = [v["median"] for v in by_year_sorted.values() if v["median"] is not None]

    # evidence_label（NEGATIVE_HISTORY 优先）
    if n < 2:
        evidence_label = "INSUFFICIENT_HISTORY"
    elif med <= 0:
        evidence_label = "NEGATIVE_HISTORY"
    elif len(year_medians) >= 2 and all(v > 0 for v in year_medians):
        evidence_label = "CROSS_YEAR_SUPPORTED"
    else:
        evidence_label = "YEAR_DEPENDENT"

    return {"history": {
        "n": n, "median_120d": round(med, 4), "mean_120d": round(mean, 4), "win_rate": round(win, 4),
        "n_positive": int(len(pos)), "n_negative": int(len(neg)),
        "positive_median": round(pos_med, 4) if pos_med is not None else None,
        "negative_median": round(neg_med, 4) if neg_med is not None else None,
        "payoff_ratio": round(payoff, 4) if payoff is not None else None,
        "support": support, "evidence_label": evidence_label, "by_year": by_year,
    }}


# ── 综合判断评级（⑦ Odds Assessment）────────────────────────────
# 数据层只存稳定枚举（去 emoji）；CLI/HTML 展示时再映射符号。

def odds_assessment(stage: str, evidence_label: str, history: dict) -> str:
    """按文档决策矩阵映射 (stage, evidence_label) → 稳定枚举。

    枚举：strong_observe / watch_structure / position_only / cautious /
          out_of_domain_good / out_of_domain_bad / out_of_domain_unknown / unreliable

    OUT_OF_DOMAIN 三分类：
      good    = 历史正收益（median_120d>0）但当前不在研究域
      bad     = 历史负收益但当前不在研究域
      unknown = 历史不足（INSUFFICIENT_HISTORY，n<2 无可靠 median）
    """
    if stage == "UNRELIABLE":
        return "unreliable"
    if stage == "TARGET":
        return "strong_observe" if evidence_label == "CROSS_YEAR_SUPPORTED" else (
            "watch_structure" if evidence_label in ("YEAR_DEPENDENT", "INSUFFICIENT_HISTORY") else "cautious")
    if stage == "IN_DOMAIN_NON_TARGET":
        if evidence_label == "CROSS_YEAR_SUPPORTED":
            return "watch_structure"
        if evidence_label == "INSUFFICIENT_HISTORY":
            return "position_only"
        if evidence_label == "NEGATIVE_HISTORY":
            return "cautious"
        return "position_only"  # YEAR_DEPENDENT：位置有意义但赔率依赖年份
    if stage == "OUT_OF_DOMAIN":
        # INSUFFICIENT_HISTORY 优先于 pooled median：n<2 无可靠收益分布，不能判好坏
        if evidence_label == "INSUFFICIENT_HISTORY":
            return "out_of_domain_unknown"
        med = history.get("median_120d")
        if med is None or (isinstance(med, float) and med != med):
            return "out_of_domain_unknown"
        return "out_of_domain_good" if med > 0 else "out_of_domain_bad"
    return "unreliable"


def run_current_eval(study_dir: Path | None = None) -> dict:
    study_dir = study_dir or STUDY_DIR
    study_dir.mkdir(parents=True, exist_ok=True)
    watch = load_watch_etfs()
    spec = load_rule_spec()
    cut120, cut60 = load_frozen_cutpoints()

    results = []
    for _, row in watch.iterrows():
        res = eval_one(row["fund_code"], row["fund_name"], row["theme"], cut120, cut60,
                       tier=row.get("tier", ""), participation=row.get("participation", "tradeable"))
        res.update(attach_history(row["fund_code"]))
        h = res.get("history", {})
        res["evidence_label"] = h.get("evidence_label", "INSUFFICIENT_HISTORY")
        res["odds_assessment"] = odds_assessment(res["stage"], res["evidence_label"], h)
        results.append(res)

    stages = {}
    for r in results:
        stages[r["stage"]] = stages.get(r["stage"], 0) + 1

    # CSV 落盘（数据层纯枚举，无 emoji）
    rows = []
    for r in results:
        h = r.get("history", {})
        rows.append({
            "fund_code": r["fund_code"], "fund_name": r["fund_name"], "theme": r["theme"],
            "stage": r["stage"], "evidence_label": r["evidence_label"], "odds_assessment": r["odds_assessment"],
            "pos60": r.get("p60"), "pos120": r.get("p120"), "pos360": r.get("p360"),
            "n_entries": h.get("n"), "median_120d": h.get("median_120d"), "mean_120d": h.get("mean_120d"),
            "win_rate": h.get("win_rate"), "n_positive": h.get("n_positive"), "n_negative": h.get("n_negative"),
            "positive_median": h.get("positive_median"), "negative_median": h.get("negative_median"),
            "payoff_ratio": h.get("payoff_ratio"),
        })
    csv_path = study_dir / "current_odds_table.csv"
    pd.DataFrame(rows).to_csv(csv_path, index=False, encoding="utf-8-sig")

    payload = {
        "study": "Current Watch ETF Repair-Retest V1 Evaluation",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of": AS_OF,
        "rule_id": spec["rule_id"],
        "rule_spec_source": "config/research/repair_retest_v1.yaml",
        "cut_points_source": "config/research/repair_retest_v1.yaml (frozen V1)",
        "cut_points": {"price_pos_120": cut120, "price_pos_60": cut60},
        "stage_summary": stages,
        "odds_table_csv": str(csv_path),
        "etfs": results,
    }
    out = study_dir / "current_watch_eval.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    logger.info("current watch eval -> %s", out)
    return payload
