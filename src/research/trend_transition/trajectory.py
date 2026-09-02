"""Lane 3 · Trend Transition — 底部→非底部生命周期状态链（Study 3A 核心）。

职责：
  - build_persistence_series：把 daily 的 long_term_bottom bool 序列转成 persistence-confirmed
    序列（离开/回来必须连续 window 个交易日才翻转），RAW / 3D / 5D 三口径。
  - extract_trajectories：从 persistence-confirmed 序列提取每只 ETF 的生命周期轨迹，
    以 first_exit_date 为核心 anchor，保存原始事实字段：
        entry_date / first_exit_date / first_retest_date / second_exit_date /
        observation_end / days_to_first_retest /
        escape_60d / 120d / 250d（bool）+ right_censored_60d/120d/250d + forward_data_complete
  - derive_report_labels：把原始字段派生出 report-friendly 标签（ESCAPE/RAPID_RETEST/...），
    **只用于报告，不是互斥上帝视角分类**，不进 3B target。

口径（用户锁定，写死点 ④）：
  - trajectory_table 最核心字段 = days_to_first_retest + escape_* + censored_*（原始事实）。
  - ESCAPE / RAPID / DELAYED / PERSISTENT 只是派生标签；CHURN 不做 class，
    只留 n_retests / retests_per_252d / median_gap 属性。

数据契约（写死点 ①）：
  - right-censor 用全市场交易日历（MarketCalendar），first_exit + N = 其后存在 N 个市场交易日。
  - 个体 OHLCV 完整度用 forward_data_complete（需要外部传入正向前缀有报价的天数）。
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from . import CLEAN_ESCAPE_HORIZON, HORIZONS, PERSISTENCE_PRIMARY, PERSISTENCE_RAW
from .calendar import MarketCalendar

logger = logging.getLogger(__name__)

_NON_ESCAPE_HORIZONS = tuple(h for h in HORIZONS if h != CLEAN_ESCAPE_HORIZON)


def build_persistence_series(df: pd.DataFrame, window: int = PERSISTENCE_RAW) -> pd.DataFrame:
    """按 fund_code 把 long_term_bottom 转成 persistence-confirmed 序列。

    window = 0 表示 RAW（不连续日确认，直接用 bool）。
    window = N：只有在「连续 N 个交易日同向」才翻转状态。
    返回带 per-fund 'ltb_persist' 列的 df（保持输入行序/索引）。
    """
    out = df.copy()
    if window == PERSISTENCE_RAW:
        out["ltb_persist"] = out["long_term_bottom"].astype(bool)
        return out

    groups = []
    for _code, g in df.groupby("fund_code", sort=False):
        g = g.sort_values("trade_date")
        raw = g["long_term_bottom"].to_numpy(bool)
        n = len(raw)
        # 连续确认：向前滚动 window 个值全同向才翻转
        out_val = np.zeros(n, dtype=bool)
        # 前 window-1 个点无法确认，直接用 raw 占位（window 边界内用 raw 保守不引伪信号）
        out_val[: min(window - 1, n)] = raw[: min(window - 1, n)]
        # 状态以「当前与过去 window 个点」都一致判定
        # 翻转判据：若 raw[i-window+1 .. i] 全 True → True；全 False → False；混合 → 保持前值
        prev = bool(raw[0]) if n else False
        out_val[0] = prev
        for i in range(1, n):
            lo = max(0, i - window + 1)
            block = raw[lo:i + 1]
            if block.all():
                out_val[i] = True
            elif not block.any():
                out_val[i] = False
            else:
                out_val[i] = prev
            prev = out_val[i]
        g = g.copy()
        g["ltb_persist"] = out_val
        groups.append(g)
    return pd.concat(groups).sort_index() if groups else out


def _trajectory_for_code(
    g: pd.DataFrame,
    cal: MarketCalendar,
    horizon_set: tuple[int, ...],
) -> list[dict[str, Any]]:
    """对单只 ETF 的 persistence-confirmed 序列提取多条轨迹。

    long_term_bottom 连续段 → 每个 off→on 段是一条轨迹。
    核心 anchor = 该段的第一次 off→on exit（first_exit_date）。
    """
    g = g.sort_values("trade_date").reset_index(drop=True)
    ltb = g["ltb_persist"].to_numpy(bool)
    dates = g["trade_date"].to_numpy()
    fund_code = str(g["fund_code"].iloc[0])
    fund_name = str(g["fund_name"].iloc[0])
    etf_type = str(g["etf_type"].iloc[0])
    cluster = str(g["industry_cluster"].iloc[0]) if "industry_cluster" in g.columns else "OTHER"

    obs_end = cal.end
    rows: list[dict[str, Any]] = []
    n = len(ltb)
    i = 0
    while i < n:
        if not ltb[i]:
            i += 1
            continue
        # on 段开始
        entry_idx = i
        j = i
        while j < n and ltb[j]:
            j += 1
        # on 段 [entry_idx, j) ; 若 j==n → 仍在底部（PERSISTENT/is_current）
        seg_end_idx = j - 1
        is_current = j >= n

        if is_current:
            rows.append({
                "fund_code": fund_code,
                "fund_name": fund_name,
                "etf_type": etf_type,
                "industry_cluster": cluster,
                "entry_date": dates[entry_idx],
                "first_exit_date": None,
                "first_retest_date": None,
                "second_exit_date": None,
                "observation_end": obs_end,
                "days_to_first_retest": None,
                "days_in_bottom_total": None,
                "is_current": True,
                "n_retests": 0,
            })
            i = j
            continue

        first_exit_date = dates[seg_end_idx]
        # 之后是否重测（再 off→on）
        first_retest = None
        second_exit = None
        k = seg_end_idx + 1
        while k < n:
            if ltb[k]:
                retest_idx = k
                m = k
                while m < n and ltb[m]:
                    m += 1
                first_retest = dates[retest_idx]
                if m < n:
                    second_exit = dates[max(retest_idx, m - 1)]
                break
            k += 1

        row: dict[str, Any] = {
            "fund_code": fund_code,
            "fund_name": fund_name,
            "etf_type": etf_type,
            "industry_cluster": cluster,
            "entry_date": dates[entry_idx],
            "first_exit_date": first_exit_date,
            "first_retest_date": first_retest,
            "second_exit_date": second_exit,
            "observation_end": obs_end,
            "days_to_first_retest": cal.trade_days_between(first_exit_date, first_retest)
                if first_retest is not None and cal.is_trade_date(first_retest) else None,
            "days_in_bottom_total": cal.trade_days_between(dates[entry_idx], first_exit_date),
            "is_current": False,
            "n_retests": 1 if first_retest is not None else 0,
        }

        # escape_Nd + right_censored_Nd + forward_data_complete
        fe = first_exit_date
        for h in horizon_set:
            can_observe = cal.has_complete_window(fe, h)
            retested_within = first_retest is not None and \
                cal.is_trade_date(first_retest) and \
                cal.trade_days_between(fe, first_retest) <= h
            if not can_observe:
                row[f"escape_{h}d"] = None
                row[f"right_censored_{h}d"] = True
            else:
                row[f"escape_{h}d"] = not retested_within
                row[f"right_censored_{h}d"] = False
            row[f"forward_data_complete_{h}d"] = can_observe

        rows.append(row)
        i = j

    # forward_data_complete（个体 OHLCV 完整度跨所有 horizon 的聚合标记）由调用方用 raw 报价补齐
    return rows


def extract_trajectories(
    df: pd.DataFrame,
    cal: MarketCalendar,
    horizon_set: tuple[int, ...] = (60, 120, 250),
    persistence: int = PERSISTENCE_PRIMARY,
) -> pd.DataFrame:
    """对全市场 daily 数据提取全部生命周期轨迹。

    df 需含 fund_code/fund_name/etf_type/industry_cluster/trade_date/long_term_bottom。
    内部先建 persistence-confirmed 序列，再逐 ETF 提取。
    """
    pers = build_persistence_series(df, window=persistence)
    all_rows: list[dict[str, Any]] = []
    for _code, g in pers.groupby("fund_code", sort=False):
        all_rows.extend(_trajectory_for_code(g, cal, horizon_set))
    table = pd.DataFrame(all_rows)
    return table


def derive_report_labels(
    t: pd.DataFrame,
    horizon: int = CLEAN_ESCAPE_HORIZON,
) -> pd.DataFrame:
    """从原始字段派生 report-friendly trajectory_type 标签。

    **只用于报告/展示，不进入 3B target。**
    标签逻辑（report-only，非互斥上帝视角）：
      PERSISTENT     is_current == True
      ESCAPE         非 current 且 escape_{h}d == True 且无 right_censored
      DELAYED_RETEST 非 current 且 escape_{h}d == False 且 first_retest 距 exit > threshold
      RAPID_RETEST   非 current 且 escape_{h}d == False 且 first_retest 距 exit <= threshold
    是否展示由调用方决定；返回时附每个 label 的原始依据。
    """
    out = t.copy()
    col = f"escape_{horizon}d"
    out["trajectory_type"] = None
    out["_label_basis"] = ""

    for idx, r in out.iterrows():
        if r.get("is_current"):
            out.at[idx, "trajectory_type"] = "PERSISTENT"
            out.at[idx, "_label_basis"] = "still_in_bottom"
            continue
        esc = r.get(col)
        cens = r.get(f"right_censored_{horizon}d")
        if esc is None or cens is True:
            out.at[idx, "trajectory_type"] = "CENSORED"
            out.at[idx, "_label_basis"] = "insufficient_window"
            continue
        if esc is True:
            out.at[idx, "trajectory_type"] = "ESCAPE"
            out.at[idx, "_label_basis"] = f"no_retest_within_{horizon}d"
            continue
        gap = r.get("days_to_first_retest")
        if gap is None:
            out.at[idx, "trajectory_type"] = "RAPID_RETEST"
            out.at[idx, "_label_basis"] = "retest_present_gap_none"
        else:
            # 中位 gap 约 5-8 天，用 horizon 一半做时空分隔（domain heuristic，报告层）
            if gap <= horizon // 2:
                out.at[idx, "trajectory_type"] = "RAPID_RETEST"
                out.at[idx, "_label_basis"] = f"retest_gap={gap}d"
            else:
                out.at[idx, "trajectory_type"] = "DELAYED_RETEST"
                out.at[idx, "_label_basis"] = f"retest_gap={gap}d"
    return out
