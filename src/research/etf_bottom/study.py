"""Study 1 编排：加载 universe → 状态 → 事件 → 前向收益 → 汇总 → 落盘。

汇总输出（用户锁定的 Stage 2 表格）：
  每个 Entry state × horizon：mean/median/win_rate/MAE/MFE/n
辅助：
  days_low_to_ma20 / days_low_to_ma60 分布
  n_entries 每 ETF 事件数分布
  按 etf_type 分层（校准后）
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import STUDY_DIR, HORIZONS
from .states import compute_states, extract_events
from .returns import ClosePanel, augment_events
from .universe import load_full_etf_universe, taxonomy_audit

logger = logging.getLogger(__name__)

ENTRY_TYPES = ("PRICE_LOW", "PRICE_LOW_DD30", "MA20_RECOVERY", "MA60_RECOVERY")


def _agg(s: Any) -> dict:
    if s is None:
        return {"n": 0, "mean": None, "median": None, "win_rate": None}
    if not isinstance(s, pd.Series):
        s = pd.Series(s)
    v = pd.to_numeric(s, errors="coerce").dropna()
    if v.empty:
        return {"n": 0, "mean": None, "median": None, "win_rate": None}
    return {
        "n": int(len(v)),
        "mean": round(float(v.mean()), 4),
        "median": round(float(v.median()), 4),
        "win_rate": round(float((v > 0).mean()), 4),
    }


def build_panel(universe: pd.DataFrame) -> ClosePanel:
    """构建 FULL ETF close 面板。"""
    pivots = []
    for code in universe["fund_code"]:
        d = pd.read_parquet(f"data/etf_signal/raw/{code}.parquet", columns=["date", "close"])
        s = d.set_index("date")["close"]
        pivots.append(pd.DataFrame({code: s}))
    pivot = pd.concat(pivots, axis=1).sort_index()
    pivot = pivot[pivot.index.notna()]
    logger.info("close panel: %s", pivot.shape)
    return ClosePanel(pivot)


def _biz_days_between(a, b) -> int:
    try:
        s = pd.Timestamp(a).strftime("%Y-%m-%d")
        e = pd.Timestamp(b).strftime("%Y-%m-%d")
        return int(np.busday_count(np.datetime64(s), np.datetime64(e), weekmask="1111100"))
    except Exception:
        return 0


def count_non_overlap(sub: pd.DataFrame, horizon: int) -> int:
    """组内（按 fund_code）互不重叠的事件数：相邻事件间隔 ≥ horizon 交易日。"""
    total = 0
    for _code, g in sub.groupby("fund_code"):
        g = g.sort_values("entry_date")
        last = None
        for t in g["entry_date"]:
            if last is None or _biz_days_between(last, t) >= horizon:
                total += 1
                last = t
    return total


def dd30_sensitivity(events: pd.DataFrame) -> dict:
    """DD30 深跌分档：回答「又低又深跌」是更好还是更危险（用户第二问）。"""
    sub = events[events["event_type"] == "PRICE_LOW"].copy()
    sub = sub.dropna(subset=["dd30_at_entry"])
    bands = [(-1.0, -0.30), (-0.30, -0.20), (-0.20, -0.10), (-0.10, 0.0)]
    out = {}
    for lo, hi in bands:
        g = sub[(sub["dd30_at_entry"] > lo) & (sub["dd30_at_entry"] <= hi)]
        out[f"{lo*100:+.0f}~{hi*100:+.0f}%"] = {
            str(h): {
                "ret": _agg(g.get(f"ret_{h}")),
                "n_non_overlap": count_non_overlap(g, h),
            }
            for h in HORIZONS
        } | {"n": int(len(g))}
    return out


def summarize(events: pd.DataFrame) -> dict:
    """按 (event_type) 汇总，返回研究级 summary。"""
    out: dict[str, dict] = {}
    for etype in ENTRY_TYPES:
        sub = events[events["event_type"] == etype]
        rec: dict[str, dict] = {}
        n_events = len(sub)
        n_etfs = sub["fund_code"].nunique() if n_events else 0
        for h in HORIZONS:
            rec[str(h)] = {
                "ret": _agg(sub.get(f"ret_{h}")),
                "excess": _agg(sub.get(f"excess_{h}")),
                "mfe": _agg(sub.get(f"mfe_{h}")),
                "mae": _agg(sub.get(f"mae_{h}")),
            }
        rec["_meta"] = {"n_events": n_events, "n_etfs": n_etfs}
        if n_events:
            rec["_days_low_to_ma20"] = _agg(sub.get("days_low_to_ma20"))
            rec["_days_low_to_ma60"] = _agg(sub.get("days_low_to_ma60"))
            rec["_n_entries_per_etf"] = _agg(sub.groupby("fund_code").size())
            rec["_censored_recovery"] = int(sub["days_low_to_ma20"].isna().sum())
            rec["_n_non_overlap"] = {str(h): count_non_overlap(sub, h) for h in HORIZONS}
        out[etype] = rec
    return out


def summarize_by_type(events: pd.DataFrame) -> dict:
    """按 (etf_type, event_type) 分层汇总（20D/60D/120D 收益）。"""
    out: dict[str, dict] = {}
    if "etf_type" not in events.columns:
        return out
    for etype in ENTRY_TYPES:
        sub = events[events["event_type"] == etype]
        for et, g in sub.groupby("etf_type"):
            out.setdefault(str(etype), {})[str(et)] = {
                str(h): {"ret": _agg(g.get(f"ret_{h}")), "n": int(pd.to_numeric(g.get(f"ret_{h}"), errors="coerce").notna().sum())}
                for h in HORIZONS
            }
    return out


def run_study(universe: pd.DataFrame, events: pd.DataFrame, panel: ClosePanel) -> dict:
    logger.info("augmenting %d events with forward returns", len(events))
    augmented = augment_events(events, panel, HORIZONS)
    summary = summarize(augmented)
    # 核心口径：剔除货币/债券（价格近零波动，P756 分位无意义，会稀释收益结论）
    equity = augmented[~augmented["etf_type"].isin(["money", "bond"])].copy()
    summary_equity = summarize(equity)
    dd30_equity = dd30_sensitivity(equity)
    by_type = summarize_by_type(augmented)
    return {
        "summary": summary,
        "summary_equity": summary_equity,
        "dd30_sensitivity": dd30_equity,
        "by_type": by_type,
        "events": augmented,
    }


def run(study_dir: Path | None = None) -> dict:
    study_dir = study_dir or STUDY_DIR
    study_dir.mkdir(parents=True, exist_ok=True)

    universe = load_full_etf_universe()
    universe = taxonomy_audit(universe)
    universe.to_parquet(study_dir / "universe.parquet", index=False)

    # 状态 + 事件
    all_events: list[pd.DataFrame] = []
    states_by_code: dict[str, pd.DataFrame] = {}
    for _, row in universe.iterrows():
        code = row["fund_code"]
        d = pd.read_parquet(f"data/etf_signal/raw/{code}.parquet", columns=["date", "open", "high", "low", "close", "volume", "amount", "fund_code"])
        states = compute_states(d)
        states_by_code[code] = states
        ev = extract_events(states)
        if not ev:
            continue
        df = pd.DataFrame(ev)
        df["fund_name"] = row["fund_name"]
        df["etf_type"] = row["etf_type"]
        df["orig_bucket"] = row["orig_bucket"]
        all_events.append(df)
    events = pd.concat(all_events, ignore_index=True) if all_events else pd.DataFrame()
    logger.info("total raw events: %d", len(events))

    # 面板（全市场基准）
    panel = build_panel(universe)

    result = run_study(universe, events, panel)

    events_path = study_dir / "events.parquet"
    result["events"].to_parquet(events_path, index=False)

    # Study 1B Deep Stress Robustness（三重压力测试）
    from .robustness import run_robustness
    robustness = run_robustness(result["events"], states_by_code)

    # JSON 汇总（去事件级，只留 summary + by_type + provenance）
    payload = {
        "study": "Study 1 Price Bottom (Lane 2 Core)",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "universe": {
            "n_full_etf": int(len(universe)),
            "type_distribution": universe["etf_type"].value_counts().to_dict(),
            "date_range": [str(universe["start_date"].min()), str(universe["end_date"].max())],
        },
        "params": {
            "p_window": 756, "p_low_threshold": 20.0,
            "dd30_window": 30, "dd30_threshold": -0.20,
            "ma20_window": 20, "ma60_window": 60,
            "horizons": list(HORIZONS),
        },
        "summary": result["summary"],
        "summary_equity": result["summary_equity"],
        "by_type": result["by_type"],
        "dd30_sensitivity": result["dd30_sensitivity"],
        "robustness": robustness,
        "taxonomy_audit": json.loads((study_dir / "taxonomy_audit.json").read_text(encoding="utf-8")),
        "events_file": str(events_path),
        "events_count": int(len(result["events"])),
    }
    out = study_dir / "study1_result.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("study1 result -> %s", out)
    return payload
