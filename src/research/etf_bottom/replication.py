"""Study 2D — Context Broad-Sample Replication（大样本复现）。

验证 2C 的 20 个 episode 发现能否在更大、不同 ETF 样本（13855 个长期底部 entry）上复现。
注意：这不是 OOS（仍同段 2021-2026 历史），真正 OOS 留给未来数据。

冻结特征（entry 当日可观察，绝不使用未来状态）：
  asset_excess_60d / asset_excess_120d   # ETF vs HS300（单资产，非产业）
  price_pos_60 / 120 / 360               # compute_row（entry 当日）
  dd60                                    # 60D 回撤（2C 否定项复核）
  market_ret_60d                          # HS300 60D

双 outcome：
  primary   excess_vs_etf_market_120d = ret_120 - bench_120（ETF 横截面中位前向）
  secondary excess_vs_hs300_120d = ret_120 - hs300_forward_120

双 aggregation：event-weighted（每 entry 一票）/ ETF-balanced（ETF × quintile 组内 median 后每 ETF 一票）

两层 quintile：Layer1 全样本绝对 quintile（pooled）；Layer2 年内 quintile（主判据，2021-2025 多数年份方向一致）
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.common.paths import etf_signal_raw_dir

from . import STUDY_DIR
from .context import _ret_at, load_market_index
from .price_map import compute_row
from .returns import ClosePanel
from .study import count_non_overlap

logger = logging.getLogger(__name__)

FEATURES = ["asset_excess_60d", "asset_excess_120d", "price_pos_120", "dd60",
            "market_ret_60d", "price_pos_60", "price_pos_360"]
OUTCOME = "excess_vs_etf_market_120d"
OUTCOME_HS = "excess_vs_hs300_120d"
N_QUINTILES = 5
YEARS_MAIN = [2021, 2022, 2023, 2024, 2025]  # 正式跨年判据（2026 样本不足）


def _load_bottom_entries() -> pd.DataFrame:
    """长期底部 entry 事件（DEEP + RECOVERING），ret_120 可用。"""
    ev = pd.read_parquet(STUDY_DIR / "state_odds_events.parquet")
    lt = ev[ev["state"].isin(["DEEP_BOTTOM", "RECOVERING_FROM_BOTTOM"])].copy()
    lt["entry_date"] = pd.to_datetime(lt["entry_date"])
    return lt[lt["ret_120"].notna()].reset_index(drop=True)


def _hs300_forward(entry_date, hs300_close: pd.Series, horizon: int) -> float | None:
    """HS300 从 entry_date 起的前向 horizon 日收益（同一交易日口径）。"""
    try:
        idx = hs300_close.index.get_loc(pd.Timestamp(entry_date))
    except KeyError:
        return None
    j = idx + horizon
    if j >= len(hs300_close) or pd.isna(hs300_close.iloc[idx]) or pd.isna(hs300_close.iloc[j]):
        return None
    return float(hs300_close.iloc[j] / hs300_close.iloc[idx] - 1.0)


def enhance_entries(df: pd.DataFrame) -> pd.DataFrame:
    """为每个 entry 补齐冻结特征 + 双 outcome。"""
    market = load_market_index()
    hs300_close = market.set_index("date")["close"]
    out = df.copy()

    for _, row in df.iterrows():
        code, dt = row["fund_code"], row["entry_date"]
        d = pd.read_parquet(f"data/etf_signal/raw/{code}.parquet",
                            columns=["date", "close"]).sort_values("date").reset_index(drop=True)
        r = compute_row(code, "", "", d, dt)
        etf60 = _ret_at(d, dt, 60)
        etf120 = _ret_at(d, dt, 120)
        m60 = _ret_at(market, dt, 60)
        m120 = _ret_at(market, dt, 120)
        out.loc[row.name, "asset_excess_60d"] = (etf60 - m60) if (etf60 is not None and m60 is not None) else None
        out.loc[row.name, "asset_excess_120d"] = (etf120 - m120) if (etf120 is not None and m120 is not None) else None
        out.loc[row.name, "price_pos_60"] = r.get("price_pos_60")
        out.loc[row.name, "price_pos_120"] = r.get("price_pos_120")
        out.loc[row.name, "price_pos_360"] = r.get("price_pos_360")
        out.loc[row.name, "market_ret_60d"] = m60
        # dd60：60D 回撤（close / rolling60 max - 1）
        sub = d[d["date"] <= dt].reset_index(drop=True)
        out.loc[row.name, "dd60"] = (float(sub["close"].iloc[-1] / sub["close"].iloc[-60:].max() - 1.0)
                                     if len(sub) >= 60 else None)
        out.loc[row.name, OUTCOME] = row.get("excess_120")  # ret_120 - bench_120（已有）
        hs = _hs300_forward(dt, hs300_close, 120)
        out.loc[row.name, OUTCOME_HS] = (row.get("ret_120") - hs) if (hs is not None and pd.notna(row.get("ret_120"))) else None
    return out


def _quintile_labels(s: pd.Series, n: int = N_QUINTILES) -> pd.Series:
    """绝对五分位（Q1=最弱/最低）。"""
    q = s.quantile([i / n for i in range(1, n)])
    labels = {1: "Q1", 2: "Q2", 3: "Q3", 4: "Q4", 5: "Q5"}
    def _label(v):
        if pd.isna(v):
            return np.nan
        for i in range(n, 0, -1):
            if i == 1:
                return labels[i]
            if v > q.iloc[i - 2]:
                return labels[i]
        return "Q1"
    return s.map(_label)


def _quintile_by_year(s: pd.Series, years: pd.Series, n: int = N_QUINTILES) -> pd.Series:
    """年内五分位（每年内部独立切分）。"""
    out = pd.Series(np.nan, index=s.index, dtype=object)
    for y in years.unique():
        mask = years == y
        sub = s[mask]
        if len(sub.dropna()) < n:
            continue
        q = sub.quantile([i / n for i in range(1, n)])
        def _label(v):
            if pd.isna(v):
                return np.nan
            for i in range(n, 0, -1):
                if i == 1:
                    return f"Q{i}"
                if v > q.iloc[i - 2]:
                    return f"Q{i}"
            return "Q1"
        out[mask] = sub.map(_label)
    return out


def _agg_outcome(vals, hs_vals) -> dict:
    v = pd.to_numeric(vals, errors="coerce").dropna()
    hv = pd.to_numeric(hs_vals, errors="coerce").dropna()
    return {
        "n": int(len(v)),
        "median_120d": round(float(v.median()), 4) if len(v) else None,
        "win_rate": round(float((v > 0).mean()), 4) if len(v) else None,
        "excess_etf_market": round(float(v.mean()), 4) if len(v) else None,
        "excess_hs300": round(float(hv.mean()), 4) if len(hv) else None,
        "n_hs300": int(len(hv)),
    }


def _quintile_table(df: pd.DataFrame, feature: str, qcol: str) -> list[dict]:
    rows = []
    for q in ["Q1", "Q2", "Q3", "Q4", "Q5"]:
        sub = df[df[qcol] == q]
        if sub.empty:
            continue
        rec = {"quintile": q}
        rec.update(_agg_outcome(sub["ret_120"], sub.get(OUTCOME_HS)))
        rows.append(rec)
    return rows


def _etf_balanced(df: pd.DataFrame, feature: str, qcol: str) -> list[dict]:
    """ETF × quintile 组内 median outcome，再每 ETF 一票。"""
    rows = []
    for q in ["Q1", "Q2", "Q3", "Q4", "Q5"]:
        sub = df[df[qcol] == q]
        if sub.empty:
            continue
        g = sub.groupby("fund_code")["ret_120"].median()
        gh = sub.groupby("fund_code")[OUTCOME_HS].median()
        rows.append({
            "quintile": q,
            "n_etfs": int(len(g)),
            "median_120d": round(float(g.median()), 4) if len(g) else None,
            "win_rate": round(float((g > 0).mean()), 4) if len(g) else None,
            "excess_etf_market": round(float(g.mean()), 4) if len(g) else None,
            "excess_hs300": round(float(gh.mean()), 4) if len(gh) else None,
        })
    return rows


def run_replication(study_dir: Path | None = None) -> dict:
    """Study 2D 编排。"""
    study_dir = study_dir or STUDY_DIR
    study_dir.mkdir(parents=True, exist_ok=True)

    entries = _load_bottom_entries()
    entries = enhance_entries(entries)
    entries["year"] = entries["entry_date"].dt.year

    # n_non_overlap（每 horizon 的非重叠 entry 数）
    nol = {str(h): count_non_overlap(entries, h) for h in (20, 60, 120)}

    # Layer 1：全样本绝对 quintile
    layer1 = {}
    for f in FEATURES:
        entries[f"_q_abs_{f}"] = _quintile_labels(entries[f])
        layer1[f] = {
            "event_weighted": _quintile_table(entries, f, f"_q_abs_{f}"),
            "etf_balanced": _etf_balanced(entries, f, f"_q_abs_{f}"),
        }

    # Layer 2：年内 quintile（主判据）
    layer2 = {}
    for f in FEATURES:
        entries[f"_q_yr_{f}"] = _quintile_by_year(entries[f], entries["year"])
        yr_rows = []
        for y in sorted(entries["year"].unique()):
            sub = entries[entries["year"] == y]
            q1 = sub[sub[f"_q_yr_{f}"] == "Q1"]
            q5 = sub[sub[f"_q_yr_{f}"] == "Q5"]
            if q1.empty or q5.empty:
                continue
            r1 = _agg_outcome(q1["ret_120"], q1.get(OUTCOME_HS))
            r5 = _agg_outcome(q5["ret_120"], q5.get(OUTCOME_HS))
            spread = (r1["excess_etf_market"] - r5["excess_etf_market"]) if (r1["excess_etf_market"] is not None and r5["excess_etf_market"] is not None) else None
            spread_hs = (r1["excess_hs300"] - r5["excess_hs300"]) if (r1["excess_hs300"] is not None and r5["excess_hs300"] is not None) else None
            yr_rows.append({
                "year": int(y), "n_q1": r1["n"], "n_q5": r5["n"],
                "q1_excess_etf": r1["excess_etf_market"], "q5_excess_etf": r5["excess_etf_market"],
                "q1_q5_spread": spread, "q1_q5_spread_hs300": spread_hs,
                "q1_win": r1["win_rate"], "q5_win": r5["win_rate"],
                "is_main_year": int(y) in YEARS_MAIN,
            })
        layer2[f] = {"by_year": yr_rows}

    # Layer 3：2C vs Broad 方向一致性
    layer3 = _build_layer3(layer1, layer2)

    payload = {
        "study": "Study 2D Context Broad-Sample Replication",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_entries": int(len(entries)),
        "n_etfs": int(entries["fund_code"].nunique()),
        "n_entry_dates": int(entries["entry_date"].dt.date.nunique()),
        "n_non_overlap": nol,
        "features": FEATURES,
        "layer1": layer1,
        "layer2": layer2,
        "layer3": layer3,
    }
    out = study_dir / "context_replication.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    entries.to_parquet(study_dir / "context_replication_events.parquet", index=False)
    logger.info("study 2D replication -> %s", out)
    return payload


def _build_layer3(layer1: dict, layer2: dict) -> list[dict]:
    """2C 方向 vs Broad 复现方向的一致性表。"""
    # 2C 结论（来自 context_matching.json 的 feature_stats）
    try:
        c2 = json.loads((STUDY_DIR / "context_matching.json").read_text(encoding="utf-8"))["feature_stats"]
    except Exception:
        c2 = {}
    rows = []
    spec = [
        # (feature, 2C 成功均值, 2C 失败均值, 方向: 正值表示"高→好"或"负值表示低→好")
        ("asset_excess_60d", "industry_excess_60d", "neg"),
        ("price_pos_120", "pos120", "pos"),
        ("market_ret_60d", "market_ret_60d", "pos"),
        ("dd60", "dd60", "none"),
        ("price_pos_360", "pos360", "none"),
    ]
    for feature, c2f, direction in spec:
        # Broad 全样本 Q1 vs Q5（excess_etf_market）
        ev = layer1.get(feature, {}).get("event_weighted", [])
        q1 = next((r for r in ev if r["quintile"] == "Q1"), {})
        q5 = next((r for r in ev if r["quintile"] == "Q5"), {})
        q1e, q5e = q1.get("excess_etf_market"), q5.get("excess_etf_market")
        broad_dir = (q1e - q5e) if (q1e is not None and q5e is not None) else None
        # 年内主判据：多数年份 Q1-Q5 spread 方向
        by_year = layer2.get(feature, {}).get("by_year", [])
        main_years = [r for r in by_year if r["is_main_year"] and r["q1_q5_spread"] is not None]
        n_pos = sum(1 for r in main_years if r["q1_q5_spread"] > 0)
        n_neg = sum(1 for r in main_years if r["q1_q5_spread"] < 0)
        year_verdict = "REPLICATED" if n_pos > n_neg and len(main_years) >= 3 else ("PARTIAL" if main_years else "INSUFFICIENT")
        # 2C 方向
        c2s = c2.get(c2f, {})
        c2_dir = None
        if c2s.get("success_mean") is not None and c2s.get("fail_mean") is not None:
            c2_dir = (c2s["success_mean"] - c2s["fail_mean"]) > 0
        rows.append({
            "feature": feature,
            "c2_feature": c2f,
            "c2_direction": "success_higher" if c2_dir else ("success_lower" if c2_dir is not None else "none"),
            "broad_q1_excess": q1e, "broad_q5_excess": q5e,
            "broad_q1_q5_spread": broad_dir,
            "year_positive_count": n_pos, "year_negative_count": n_neg,
            "year_total_main": len(main_years),
            "year_verdict": year_verdict,
        })
    return rows
