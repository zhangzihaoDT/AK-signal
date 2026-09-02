"""Study 3A · 断点检验（Post-924 structural break）。

验证「底部 → 非底部」切换后 escape（120 日不重测）的比例在 2024-09-24 附近
是否发生断点式变化。

统计语义（用户锁定）：
  - event-weighted estimate：每个 first exit 一票，直接算 pre/post 的 escape 生存比例。
    escape 生存比例 = 有完整 120 日交易日窗口的 exit 中，仍处于 escape（未重测）的占比。
  - date-block bootstrap inference：按「交易日块」重抽样（块 = 单个交易日当天全部 exit），
    重抽样单位 = 交易日，估计 pre–post 效应（post − pre）的分布与 95% CI。
    decision 只看 date-block CI 是否跨 0（P4），event-weighted 只提供点估计，不混为显著性。

口径：
  - 只对 right_censored_120d == False 的 exit 判 escape（分母不保留不完整个体为 escape）。
  - break effect = post 的 escape 比例 − pre 的 escape 比例。
  - 支持 P1（persistence=3/5/raw）、P2（3D/5D/RAW 一致性）共用同一函数；
    robustness 参数仅改变分母——不同 persistence 产出的「exit→escape」链基准不同。
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from . import CLEAN_ESCAPE_HORIZON, PERSISTENCE_PRIMARY, PERSISTENCE_ROBUST, PERSISTENCE_RAW

logger = logging.getLogger(__name__)


def _escape_series(
    traj: pd.DataFrame,
    horizon: int = CLEAN_ESCAPE_HORIZON,
    persistence: int = PERSISTENCE_PRIMARY,
) -> pd.DataFrame:
    """选出可用于 {horizon} escape 判断的 first-exit 行。

    - 必须有 first_exit_date。
    - 必须有完整 {horizon} 交易日窗口（right_censored_{h}d == False）。
    - escape = escape_{h}d == True。
    - 若指定 persistence>0，要求该记号的 first exit 来自该 persistence 的轨迹链
      （trajectory 表按 persistence 产出，因此上进样需限定 exit_source）。
      此处 persistence 主要是鲁棒性参数；轨迹构造时已按 persistence 生成列。
    """
    h = horizon
    cens = f"right_censored_{h}d"
    esc = f"escape_{h}d"
    if cens not in traj.columns or esc not in traj.columns:
        return pd.DataFrame()
    sub = traj.loc[traj["first_exit_date"].notna()].copy()
    sub = sub.loc[~sub[cens].astype(bool)]
    # escape 布尔化；非 escape（重测）记为 0；保留原始时间
    sub["_y"] = sub[esc].astype(bool).to_numpy().astype(int)
    sub["_t"] = pd.to_datetime(sub["first_exit_date"])
    return sub


def _window_split(
    s: pd.DataFrame,
    dates: list[pd.Timestamp],
    bd: Any,
    window: int,
) -> dict[str, pd.DataFrame]:
    """用交易日窗口划分 pre/post：bd 前 window 个交易日（不含 bd）与 bd 起 window 个交易日。

    以 bd 在样本交易日序列中的位置为锚；位置不存在的（bd 非样本交易日）取最近。
    """
    import bisect

    bdt = pd.Timestamp(bd)
    bpos = bisect.bisect_left(dates, bdt)
    if bpos < len(dates) and dates[min(bpos, len(dates) - 1)] == bdt:
        pass  # bdt 恰为交易日，bpos 即其位置
    pre_dates = dates[max(0, bpos - window):bpos]
    post_dates = dates[bpos:bpos + window + 1]
    pre = s.loc[s["_t"].isin(pre_dates)]
    post = s.loc[s["_t"].isin(post_dates)]
    return {"pre": pre, "post": post}


def _escape_rate_1d(frame: pd.DataFrame) -> float | None:
    """单组 escape 比例（_y 列 to_numpy 避免类型推断问题）。"""
    if len(frame) == 0:
        return None
    return float(frame["_y"].to_numpy().mean())


def _break_effect(
    traj: pd.DataFrame,
    break_date: Any,
    window: int,
    horizons: tuple[int, ...] = (CLEAN_ESCAPE_HORIZON,),
) -> dict[str, Any]:
    """event-weighted pre/post escape 比例与 break effect（post − pre）。"""
    sample = _escape_series(traj, horizon=CLEAN_ESCAPE_HORIZON)
    if sample.empty:
        return {"n_pre": 0, "n_post": 0, "pre_escape": None, "post_escape": None,
                "effect": None, "window": window}
    s = sample.sort_values("_t").reset_index(drop=True)
    dates = sorted(s["_t"].unique())
    split = _window_split(s, dates, pd.Timestamp(break_date), window)
    pre, post = split["pre"], split["post"]
    pre_esc = _escape_rate_1d(pre)
    post_esc = _escape_rate_1d(post)
    effect = (post_esc - pre_esc) if (pre_esc is not None and post_esc is not None) else None
    return {
        "n_pre": int(len(pre)),
        "n_post": int(len(post)),
        "pre_escape": round(pre_esc, 4) if pre_esc is not None else None,
        "post_escape": round(post_esc, 4) if post_esc is not None else None,
        "effect": round(effect, 4) if effect is not None else None,
        "window": window,
    }


def hypothesis_break(
    traj: pd.DataFrame,
    break_date: Any = "2024-09-24",
    windows: tuple[int, ...] = (40, 63, 90),
) -> dict[str, Any]:
    """主假设断点检验（P3）：在 ±window 交易日窗口内 pre/post escape 是否结构不同。

    主判据 = windows 中的主窗口（63 多变）下 effect 的方向与量级。
    同时输出各窗口，供 P3「±40/±63/±90 窗口一致性」判定。
    """
    out: dict[str, Any] = {"break_date": str(break_date), "windows": {}}
    for w in windows:
        out["windows"][str(w)] = _break_effect(traj, break_date, w)
    return out


def data_driven_breakpoint(
    traj: pd.DataFrame,
    search_geom: bool = True,
    window: int = 63,
) -> dict[str, Any]:
    """数据驱动断点扫描：在候选交易日序列上滑动，找 escape 比例变化最大的点。

    回答「断点是否真的在 2024-09-24 附近，还是数据自己另有明显断点」。
    """
    sample = _escape_series(traj, horizon=CLEAN_ESCAPE_HORIZON)
    if sample.empty:
        return {"status": "insufficient", "n": 0}
    s = sample.sort_values("_t").reset_index(drop=True)
    dates = sorted(s["_t"].unique())
    bd_target = pd.Timestamp("2024-09-24")
    best: pd.Timestamp | None = None
    best_effect: float = -np.inf
    best_pe: float = 0.0
    best_po: float = 0.0
    results: list[dict[str, Any]] = []
    n_dates = len(dates)
    # 扫全部候选断点（首日起 window 日后可作 post 起点）
    for i in range(window, n_dates - window):
        bd = dates[i]
        pre = s.loc[s["_t"].isin(dates[max(0, i - window):i])]
        post = s.loc[s["_t"].isin(dates[i + 1:i + window + 1])]
        if len(pre) == 0 or len(post) == 0:
            continue
        pe_f, po_f = _escape_rate_1d(pre), _escape_rate_1d(post)
        if pe_f is None or po_f is None:
            continue
        pe, po = float(pe_f), float(po_f)
        eff = po - pe
        dist = abs((bd - bd_target).days)
        results.append({"date": bd.strftime("%Y-%m-%d"), "effect": round(eff, 4),
                        "pre_escape": round(pe, 4), "post_escape": round(po, 4),
                        "n_pre": int(len(pre)), "n_post": int(len(post)), "dist_days_924": dist})
        if eff > best_effect:
            best_effect, best, best_pe, best_po = eff, bd, pe, po
    if best is None:
        return {"status": "insufficient", "n": int(len(s))}
    top = sorted(results, key=lambda r: r["effect"], reverse=True)[:10]
    near_924 = abs((best - bd_target).days) <= 120
    return {
        "status": "ok",
        "n_entries": int(len(s)),
        "argmax_date": best.strftime("%Y-%m-%d"),
        "argmax_effect": round(best_effect, 4),
        "argmax_pre_escape": round(best_pe, 4),
        "argmax_post_escape": round(best_po, 4),
        "near_924": bool(near_924),
        "dist_days_924": int(abs((best - bd_target).days)),
        "top10": top,
    }


def date_block_bootstrap(
    traj: pd.DataFrame,
    break_date: Any = "2024-09-24",
    window: int = 63,
    n_boot: int = 2000,
    seed: int = 42,
) -> dict[str, Any]:
    """P4：date-block bootstrap 推断（重抽样单位 = 交易日块）。

    - 块 = 单个交易日当天全部 first-exit（保留组内横截面相关）。
    - 每次重抽样：从 pre 交易日与 post 交易日集合分别有放回抽样（等量），
      由抽样日期的全部 exit 计算 post−pre escape 效应 → 得到效应分布。
    - decision：95% CI（2.5/97.5 分位）不跨 0 ⇒ 效应的方向在交易日聚类下稳健（PASS）。
    """
    sample = _escape_series(traj, horizon=CLEAN_ESCAPE_HORIZON)
    if sample.empty:
        return {"status": "insufficient"}
    s = sample.sort_values("_t").reset_index(drop=True)
    dates = sorted(s["_t"].unique())
    split = _window_split(s, dates, pd.Timestamp(break_date), window)
    pre, post = split["pre"], split["post"]
    if len(pre) == 0 or len(post) == 0:
        return {"status": "insufficient", "n_pre": int(len(pre)), "n_post": int(len(post))}

    pre_dates = sorted(pre["_t"].unique())
    post_dates = sorted(post["_t"].unique())
    by_pre = {d: pre.loc[pre["_t"] == d, "_y"].to_numpy(float) for d in pre_dates}
    by_post = {d: post.loc[post["_t"] == d, "_y"].to_numpy(float) for d in post_dates}
    rng = np.random.default_rng(seed)
    n_pre, n_post = len(pre_dates), len(post_dates)

    def _rate(by: dict, codes: np.ndarray) -> float:
        vals = np.concatenate([by[c] for c in codes]) if codes.size else np.array([])
        return float(vals.mean()) if vals.size else 0.0

    obs_eff = _rate(by_post, np.array(post_dates)) - _rate(by_pre, np.array(pre_dates))
    boot_effs = []
    for _ in range(n_boot):
        pb = rng.choice(np.array(pre_dates), size=n_pre, replace=True)
        ob = rng.choice(np.array(post_dates), size=n_post, replace=True)
        boot_effs.append(_rate(by_post, ob) - _rate(by_pre, pb))
    b = np.array(boot_effs)
    lo, hi = float(np.percentile(b, 2.5)), float(np.percentile(b, 97.5))
    pct_gt0 = float((b > 0).mean())
    obs = float(obs_eff)
    return {
        "status": "ok",
        "window": window,
        "break_date": str(break_date),
        "n_pre_dates": n_pre,
        "n_post_dates": n_post,
        "n_pre": int(len(pre)),
        "n_post": int(len(post)),
        "n_boot": n_boot,
        "obs_effect": round(obs, 4),
        "bootstrap_mean": round(float(b.mean()), 4),
        "ci95": [round(lo, 4), round(hi, 4)],
        "ci_crosses_zero": bool(lo < 0 < hi),
        "p_effect_gt0": round(pct_gt0, 4),
    }


def persistence_consistency(
    trajectories: dict[int, pd.DataFrame],
    break_date: Any = "2024-09-24",
    window: int = 63,
) -> dict[str, Any]:
    """P1/P2：persistence 鲁棒性（raw/primary/robust）下 break effect 方向一致。

    trajectories 以 {persistence: 轨迹表} 传入（每次用不同 density 的 first-exit 样本），
    确保鲁棒性检验真正遍历不同口径，而非对同一表重复。
    """
    out: dict[str, Any] = {"break_date": str(break_date), "window": window, "persistences": {}}
    for p, traj in sorted(trajectories.items()):
        out["persistences"][str(p)] = _break_effect(traj, break_date, window)
    signs = {int(k): v["effect"] for k, v in out["persistences"].items() if v.get("effect") is not None}
    out["consistent_direction"] = bool(signs) and len({e > 0 for e in signs.values()}) == 1
    return out


def structural_break_report(
    trajectories: dict[int, pd.DataFrame],
    break_date: Any = "2024-09-24",
    windows: tuple[int, ...] = (40, 63, 90),
    n_boot: int = 2000,
    seed: int = 42,
    horizon: int = CLEAN_ESCAPE_HORIZON,
) -> dict[str, Any]:
    """汇总 3A 断点研究的全部检验结果（供 study3a.py 落盘 + 报告消费）。

    trajectories 以 primary 为主口径；persistence 鲁棒性用全部口径。
    """
    primary = trajectories[PERSISTENCE_PRIMARY]
    hyp = hypothesis_break(primary, break_date, windows)
    ddb = data_driven_breakpoint(primary, window=max(windows))
    boot = date_block_bootstrap(primary, break_date, window=windows[1], n_boot=n_boot, seed=seed)
    pers = persistence_consistency(trajectories, break_date, window=windows[1])
    return {
        "horizon": horizon,
        "break_date": str(break_date),
        "windows": list(windows),
        "hypothesis_break": hyp,
        "data_driven_breakpoint": ddb,
        "date_block_bootstrap": boot,
        "persistence_consistency": pers,
    }
