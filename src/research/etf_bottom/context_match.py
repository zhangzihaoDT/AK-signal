"""Study 2C 编排：全 episode context 计算 + 距离匹配 + 成功/失败区分。

关键规则（用户锁定）：
  - context 只在 episode.start 当日可观察（no look-ahead）
  - Z-score scaler 只 fit 历史 episode，再 transform historical + current
  - 距离 = 维度组等权（主）或 30/25/20/15/10（sensitivity），组内先平均 z²
  - outcome = ret120 median > 0（primary）/ > 10%（strong-success sensitivity）
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import STUDY_DIR
from .context import (
    DIM_GROUP_ORDER, EQUAL_WEIGHTS, SENS_WEIGHTS, CONTINUOUS_FEATURES,
    bottom_depth_context, breadth_at, industry_context, load_full_etf_codes,
    load_market_index, market_context, recovery_context, synchronization_context,
)
from .episodes import INDUSTRY_CLUSTERS

logger = logging.getLogger(__name__)

STRONG_SUCCESS_THRESHOLD = 0.10


def _load_episodes() -> pd.DataFrame:
    """返回 episodes_df 含 cluster/start/is_current/ret120_median/episode_up。"""
    p = json.loads((STUDY_DIR / "bottom_episodes.json").read_text(encoding="utf-8"))
    rows = []
    for cluster, eps in p["clusters"].items():
        for e in eps:
            rows.append({
                "cluster": cluster,
                "start": pd.Timestamp(e["start"]),
                "is_current": bool(e["is_current"]),
                "ret120_median": e["returns"].get("ret120_median"),
                "episode_up": e["returns"].get("episode_up"),
            })
    df = pd.DataFrame(rows).sort_values("start").reset_index(drop=True)
    df["episode_id"] = df["cluster"] + "_" + df["start"].dt.strftime("%Y%m%d")
    return df


def compute_episode_contexts() -> pd.DataFrame:
    """计算全部 episode（历史+当前）的 context features。"""
    episodes = _load_episodes()
    market = load_market_index()
    full_codes = load_full_etf_codes()
    cache: dict[str, pd.DataFrame] = {}
    breadth_cache: dict[str, pd.DataFrame] = {}

    rows = []
    for _, ep in episodes.iterrows():
        dt = ep["start"]
        cluster = ep["cluster"]
        breadth = breadth_at(dt, full_codes, breadth_cache)
        mkt = market_context(dt, market, breadth)
        ind = industry_context(dt, cluster, market, cache)
        depth, in_bottom, state_counts = bottom_depth_context(dt, cluster, cache)
        sync = synchronization_context(dt, cluster, in_bottom, cache)
        rec = recovery_context(state_counts, cluster, in_bottom)
        row = {
            "episode_id": ep["episode_id"], "cluster": cluster,
            "start": dt, "is_current": ep["is_current"],
            "ret120_median": ep["ret120_median"], "episode_up": ep["episode_up"],
        }
        for k, v in mkt.items(): row[k] = v
        for k, v in ind.items(): row[k] = v
        for k, v in depth.items(): row[k] = v
        for k, v in sync.items(): row[k] = v
        for k, v in rec.items(): row[k] = v
        rows.append(row)
    return pd.DataFrame(rows)


def _scaler_historical(df: pd.DataFrame) -> dict[str, dict]:
    """只在历史 episode 上 fit mean/std（用户锁定）。"""
    hist = df[df["is_current"] == False]
    scaler = {}
    for grp, feats in CONTINUOUS_FEATURES.items():
        for f in feats:
            vals = pd.to_numeric(hist.get(f), errors="coerce").dropna()
            scaler[f] = {"mean": float(vals.mean()) if len(vals) else 0.0,
                         "std": float(vals.std()) if len(vals) > 1 else 1.0}
    return scaler


def _z(feats: dict[str, Any], scaler: dict[str, dict]) -> dict[str, float]:
    out = {}
    for f, v in feats.items():
        if v is None or (isinstance(v, float) and v != v):
            out[f] = np.nan
            continue
        s = scaler.get(f, {"mean": 0.0, "std": 1.0})
        out[f] = (float(v) - s["mean"]) / max(s["std"], 1e-9)
    return out


def _dim_distance(zcur: dict, zhist: dict, group: str) -> float:
    """组内平均 z² 再开方（用户口径 D_g = sqrt(mean_j (z_c,j - z_h,j)^2)）。"""
    feats = CONTINUOUS_FEATURES[group]
    sq = []
    for f in feats:
        a, b = zcur.get(f), zhist.get(f)
        if a is None or b is None or np.isnan(a) or np.isnan(b):
            continue
        sq.append((a - b) ** 2)
    if not sq:
        return np.nan
    return float(np.sqrt(np.mean(sq)))


def _total_distance(zcur: dict, zhist: dict, weights: dict[str, float]) -> float:
    """加权总距离 D = sqrt(sum_g w_g * D_g^2)。"""
    sq = []
    for g in DIM_GROUP_ORDER:
        dg = _dim_distance(zcur, zhist, g)
        if dg is None or np.isnan(dg):
            continue
        sq.append(weights[g] * dg ** 2)
    if not sq:
        return np.nan
    return float(np.sqrt(np.sum(sq)))


def run_context_matching(study_dir: Path | None = None) -> dict:
    """Study 2C 编排。"""
    study_dir = study_dir or STUDY_DIR
    study_dir.mkdir(parents=True, exist_ok=True)

    contexts = compute_episode_contexts()
    scaler = _scaler_historical(contexts)

    # z-transform（scaler 只 fit 历史，transform 全部）
    zmap = {}
    for _, r in contexts.iterrows():
        feats = {f: r.get(f) for grp in DIM_GROUP_ORDER for f in CONTINUOUS_FEATURES[grp]}
        zmap[r["episode_id"]] = _z(feats, scaler)

    hist = contexts[contexts["is_current"] == False]
    cur = contexts[contexts["is_current"] == True]

    # 匹配：每个当前 episode → 历史 Top3（两套权重）
    matches = {}
    for _, ce in cur.iterrows():
        zc = zmap[ce["episode_id"]]
        scored = []
        for _, he in hist.iterrows():
            zh = zmap[he["episode_id"]]
            d_eq = _total_distance(zc, zh, EQUAL_WEIGHTS)
            d_sens = _total_distance(zc, zh, SENS_WEIGHTS)
            if d_eq is None or np.isnan(d_eq):
                continue
            scored.append({
                "episode_id": he["episode_id"], "cluster": he["cluster"],
                "start": str(he["start"])[:10], "ret120_median": he["ret120_median"],
                "episode_up": he["episode_up"],
                "distance_equal": round(d_eq, 4), "distance_sensitivity": round(d_sens, 4) if d_sens is not None else None,
            })
        scored.sort(key=lambda x: x["distance_equal"])
        matches[ce["episode_id"]] = {
            "cluster": ce["cluster"], "start": str(ce["start"])[:10],
            "top3_equal": scored[:3],
            "top3_sensitivity": sorted(scored, key=lambda x: x["distance_sensitivity"])[:3] if scored else [],
        }

    # 成功/失败区分（核心交付）：历史 episode 按 outcome 分组，各维度均值
    hist = hist.copy()
    hist["success"] = hist["episode_up"].astype(bool)
    hist["strong_success"] = hist["ret120_median"] > STRONG_SUCCESS_THRESHOLD

    feature_stats = {}
    for grp, feats in CONTINUOUS_FEATURES.items():
        for f in feats:
            succ = pd.to_numeric(hist.loc[hist["success"], f], errors="coerce").dropna()
            fail = pd.to_numeric(hist.loc[~hist["success"], f], errors="coerce").dropna()
            strong = pd.to_numeric(hist.loc[hist["strong_success"], f], errors="coerce").dropna()
            feature_stats[f] = {
                "dim": grp,
                "success_mean": round(float(succ.mean()), 4) if len(succ) else None,
                "fail_mean": round(float(fail.mean()), 4) if len(fail) else None,
                "success_n": int(len(succ)), "fail_n": int(len(fail)),
                "strong_success_mean": round(float(strong.mean()), 4) if len(strong) else None,
                "strong_success_n": int(len(strong)),
            }

    # 分类标签汇总（regime / relative_mode / already_improving）
    label_summary = {
        "success_regime": _label_dist(hist, "market_regime", "success"),
        "success_mode": _label_dist(hist, "industry_relative_mode", "success"),
        "success_recovery": _label_dist(hist, "already_improving", "success"),
    }

    payload = {
        "study": "Study 2C Current Episode Context Matching",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_historical": int(len(hist)), "n_current": int(len(cur)),
        "contexts": contexts.to_dict("records"),
        "scaler_fit_on": "historical_only",
        "matches": matches,
        "feature_stats": feature_stats,
        "label_summary": label_summary,
        "strong_success_threshold": STRONG_SUCCESS_THRESHOLD,
    }
    out = study_dir / "context_matching.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    contexts.to_parquet(study_dir / "context_features.parquet", index=False)
    logger.info("study 2C context matching -> %s", out)
    return payload


def _label_dist(df: pd.DataFrame, col: str, outcome_col: str) -> dict:
    out = {}
    for label, g in df.groupby(col, dropna=True):
        n_succ = int(g[g[outcome_col]].shape[0])
        n_total = int(g.shape[0])
        out[str(label)] = {"n_total": n_total, "n_success": n_succ,
                           "success_rate": round(n_succ / max(n_total, 1), 3)}
    return out
