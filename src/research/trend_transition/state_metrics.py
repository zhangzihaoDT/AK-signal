"""Study 3C · 市场层统计（state_metrics）。

职责（§19-§25）：
  - 全市场 breadth：bottom_breadth / transition_breadth / active_transition_breadth
    （分母 = 当日 reliable_360 == True 的 ETF，§6 UNRELIABLE 不进分母）。
  - Transition Flow（§20）：每日 new_* 计数 + net_transition_flow = new_first_exit − new_retest。
  - Transition Momentum（§21）：transition_breadth / active_transition_breadth 的 5d/20d 变化。
  - ETF Type 分层（§23）：各 etf_type 的 breadth + flow。
  - Lane 2 联动（§25）：BOTTOM/RETEST × target_stage（TARGET/NEAR_MISS）。
  - Lane 1 联动（§24，辅助验证）：TRANSITION_* × trend_state（只读，不改变 state；
    覆盖率不影响 3C PASS/FAIL）。

renderer 不重算：所有市场层统计在此模块一次性算好落盘。
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from .state_classifier import (
    STATE_BOTTOM,
    STATE_FIRST_EXIT,
    STATE_POST_TRANSITION,
    STATE_RETEST,
    STATE_TRANSITION_ACTIVE,
    STATE_TRANSITION_EARLY,
    STATE_TRANSITION_ESTABLISHED,
    STATE_UNRELIABLE,
)

logger = logging.getLogger(__name__)

_TRANSITION_STATES = (
    STATE_FIRST_EXIT, STATE_TRANSITION_EARLY, STATE_TRANSITION_ACTIVE,
    STATE_TRANSITION_ESTABLISHED,
)
_ACTIVE_TRANSITION_STATES = (
    STATE_FIRST_EXIT, STATE_TRANSITION_EARLY, STATE_TRANSITION_ACTIVE,
)

STATE_LABEL_CN = {
    STATE_UNRELIABLE: "数据不足",
    STATE_BOTTOM: "长期底部",
    STATE_FIRST_EXIT: "刚离开底部",
    STATE_TRANSITION_EARLY: "趋势切换·早期",
    STATE_TRANSITION_ACTIVE: "趋势切换·进行中",
    STATE_TRANSITION_ESTABLISHED: "趋势切换·已建立",
    STATE_RETEST: "重新回到底部",
    STATE_POST_TRANSITION: "已完成底部切换",
}


def compute_state_distribution(hist: pd.DataFrame) -> pd.DataFrame:
    """逐日各状态计数（reliable 口径：只有 reliable_360 计入）。"""
    rel = hist[hist["reliable_360"]]
    return rel.pivot_table(index="trade_date", columns="transition_state",
                           values="fund_code", aggfunc="count", fill_value=0)


def compute_transition_breadth(hist: pd.DataFrame) -> pd.DataFrame:
    """逐日 breadth（分母 = reliable ETF 数）。"""
    rel = hist[hist["reliable_360"]]
    daily = rel.groupby("trade_date")
    denom = daily["fund_code"].count()
    dist = daily["transition_state"].value_counts().unstack(fill_value=0)
    bottom = dist.get(STATE_BOTTOM, 0) + dist.get(STATE_RETEST, 0)
    trans = sum(dist.get(s, 0) for s in _TRANSITION_STATES)
    active = sum(dist.get(s, 0) for s in _ACTIVE_TRANSITION_STATES)
    out = pd.DataFrame({
        "reliable_count": denom,
        "bottom_breadth": bottom / denom,
        "transition_breadth": trans / denom,
        "active_transition_breadth": active / denom,
    })
    out.index.name = "trade_date"
    return out


def compute_transition_flow(hist: pd.DataFrame) -> pd.DataFrame:
    """逐日 Flow：new_* 计数（当日真实 state transition）+ net_transition_flow。

    new_first_exit  = 当日进入 FIRST_EXIT（confirmed first exit day 0）
    new_retest      = 当日进入 RETEST
    new_active      = 当日进入 TRANSITION_ACTIVE（21-60D）
    new_established = 当日进入 TRANSITION_ESTABLISHED（61-120D）
    new_post        = 当日进入 POST_TRANSITION（>120D）
    net_transition_flow = new_first_exit − new_retest（§20）
    """
    rel = hist[hist["reliable_360"]].copy().sort_values(["fund_code", "trade_date"])
    rel["prev_state"] = rel.groupby("fund_code")["transition_state"].shift(1)
    cur = rel["transition_state"]
    prev = rel["prev_state"]

    dates = sorted(rel["trade_date"].unique())
    flow = pd.DataFrame(index=pd.DatetimeIndex(dates, name="trade_date"))
    flow["new_first_exit_count"] = ((cur == STATE_FIRST_EXIT) & (prev != STATE_FIRST_EXIT))\
        .groupby(rel["trade_date"]).sum()
    flow["new_retest_count"] = ((cur == STATE_RETEST) & (prev != STATE_RETEST))\
        .groupby(rel["trade_date"]).sum()
    flow["new_active_count"] = ((cur == STATE_TRANSITION_ACTIVE) & (prev != STATE_TRANSITION_ACTIVE))\
        .groupby(rel["trade_date"]).sum()
    flow["new_established_count"] = ((cur == STATE_TRANSITION_ESTABLISHED) & (prev != STATE_TRANSITION_ESTABLISHED))\
        .groupby(rel["trade_date"]).sum()
    flow["new_post_transition_count"] = ((cur == STATE_POST_TRANSITION) & (prev != STATE_POST_TRANSITION))\
        .groupby(rel["trade_date"]).sum()
    flow["net_transition_flow"] = flow["new_first_exit_count"] - flow["new_retest_count"]
    return flow.reset_index()


def compute_momentum(breadth: pd.DataFrame, windows: tuple[int, ...] = (5, 20)) -> pd.DataFrame:
    """Transition Momentum（§21）：breadth 的 5d/20d 变化（pp）。"""
    out = breadth.copy()
    for w in windows:
        out[f"transition_breadth_change_{w}d"] = out["transition_breadth"].diff(w)
        out[f"active_transition_breadth_change_{w}d"] = out["active_transition_breadth"].diff(w)
    return out


def compute_etf_type_breakdown(hist: pd.DataFrame, as_of: pd.Timestamp | None = None) -> pd.DataFrame:
    """§23：各 etf_type 的 bottom/transition/active breadth + net flow。

    as_of 给出时只统计当日；否则统计全历史逐日（供 study3c market history 用）。
    返回每行一个 (date, etf_type) 组合。
    """
    rel = hist[hist["reliable_360"]]
    if as_of is not None:
        rel = rel[rel["trade_date"] == as_of]
    rows: list[dict[str, Any]] = []
    for (d, etype), g in rel.groupby(["trade_date", "etf_type"]):
        denom = len(g)
        if denom == 0:
            continue
        dist = g["transition_state"].value_counts().to_dict()
        bottom = dist.get(STATE_BOTTOM, 0) + dist.get(STATE_RETEST, 0)
        trans = sum(dist.get(s, 0) for s in _TRANSITION_STATES)
        active = sum(dist.get(s, 0) for s in _ACTIVE_TRANSITION_STATES)
        rows.append({
            "trade_date": pd.Timestamp(d),
            "etf_type": etype,
            "n_reliable": denom,
            "bottom_breadth": bottom / denom,
            "transition_breadth": trans / denom,
            "active_transition_breadth": active / denom,
        })
    return pd.DataFrame(rows)


def compute_lane2_overlap(hist: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """§25：Lane 2 联动（BOTTOM/RETEST × TARGET/NEAR_MISS）——只读 target_stage，不改变 state。"""
    sub = hist[(hist["trade_date"] == as_of) & hist["reliable_360"]]
    sub = sub[sub["transition_state"].isin((STATE_BOTTOM, STATE_RETEST))]
    if len(sub) == 0:
        return pd.DataFrame()
    return sub.groupby(["transition_state", "lane2_target_stage"]).size().reset_index(name="n")


def compute_lane1_overlap(hist: pd.DataFrame, watchlist: pd.DataFrame | None,
                          as_of: pd.Timestamp) -> dict[str, Any]:
    """§24：Lane 1 联动（辅助验证）——只读 trend_state，覆盖率不影响 PASS/FAIL。

    watchlist：当日 watchlist_{date}.parquet（含 fund_code / trend_state）；
    仅当存在时计算，否则返回覆盖率为 0 的空结果。
    """
    if watchlist is None or len(watchlist) == 0:
        return {"coverage": 0, "n_overlap": 0, "detail": "lane1 facts unavailable"}
    sub = hist[hist["trade_date"] == as_of]
    m = sub.merge(watchlist[["fund_code", "trend_state"]], on="fund_code", how="inner")
    if len(m) == 0:
        return {"coverage": 0, "n_overlap": 0, "detail": "no lane1 overlap"}
    leader = m[m["trend_state"].isin(("STRONG_WATCH", "BUY_CANDIDATE"))]
    trans_states = (STATE_TRANSITION_ACTIVE, STATE_TRANSITION_ESTABLISHED, STATE_POST_TRANSITION)
    overlap = leader[leader["transition_state"].isin(trans_states)]
    cov = float(len(m)) / float(len(hist[hist["trade_date"] == as_of])) if len(hist[hist["trade_date"] == as_of]) else 0.0
    return {
        "coverage": round(cov, 4),
        "n_overlap": int(len(overlap)),
        "n_leader": int(len(leader)),
        "detail": f"{len(overlap)}/{len(leader)} leader in active/established/post transition",
    }


def build_market_history(hist: pd.DataFrame, flow: pd.DataFrame,
                         windows: tuple[int, ...] = (5, 20)) -> pd.DataFrame:
    """合并 breadth + flow + momentum 为每日市场历史表。"""
    breadth = compute_transition_breadth(hist)
    mom = compute_momentum(breadth, windows)
    out = mom.reset_index()
    out["trade_date"] = pd.to_datetime(out["trade_date"])
    fl = flow.set_index("trade_date")
    for c in fl.columns:
        out[c] = out["trade_date"].map(fl[c])
    return out
