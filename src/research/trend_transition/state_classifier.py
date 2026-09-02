"""Study 3C · 确定性 as-of 状态分类器（Trend Transition State Machine V1）。

核心职责（§0-§15，用户锁定）：
  在任意 as-of date，仅用当日及以前数据，为每只可靠 ETF 标记其当前所处的
  Trend Transition 生命周期状态。STATE != SIGNAL / PREDICTION / BUY。

状态（§5-§15，canonical）：
  UNRELIABLE / BOTTOM / FIRST_EXIT / TRANSITION_EARLY / TRANSITION_ACTIVE /
  TRANSITION_ESTABLISHED / RETEST / POST_TRANSITION
  - LEADERSHIP 不属于 Lane 3（由 Lane 1 判定）。
  - RETEST 是事件状态，持续到下一次 confirmed exit，然后 transition_cycle += 1。
  - left-censored（窗口首日即 non-bottom、无法观测 first_exit）→ POST_TRANSITION
    + state_origin=LEFT_CENSORED，transition_anchor_known=False，不伪造 first_exit。

确定性保证：
  - confirmed_long_term_bottom = build_persistence_series(long_term_bottom, window)
    （向后滚动窗口，只用 <= 当日的行 → 天然无 look-ahead）。
  - 事件检测只在 reliable_360 == True 的日子进行；UNRELIABLE 日输出 UNRELIABLE、
    不参与事件检测、不进入 breadth 分母，但状态变量（first_exit_date 等）跨不可靠段保留。
  - 同一输入 → 同一状态（无 RNG、无日期泄漏）。
  - first_exit_date = confirmed 状态首次 True→False 翻转的当日（day 0，days_since=0）。

不依赖 RPS / drawdown / market breadth / etf_type（§16：这些只进 state_context，
不改主状态定义）。
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from . import PERSISTENCE_PRIMARY, PERSISTENCE_RAW
from .calendar import MarketCalendar
from .trajectory import build_persistence_series

logger = logging.getLogger(__name__)

# ── canonical states ───────────────────────────────────────────
STATE_UNRELIABLE = "UNRELIABLE"
STATE_BOTTOM = "BOTTOM"
STATE_FIRST_EXIT = "FIRST_EXIT"
STATE_TRANSITION_EARLY = "TRANSITION_EARLY"
STATE_TRANSITION_ACTIVE = "TRANSITION_ACTIVE"
STATE_TRANSITION_ESTABLISHED = "TRANSITION_ESTABLISHED"
STATE_RETEST = "RETEST"
STATE_POST_TRANSITION = "POST_TRANSITION"

STATES = (
    STATE_UNRELIABLE, STATE_BOTTOM, STATE_FIRST_EXIT, STATE_TRANSITION_EARLY,
    STATE_TRANSITION_ACTIVE, STATE_TRANSITION_ESTABLISHED, STATE_RETEST,
    STATE_POST_TRANSITION,
)

# §18 age bucket
AGE_BUCKET_FIRST_EXIT = "0_5D"
AGE_BUCKET_EARLY = "6_20D"
AGE_BUCKET_ACTIVE = "21_60D"
AGE_BUCKET_ESTABLISHED = "61_120D"
AGE_BUCKET_POST = "120D_PLUS"
AGE_BUCKET_NA = "NA"

AGE_BUCKETS = (AGE_BUCKET_FIRST_EXIT, AGE_BUCKET_EARLY, AGE_BUCKET_ACTIVE,
               AGE_BUCKET_ESTABLISHED, AGE_BUCKET_POST, AGE_BUCKET_NA)

# §30 age windows（交易日）
AGE_WINDOWS: dict[str, tuple[int, int | None]] = {
    "first_exit": (0, 5),
    "early": (6, 20),
    "active": (21, 60),
    "established": (61, 120),
    "post_transition": (121, None),   # >120
}

STATE_ORIGIN_NORMAL = "NORMAL"
STATE_ORIGIN_LEFT_CENSORED = "LEFT_CENSORED"

    # §14 legal transitions（state_t-1 → state_t）
    # 含跨可靠段空隙的合法迁移（fund 短暂不在 reliable 宇宙，age 跨窗口推进）：
    #   FIRST_EXIT/EARLY/ACTIVE/ESTABLISHED → POST_TRANSITION、RETEST → POST_TRANSITION、
    #   BOTTOM → POST_TRANSITION（可靠段空隙内 age 跨过 120）
LEGAL_TRANSITIONS: set[tuple[str, str]] = {
    (STATE_UNRELIABLE, STATE_BOTTOM), (STATE_UNRELIABLE, STATE_POST_TRANSITION),
    (STATE_BOTTOM, STATE_FIRST_EXIT), (STATE_BOTTOM, STATE_RETEST),
    (STATE_FIRST_EXIT, STATE_TRANSITION_EARLY), (STATE_FIRST_EXIT, STATE_RETEST),
    (STATE_TRANSITION_EARLY, STATE_TRANSITION_ACTIVE), (STATE_TRANSITION_EARLY, STATE_RETEST),
    (STATE_TRANSITION_ACTIVE, STATE_TRANSITION_ESTABLISHED), (STATE_TRANSITION_ACTIVE, STATE_RETEST),
    (STATE_TRANSITION_ESTABLISHED, STATE_POST_TRANSITION), (STATE_TRANSITION_ESTABLISHED, STATE_RETEST),
    (STATE_RETEST, STATE_FIRST_EXIT),
    (STATE_POST_TRANSITION, STATE_RETEST),
    (STATE_FIRST_EXIT, STATE_FIRST_EXIT),   # 同状态驻留（age 窗口内）
    (STATE_TRANSITION_EARLY, STATE_TRANSITION_EARLY),
    (STATE_TRANSITION_ACTIVE, STATE_TRANSITION_ACTIVE),
    (STATE_TRANSITION_ESTABLISHED, STATE_TRANSITION_ESTABLISHED),
    (STATE_POST_TRANSITION, STATE_POST_TRANSITION),
    (STATE_BOTTOM, STATE_BOTTOM), (STATE_RETEST, STATE_RETEST),
    (STATE_UNRELIABLE, STATE_UNRELIABLE),
    # UNRELIABLE 恢复可靠（state_origin=LEFT_CENSORED 初始化）
    (STATE_UNRELIABLE, STATE_FIRST_EXIT), (STATE_UNRELIABLE, STATE_TRANSITION_EARLY),
    (STATE_UNRELIABLE, STATE_TRANSITION_ACTIVE), (STATE_UNRELIABLE, STATE_TRANSITION_ESTABLISHED),
    (STATE_UNRELIABLE, STATE_RETEST),
    # 跨可靠段空隙的合法 age 推进（fund 短暂退出 reliable 宇宙后回来，age 已跨窗口）
    (STATE_FIRST_EXIT, STATE_POST_TRANSITION),
    (STATE_TRANSITION_EARLY, STATE_POST_TRANSITION),
    (STATE_TRANSITION_ACTIVE, STATE_POST_TRANSITION),
    (STATE_TRANSITION_ESTABLISHED, STATE_TRANSITION_ESTABLISHED),
    (STATE_TRANSITION_ESTABLISHED, STATE_POST_TRANSITION),
    (STATE_RETEST, STATE_POST_TRANSITION),
    (STATE_BOTTOM, STATE_POST_TRANSITION),
}


def age_to_state(days_since: int) -> tuple[str, str]:
    """days_since_first_exit → (transition_state, age_bucket)。"""
    if days_since <= 5:
        return STATE_FIRST_EXIT, AGE_BUCKET_FIRST_EXIT
    if days_since <= 20:
        return STATE_TRANSITION_EARLY, AGE_BUCKET_EARLY
    if days_since <= 60:
        return STATE_TRANSITION_ACTIVE, AGE_BUCKET_ACTIVE
    if days_since <= 120:
        return STATE_TRANSITION_ESTABLISHED, AGE_BUCKET_ESTABLISHED
    return STATE_POST_TRANSITION, AGE_BUCKET_POST


def classify_fund_history(
    fund_history: pd.DataFrame,
    cal: MarketCalendar,
    persistence: int = PERSISTENCE_PRIMARY,
) -> pd.DataFrame:
    """对单只 ETF 的完整历史做逐日状态分类（单次遍历）。

    fund_history 需含 trade_date/fund_code/fund_name/etf_type/reliable_360/
    long_term_bottom/bottom_state/pos60/pos120/pos360（target_stage 可选）。
    返回每行一个状态记录（与输入行数相同，含全部 §17 字段）。
    """
    g = fund_history.copy().sort_values("trade_date").reset_index(drop=True)
    if len(g) == 0:
        return pd.DataFrame()
    confirmed = build_persistence_series(g, window=persistence)["ltb_persist"].to_numpy(bool)
    reliable = g["reliable_360"].to_numpy(bool)
    dates = g["trade_date"].to_numpy()
    n = len(g)

    fund_code = str(g["fund_code"].iloc[0])
    fund_name = str(g["fund_name"].iloc[0])
    etf_type = str(g["etf_type"].iloc[0])

    # 预取静态列（避免逐行 .iloc）
    bottom_state = (g["bottom_state"].astype(str).to_numpy()
                    if "bottom_state" in g.columns else np.array(["NORMAL"] * n))
    pos60 = (g["pos60"].to_numpy(float) if "pos60" in g.columns
             else np.full(n, np.nan))
    pos120 = (g["pos120"].to_numpy(float) if "pos120" in g.columns
              else np.full(n, np.nan))
    pos360 = (g["pos360"].to_numpy(float) if "pos360" in g.columns
              else np.full(n, np.nan))
    target_stage = (g["target_stage"].astype(str).to_numpy()
                    if "target_stage" in g.columns else np.array(["NON_TARGET"] * n))

    # 预计算每行在全市场日历中的位置（days_since 用位置差，避免 dict 查找）
    pos_arr = np.array([cal.date_pos(pd.Timestamp(d)) for d in dates], dtype=np.int64)
    first_exit_pos: int | None = None

    prev_confirmed: bool | None = None
    has_exited = False
    first_exit_date: pd.Timestamp | None = None
    first_retest_date: pd.Timestamp | None = None
    cycle = 0
    seen_reliable = False

    rows: list[dict[str, Any]] = []
    for i in range(n):
        c = bool(confirmed[i])
        rel = bool(reliable[i])
        date_val = pd.Timestamp(dates[i])
        cur_pos = int(pos_arr[i]) if pos_arr[i] >= 0 else -1

        if not rel:
            # UNRELIABLE：不检测事件、不进入 breadth 分母；状态变量跨段保留
            days_since = (cur_pos - first_exit_pos
                          if (has_exited and first_exit_pos is not None) else None)
            rows.append(_record(bottom_state, pos60, pos120, pos360, target_stage,
                                i, STATE_UNRELIABLE, STATE_ORIGIN_NORMAL, c,
                                rel, has_exited, first_exit_date, first_retest_date,
                                cycle, days_since, None, persistence, date_val,
                                fund_code, fund_name, etf_type))
            prev_confirmed = None  # 不可靠段不参与事件检测
            continue

        # 首个可靠日即非底部 → left-censored：视为已发生（不可观测）prior exit
        if not seen_reliable:
            seen_reliable = True
            if not c:
                has_exited = True   # 有未观测到的 prior exit（LEFT_CENSORED 语义）

        # 事件检测（相对上一个可靠日）
        if prev_confirmed is not None:
            if prev_confirmed and not c:
                # confirmed True→False：confirmed first exit（day 0）
                has_exited = True
                cycle += 1
                first_exit_date = date_val
                first_exit_pos = cur_pos
                first_retest_date = None
            elif not prev_confirmed and c and has_exited:
                # False→True 且已有 exit：retest
                first_retest_date = date_val
        prev_confirmed = c

        # 事件检测之后统一计算 days_since（退出当日 = 0）
        days_since = (cur_pos - first_exit_pos
                      if (has_exited and first_exit_pos is not None) else None)

        if c:
            state = STATE_RETEST if has_exited else STATE_BOTTOM
            rows.append(_record(bottom_state, pos60, pos120, pos360, target_stage,
                                i, state, STATE_ORIGIN_NORMAL, c, rel, has_exited,
                                first_exit_date, first_retest_date, cycle,
                                days_since, None, persistence, date_val,
                                fund_code, fund_name, etf_type))
            continue

        # confirmed == False（非底部）
        if first_exit_date is None:
            # left-censored / anchor 未知：窗口内未观测到 confirmed exit，不伪造 first_exit（§15）
            rows.append(_record(bottom_state, pos60, pos120, pos360, target_stage,
                                i, STATE_POST_TRANSITION, STATE_ORIGIN_LEFT_CENSORED,
                                c, rel, has_exited, None, None, cycle, None, None,
                                persistence, date_val, fund_code, fund_name, etf_type))
            continue

        assert has_exited and days_since is not None
        state, bucket = age_to_state(days_since)
        rows.append(_record(bottom_state, pos60, pos120, pos360, target_stage,
                            i, state, STATE_ORIGIN_NORMAL, c, rel, has_exited,
                            first_exit_date, first_retest_date, cycle, days_since,
                            bucket, persistence, date_val, fund_code, fund_name, etf_type))

    return pd.DataFrame(rows)


def _record(bottom_state: np.ndarray, pos60: np.ndarray, pos120: np.ndarray,
            pos360: np.ndarray, target_stage: np.ndarray, i: int,
            state: str, origin: str, confirmed_ltb: bool, reliable: bool,
            has_exited: bool, first_exit_date: pd.Timestamp | None,
            first_retest_date: pd.Timestamp | None, cycle: int,
            days_since: int | None, bucket: str | None, persistence: int,
            date_val: pd.Timestamp, fund_code: str, fund_name: str,
            etf_type: str) -> dict[str, Any]:
    return {
        "trade_date": date_val,
        "fund_code": fund_code,
        "fund_name": fund_name,
        "etf_type": etf_type,
        "reliable_360": bool(reliable),
        "transition_state": state,
        "state_origin": origin,
        "transition_anchor_known": bool(has_exited) and first_exit_date is not None,
        "confirmed_long_term_bottom": confirmed_ltb,
        "bottom_state": str(bottom_state[i]),
        "first_exit_date": first_exit_date,
        "days_since_first_exit": days_since,
        "first_retest_date": first_retest_date,
        "transition_cycle": cycle,
        "persistence": persistence,
        "pos60": float(pos60[i]) if np.isfinite(pos60[i]) else np.nan,
        "pos120": float(pos120[i]) if np.isfinite(pos120[i]) else np.nan,
        "pos360": float(pos360[i]) if np.isfinite(pos360[i]) else np.nan,
        "lane2_target_stage": str(target_stage[i]),
        "lane1_leadership_state": None,   # 由 study3c 从 watchlist 只读联接（辅助验证）
        "transition_age_bucket": bucket if bucket else AGE_BUCKET_NA,
    }


def classify_universe_state(
    daily_df: pd.DataFrame,
    as_of: pd.Timestamp,
    cal: MarketCalendar,
    persistence: int = PERSISTENCE_PRIMARY,
) -> pd.DataFrame:
    """对全市场在 as_of 日的状态分类（每只 ETF 一行）。

    只使用 <= as_of 的数据（截断后再分类，无 look-ahead）。
    """
    sub = daily_df[daily_df["trade_date"] <= as_of]
    frames = []
    for _code, g in sub.groupby("fund_code", sort=False):
        hist = classify_fund_history(g, cal, persistence=persistence)
        if len(hist):
            frames.append(hist.tail(1))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def replay_universe_state(
    daily_df: pd.DataFrame,
    cal: MarketCalendar,
    persistence: int = PERSISTENCE_PRIMARY,
) -> pd.DataFrame:
    """全历史逐日回放：每只 ETF 的完整状态历史（单次遍历，确定性）。"""
    frames = []
    for _code, g in daily_df.groupby("fund_code", sort=False):
        hist = classify_fund_history(g, cal, persistence=persistence)
        if len(hist):
            frames.append(hist)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    return out.sort_values(["trade_date", "fund_code"]).reset_index(drop=True)
