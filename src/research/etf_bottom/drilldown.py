"""Study 2A — Current Bottom ETF Drilldown。

对当前处于长期价格底部（long_term_bottom，2026-08-28 快照）的 29 只 ETF，
逐只回顾它自己历史上进入长期底部的每一次先例，统计每次之后 20/60/120D
前向收益，回答：「这一只是有历史支持的低位，还是只是看起来便宜？」

口径：
- Universe = price_map_20260828 的 long_term_bottom == True 的 ETF
- 历史事件 = 该 ETF 全历史上 long_term_bottom 的 off→on 转换（当前仍在状态内的一段记为 current episode，不参与历史统计）
- 前向收益 close→close，20/60/120 交易日，横截面中位为参照（不设成败阈值，只报告）
- 判断：有历史支持 = 历史先例 ≥2 且 120D 中位收益 > 0 且 120D 胜率 ≥ 0.5
        历史不支持 = 先例 ≥2 但 120D 中位 ≤0 或胜率 <0.5
        证据不足 = 先例 <2
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

from . import STUDY_DIR, HORIZONS
from .price_map import build_price_map
from .returns import ClosePanel
from .state_odds import daily_state_series
from .universe import calibrate_etf_type

logger = logging.getLogger(__name__)


def _current_bottom_etfs() -> pd.DataFrame:
    """当前 29 只长期底部 ETF（price_map 快照）。"""
    pm = build_price_map("2026-08-28")
    return pm[pm["long_term_bottom"] == True].reset_index(drop=True)


def _long_term_entries(states: pd.DataFrame) -> list[dict[str, Any]]:
    """long_term_bottom 的 off→on 转换事件（含当前仍在状态内的段）。"""
    lt = states["long_term_bottom"].to_numpy(bool)
    n = len(states)
    rows: list[dict[str, Any]] = []
    i = 0
    while i < n:
        if not lt[i]:
            i += 1
            continue
        seg_start = i
        j = i
        while j < n and lt[j]:
            j += 1
        seg_end = j  # exclusive
        is_current = seg_end >= n  # 状态持续到末尾 → 当前仍处于低位段
        rows.append({
            "fund_code": states["fund_code"].iloc[0],
            "entry_date": states["date"].iloc[seg_start],
            "days_in_state": seg_end - seg_start,
            "is_current_episode": bool(is_current),
        })
        i = seg_end
    return rows


def _agg(v) -> dict:
    if v is None:
        return {"n": 0, "mean": None, "median": None, "win_rate": None}
    vals = pd.to_numeric(v, errors="coerce").dropna()
    if vals.empty:
        return {"n": 0, "mean": None, "median": None, "win_rate": None}
    return {
        "n": int(len(vals)),
        "mean": round(float(vals.mean()), 4),
        "median": round(float(vals.median()), 4),
        "win_rate": round(float((vals > 0).mean()), 4),
    }


def _build_panel(codes: list[str]) -> ClosePanel:
    pivots = []
    for code in codes:
        d = pd.read_parquet(f"data/etf_signal/raw/{code}.parquet", columns=["date", "close"])
        pivots.append(pd.DataFrame({code: d.set_index("date")["close"]}))
    pivot = pd.concat(pivots, axis=1).sort_index()
    pivot = pivot[pivot.index.notna()]
    return ClosePanel(pivot)


def run_drilldown(study_dir: Path | None = None) -> dict:
    """Study 2A 编排：29 只当前底部 ETF → 逐只历史先例 → 前向收益 → 判断 → 落盘。"""
    study_dir = study_dir or STUDY_DIR
    study_dir.mkdir(parents=True, exist_ok=True)

    current = _current_bottom_etfs()
    codes = current["fund_code"].tolist()
    panel = _build_panel(codes)

    detail: list[dict[str, Any]] = []
    for _, row in current.iterrows():
        code = row["fund_code"]
        d = pd.read_parquet(f"data/etf_signal/raw/{code}.parquet",
                            columns=["date", "close", "fund_code"])
        states = daily_state_series(d)
        entries = _long_term_entries(states)

        # 历史先例（排除当前段）+ 当前段信息
        hist = [e for e in entries if not e["is_current_episode"]]
        cur = [e for e in entries if e["is_current_episode"]]

        # 当前段信息（价格位置/距低点/持续天数）
        last = states.iloc[-1]
        cur_info = {
            "days_in_current_bottom": int(cur[0]["days_in_state"]) if cur else 0,
            "current_price_pos_120": round(float(last["price_pos_120"]), 2) if pd.notna(last["price_pos_120"]) else None,
            "current_price_pos_360": round(float(last["price_pos_360"]), 2) if pd.notna(last["price_pos_360"]) else None,
            "current_close": round(float(last["close"]), 4),
        }

        # 历史先例前向收益
        hist_rows: list[dict[str, Any]] = []
        for e in hist:
            rets = panel.forward_returns(code, e["entry_date"], HORIZONS)
            exc = panel.excursions(code, e["entry_date"], HORIZONS)
            hist_rows.append({
                "fund_code": code, "entry_date": str(pd.Timestamp(e["entry_date"]).date()),
                "days_in_state": e["days_in_state"],
                **{f"ret_{h}": rets.get(h) for h in HORIZONS},
                **{f"mfe_{h}": exc.get(h, (None, None))[0] for h in HORIZONS},
                **{f"mae_{h}": exc.get(h, (None, None))[1] for h in HORIZONS},
            })

        # 历史赔率汇总
        stats = {str(h): _agg(pd.Series([r[f"ret_{h}"] for r in hist_rows])) for h in HORIZONS}
        n_hist = len(hist_rows)

        # 判断
        r120 = stats["120"]
        if n_hist < 2:
            support = "证据不足"
        elif r120["n"] == 0:
            # 有先例但历史 120D 前向样本全部未到期（先例过近，如 2026 年）
            support = "证据不足"
        elif r120["median"] is not None and r120["median"] > 0 and (r120["win_rate"] or 0) >= 0.5:
            support = "历史支持"
        else:
            support = "历史不支持"

        detail.append({
            "fund_code": code,
            "fund_name": row["fund_name"],
            "etf_type": row["etf_type"],
            "n_hist_entries": n_hist,
            "support_level": support,
            "hist": stats,
            "current": cur_info,
            "hist_events": hist_rows,
        })

    # 汇总：按 support_level 分组统计（每只 ETF 一个观察）
    summary = {"历史支持": 0, "历史不支持": 0, "证据不足": 0}
    for rec in detail:
        summary[rec["support_level"]] = summary.get(rec["support_level"], 0) + 1

    # 落盘：事件级 + 每 ETF 汇总
    all_events = [ev for rec in detail for ev in rec["hist_events"]]
    events_df = pd.DataFrame(all_events)
    events_path = study_dir / "state_odds_drilldown_events.parquet"
    events_df.to_parquet(events_path, index=False)

    payload = {
        "study": "Study 2A Current Bottom ETF Drilldown",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of": "2026-08-28",
        "n_etfs": len(detail),
        "support_summary": summary,
        "etfs": detail,
        "events_file": str(events_path),
    }
    out = study_dir / "state_odds_drilldown.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("study 2A drilldown -> %s", out)
    return payload
