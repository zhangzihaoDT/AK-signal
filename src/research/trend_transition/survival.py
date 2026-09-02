"""Lane 3 · Trend Transition — time-to-retest 生存分析（Study 3A）。

职责：
  - 对每个 first_exit 事件，度量「退出后到重新进入长期底部（retest）」的时间。
  - Bottom Escape Survival Curve：面向一批 exit，估计「在 N 个交易日后仍未返回底部」的比例。
  - 手写 Kaplan-Meier（不引入 lifelines，保持研究栈确定性），支持 right censoring。

口径（用户锁定）：
  - time-to-retest 用**全市场交易日**（MarketCalendar 判定），不是自然日。
  - right censor：exit 后无完整 {h} 市场交易日窗口 → 无法观测到该 h 的 retest 状态 → 在该 h 处 censored。
  - escape_{h}d 只对「有完整窗口」的 exit 有确定值；censored 不当作 escape 也不当作 retest。
  - 生存函数单调不增（KM 保序）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import HORIZONS
from .calendar import MarketCalendar


def time_to_retest(t: pd.DataFrame, cal: MarketCalendar) -> pd.DataFrame:
    """从轨迹表派生每事件的 time-to-retest 向量。

    返回行数与 t 相同；新增列：
      days_to_first_retest     （已在 trajectory 计算，这里确保为市场交易日）
      retested                 是否有 retest
      censored_{h}d            在该 h 观察窗口是否被右截断（无完整市场窗口）
  """
    out = t.copy()
    out["retested"] = out["first_retest_date"].notna()
    for h in HORIZONS:
        out[f"censored_{h}d"] = out.apply(
            lambda r: not cal.has_complete_window(r["first_exit_date"], h)
            if pd.notna(r.get("first_exit_date")) else True,
            axis=1,
        )
    return out


def survival_curve(
    t: pd.DataFrame,
    cal: MarketCalendar,
    horizons: tuple[int, ...] = HORIZONS,
) -> dict:
    """Bottom Escape Survival Curve：退出后 N 个交易日仍未返回底部的比例。

    每个 h：分母 = 有完整 {h} 市场交易日窗口的 exit；分子 = 其中到 h 仍无 retest。
    right_censored（无完整窗口）不进入该 h 的分母，也绝不当作 escape。
    返回 {h: {"survival": p, "n_total":..., "n_observed":..., "n_censored":...}}。
    """
    out: dict[str, dict] = {}
    for h in horizons:
        cens_col = f"right_censored_{h}d"
        esc_col = f"escape_{h}d"
        cens_ser = t[cens_col].astype(bool)
        sub: pd.DataFrame = t.loc[~cens_ser]
        sub = sub.loc[sub["first_exit_date"].notna()]
        n_obs = len(sub)
        n_cens = int(cens_ser.to_numpy().sum())
        if n_obs == 0:
            out[str(h)] = {"survival": None, "n_total": int(len(t)), "n_observed": 0,
                           "n_censored": n_cens, "n_escape": 0}
            continue
        n_escape = int(sub[esc_col].astype(bool).to_numpy().sum())
        out[str(h)] = {
            "survival": round(n_escape / n_obs, 4),
            "n_total": int(len(t)),
            "n_observed": n_obs,
            "n_censored": n_cens,
            "n_escape": n_escape,
        }
    return out


def kaplan_meier(
    t: pd.DataFrame,
    cal: MarketCalendar,
    horizon: int = 120,
) -> dict:
    """手写 Kaplan-Meier 生存函数估计（right censoring）。

    - 样本：有完整 {horizon} 交易日市场窗口的 exit（right_censored_{h}d == False）。
    - 事件：days_to_first_retest（市场交易日）；未重测的在 horizon 处右截断。
    - 返回逐交易日生存概率曲线（单调不增）与末尾汇总。
    不使用 lifelines；完全手写，与项目 bootstrap infra 同风格。
    """
    h = horizon
    cens_col = f"right_censored_{h}d"
    cens_ser = t[cens_col].astype(bool) if cens_col in t.columns else pd.Series([True] * len(t), index=t.index)
    ev = t.loc[~cens_ser]
    ev = ev.loc[ev["first_exit_date"].notna()].copy()
    if ev.empty:
        return {"horizon": h, "n_events_total": 0, "n_retest_events": 0,
                "n_censored": 0, "survival_at": {}, "survival_end": None,
                "monotone_non_increasing": True}

    retested = ev["first_retest_date"].notna()
    event_times = ev.loc[retested, "days_to_first_retest"].dropna().astype(int).to_numpy()
    n_cens = int((~retested).sum())  # 未重测 → 观察满 h 日，在 h 处右截断

    times = np.arange(1, h + 1)
    surv = np.ones(h + 1, dtype=float)
    # 风险集维护：逐日（S(0)=1；surv[i] 对应第 i 个交易日后）
    for i, tnow in enumerate(times, start=1):
        d_at = int((event_times == tnow).sum()) if len(event_times) else 0
        # 风险集 = 事件时间 >= tnow（含当天）+ censored（censor 时间 h >= tnow，恒成立）
        at_risk = int((event_times >= tnow).sum()) + n_cens
        if at_risk > 0 and d_at > 0:
            surv[i] = surv[i - 1] * (1 - d_at / at_risk)
        else:
            surv[i] = surv[i - 1]

    surv_inner = np.minimum.accumulate(surv[1:])
    surv[1:] = surv_inner
    return {
        "horizon": h,
        "n_events_total": int(len(ev)),
        "n_retest_events": int(len(event_times)),
        "n_censored": int(n_cens),
        "survival_at": {str(int(tnow)): round(float(surv[i]), 4) for i, tnow in enumerate(times, start=1)},
        "survival_end": round(float(surv[-1]), 4),
        "monotone_non_increasing": bool(np.all(np.diff(surv) <= 0 + 1e-12)),
    }
