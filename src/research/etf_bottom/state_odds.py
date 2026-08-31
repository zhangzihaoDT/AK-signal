"""Study 2 — Price Bottom State Odds。

研究问题：历史进入 DEEP_BOTTOM / RECOVERING_FROM_BOTTOM / RECENT_BOTTOM 后，
未来 20D / 60D / 120D 的收益分布有何差异？三种底部状态的「赔率」是否不同？

事件语义（与 Study 1 一致）：
  Entry = off→on 转换：某 ETF 当日进入某底部状态（前一日非该状态）记为一个 entry；
  连续在状态内不重复计数（合并）；前向收益从 entry 日起算，close→close，无 look-ahead。

状态机（复用 price_map 语义）：
  daily price_pos_60/120/360（rolling min/max）
  折算窗口级污染（unreliable_N，60⊂120⊂360）
  bottom_state 互斥判定；UNRELIABLE / 货币债券不产生事件

口径：
  Universe = 729 FULL ETF（历史≥756D，跨年份事件量足）
  基准 = 同市场横截面中位前向收益（ClosePanel.benchmark_forward）
  折算事件单独标记，报告给「含折算 / 剔除折算」两列
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
from .price_map import POS_LOW_THRESHOLD, CA_RET_THRESHOLD, WINDOWS
from .returns import ClosePanel, augment_events
from .study import count_non_overlap
from .universe import calibrate_etf_type, load_full_etf_universe

logger = logging.getLogger(__name__)

BOTTOM_STATES = ("DEEP_BOTTOM", "RECOVERING_FROM_BOTTOM", "RECENT_BOTTOM")


def _corp_action_flags(close: pd.Series) -> pd.Series:
    """单日 |ret| >= 20% 疑似份额折算/公司行为（审计用）。"""
    return close.pct_change().abs() >= CA_RET_THRESHOLD


def _window_ca_flags(ca: np.ndarray, w: int) -> np.ndarray:
    """窗口 i 的折算污染 = 该窗口 [i-w+1, i] 内部跳变（pct_change 从 i-w+2 起算）是否含 >=20%。

    与 price_map 语义一致：折算点后短窗口先恢复、长窗口后恢复。
    """
    n = len(ca)
    out = np.zeros(n, dtype=bool)
    for i in range(n):
        lo = max(i - w + 2, 1)  # 窗口内第一个 pct_change index
        out[i] = bool(ca[lo:i + 1].any())
    return out


def daily_state_series(d: pd.DataFrame) -> pd.DataFrame:
    """逐日计算 price_pos_N / unreliable_N / bottom_state / long_term_bottom。

    返回 df 含 date/close/price_pos_{60,120,360}/unreliable_{60,120,360}/
    bottom_state/long_term_bottom/corp_action。
    """
    df = d.sort_values("date").reset_index(drop=True).copy()
    close = df["close"]
    ca = _corp_action_flags(close).to_numpy(bool)

    for w in WINDOWS:
        # rolling min/max 需要 min_periods=w（窗口完整才定义位置）
        lo = close.rolling(w, min_periods=w).min()
        hi = close.rolling(w, min_periods=w).max()
        pos = (close - lo) / (hi - lo) * 100.0
        df[f"price_pos_{w}"] = pos.where((hi > lo), 50.0)
        # 折算污染：窗口内（[i-w+1, i]）是否有疑似公司行为
        df[f"unreliable_{w}"] = _window_ca_flags(ca, w)

    df["corp_action"] = ca
    states = df.apply(lambda r: classify_from_row(r), axis=1)
    df["bottom_state"] = [s[0] for s in states]
    df["long_term_bottom"] = [s[1] for s in states]
    return df


def classify_from_row(r: pd.Series) -> tuple[str, bool]:
    """由单行窗口指标推出 bottom_state + long_term_bottom（复用 price_map 语义）。"""
    p60, p120, p360 = r.get("price_pos_60"), r.get("price_pos_120"), r.get("price_pos_360")
    u60, u120, u360 = r.get("unreliable_60"), r.get("unreliable_120"), r.get("unreliable_360")
    if pd.isna(p360) or u360:
        return "UNRELIABLE", False
    lo60 = (p60 <= POS_LOW_THRESHOLD) if not u60 else False
    lo120 = (p120 <= POS_LOW_THRESHOLD) if not u120 else False
    lo360 = p360 <= POS_LOW_THRESHOLD
    if lo60 and lo120 and lo360:
        state = "DEEP_BOTTOM"
    elif (not lo60) and lo120 and lo360:
        state = "RECOVERING_FROM_BOTTOM"
    elif lo60 and (not lo360):
        state = "RECENT_BOTTOM"
    else:
        state = "NORMAL"
    long_term = (not u120) and (not u360) and p120 <= POS_LOW_THRESHOLD and p360 <= POS_LOW_THRESHOLD
    return state, bool(long_term)


def extract_state_entries(states: pd.DataFrame, etf_type: str) -> list[dict[str, Any]]:
    """从 daily 状态序列提取三种底部状态的 off→on 转换事件。

    货币/债券不产生底部事件（P 分位无意义）。
    """
    if etf_type in ("money", "bond"):
        return []
    st = states["bottom_state"].to_numpy(str)
    dates = states["date"].to_numpy()
    rows: list[dict[str, Any]] = []
    prev = "NONE"
    for i, s in enumerate(st):
        if s in BOTTOM_STATES and s != prev:
            rows.append({
                "fund_code": states["fund_code"].iloc[0],
                "entry_date": dates[i],
                "state": s,
                "etf_type": etf_type,
                "corp_action_at_entry": bool(states["corp_action"].iloc[i]),
            })
        prev = s
    return rows


def run_state_odds(study_dir: Path | None = None) -> dict:
    """Study 2 编排：universe → daily 状态机 → 事件 → 前向收益 → 汇总 → 落盘。"""
    study_dir = study_dir or STUDY_DIR
    study_dir.mkdir(parents=True, exist_ok=True)

    universe = load_full_etf_universe()
    all_events: list[pd.DataFrame] = []
    for _, row in universe.iterrows():
        code = row["fund_code"]
        d = pd.read_parquet(f"data/etf_signal/raw/{code}.parquet",
                            columns=["date", "close", "fund_code"])
        states = daily_state_series(d)
        ev = extract_state_entries(states, row["etf_type"])
        if not ev:
            continue
        df = pd.DataFrame(ev)
        df["fund_name"] = row["fund_name"]
        all_events.append(df)
    events = pd.concat(all_events, ignore_index=True) if all_events else pd.DataFrame()
    logger.info("state events: %d", len(events))

    # 面板（全市场基准）+ 前向收益增强
    panel = _build_panel(universe)
    augmented = augment_events(events, panel, HORIZONS)

    # 折算：事件是否落在前向窗口内（可能污染前向收益）——含/剔除两列
    augmented = _flag_forward_corp_action(augmented, panel)

    summary = _summarize(augmented)
    by_type = _summarize_by(augmented, "etf_type")
    by_year = _summarize_by_year(augmented)

    events_path = study_dir / "state_odds_events.parquet"
    augmented.to_parquet(events_path, index=False)

    payload = {
        "study": "Study 2 Price Bottom State Odds",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "universe": {"n_full_etf": int(len(universe))},
        "params": {"windows": list(WINDOWS), "pos_low_threshold": POS_LOW_THRESHOLD,
                   "ca_threshold": CA_RET_THRESHOLD, "horizons": list(HORIZONS)},
        "summary": summary,
        "by_type": by_type,
        "by_year": by_year,
        "events_file": str(events_path),
        "events_count": int(len(augmented)),
    }
    out = study_dir / "state_odds_result.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("state odds result -> %s", out)
    return payload


def _build_panel(universe: pd.DataFrame) -> ClosePanel:
    pivots = []
    for code in universe["fund_code"]:
        d = pd.read_parquet(f"data/etf_signal/raw/{code}.parquet", columns=["date", "close"])
        pivots.append(pd.DataFrame({code: d.set_index("date")["close"]}))
    pivot = pd.concat(pivots, axis=1).sort_index()
    pivot = pivot[pivot.index.notna()]
    return ClosePanel(pivot)


def _flag_forward_corp_action(events: pd.DataFrame, panel: ClosePanel) -> pd.DataFrame:
    """标记前向窗口内是否有折算（可能污染前向收益），供含/剔除两列对比。"""
    out = events.copy()
    for _, ev in events.iterrows():
        code, date = ev["fund_code"], ev["entry_date"]
        idx = panel.date_pos(date)
        if idx is None or code not in panel.pivot.columns:
            continue
        s = panel.pivot[code].iloc[idx:idx + max(HORIZONS) + 1].dropna()
        rets = s.pct_change().abs()
        ca_h = bool((rets >= CA_RET_THRESHOLD).any())
        out.loc[ev.name, "corp_action_in_forward"] = ca_h
    if "corp_action_in_forward" not in out.columns:
        out["corp_action_in_forward"] = False
    return out


def _agg(vals) -> dict:
    if vals is None or len(vals) == 0:
        return {"n": 0, "mean": None, "median": None, "win_rate": None, "p25": None, "p75": None, "p90": None}
    v = pd.to_numeric(vals, errors="coerce").dropna()
    if v.empty:
        return {"n": 0, "mean": None, "median": None, "win_rate": None, "p25": None, "p75": None, "p90": None}
    return {
        "n": int(len(v)),
        "mean": round(float(v.mean()), 4),
        "median": round(float(v.median()), 4),
        "win_rate": round(float((v > 0).mean()), 4),
        "p25": round(float(v.quantile(0.25)), 4),
        "p75": round(float(v.quantile(0.75)), 4),
        "p90": round(float(v.quantile(0.90)), 4),
    }


def _state_summary(sub: pd.DataFrame) -> dict:
    rec: dict[str, dict] = {}
    for h in HORIZONS:
        clean = sub[sub["corp_action_in_forward"] == False]
        rec[str(h)] = {
            "ret": _agg(sub.get(f"ret_{h}")),
            "ret_clean": _agg(clean.get(f"ret_{h}")),
            "excess": _agg(sub.get(f"excess_{h}")),
            "mfe": _agg(sub.get(f"mfe_{h}")),
            "mae": _agg(sub.get(f"mae_{h}")),
        }
    rec["_meta"] = {
        "n_events": int(len(sub)),
        "n_etfs": int(sub["fund_code"].nunique()),
        "n_non_overlap": {str(h): count_non_overlap(sub, h) for h in HORIZONS},
        "n_corp_action_forward": int(sub["corp_action_in_forward"].sum()),
        "n_entries_per_etf": _agg(sub.groupby("fund_code").size()),
    }
    return rec


def _summarize(events: pd.DataFrame) -> dict:
    out = {}
    for state in BOTTOM_STATES:
        sub = events[events["state"] == state]
        out[state] = _state_summary(sub)
    return out


def _summarize_by(events: pd.DataFrame, col: str) -> dict:
    out = {}
    for state in BOTTOM_STATES:
        sub = events[events["state"] == state]
        by = {}
        for key, g in sub.groupby(col):
            by[str(key)] = _state_summary(g)
        out[state] = by
    return out


def _summarize_by_year(events: pd.DataFrame) -> dict:
    ev = events.copy()
    ev["year"] = pd.to_datetime(ev["entry_date"]).dt.year
    out = {}
    for state in BOTTOM_STATES:
        sub = ev[ev["state"] == state]
        by = {}
        for y, g in sub.groupby("year"):
            by[str(int(y))] = _state_summary(g)
        out[state] = by
    return out
