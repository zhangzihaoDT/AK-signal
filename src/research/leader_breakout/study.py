"""Leader Breakout Event V1 — Q1/Q2/Q3 study engine + pre-frozen layered gates.

纯只读研究：不联网、不改生产。三问：
  Q1 前瞻收益：单点突破后分层超额是否为正？
  Q2 提前 vs 等待：事件日入场 vs 等主题确认入场，增量收益/风险？
  Q3 小仓位：以更小 sleeve 纳入，是否改善风险调整后表现（主比等确认）？
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import STUDY_DIR, RULE_ID, SPEC_SOURCE
from .events import (
    PricePanel,
    _theme_memberships,
    build_events,
    build_price_panel,
    build_stock_history,
    theme_confirmation_series,
)
from .spec import LeaderBreakoutSpec, load_spec

logger = logging.getLogger("research.leader_breakout.study")


def _finite(vals: Any) -> np.ndarray:
    clean = [v for v in vals if v is not None and np.isfinite(v)]
    return np.asarray(clean, dtype=float)


def _stats(vals: Any) -> dict[str, Any]:
    arr = _finite(vals)
    if arr.size == 0:
        return {"n": 0, "mean": None, "median": None, "win_rate": None, "std": None}
    return {
        "n": int(arr.size),
        "mean": float(arr.mean()),
        "median": float(np.median(arr)),
        "win_rate": float((arr > 0).mean()),
        "std": float(arr.std(ddof=1)) if arr.size > 1 else None,
    }


def _q1(ev: pd.DataFrame, spec: LeaderBreakoutSpec) -> dict[str, Any]:
    horizons = spec.returns.horizons
    primary = spec.returns.primary_horizon
    by_h: dict[str, Any] = {}
    for h in horizons:
        by_h[str(h)] = {
            "ret": _stats(ev.get(f"ret_{h}", [])),
            "excess": _stats(ev.get(f"exc_{h}", [])),
        }
    # 分年
    by_year: dict[str, Any] = {}
    if not ev.empty:
        yr = pd.to_datetime(ev["event_date"]).dt.year
        for y, g in ev.assign(_y=yr).groupby("_y"):
            by_year[str(int(y))] = {
                "n": int(len(g)),
                "excess_median": _stats(g[f"exc_{primary}"])["median"],
                "excess_mean": _stats(g[f"exc_{primary}"])["mean"],
                "ret_median": _stats(g[f"ret_{primary}"])["median"],
            }
    # 标签分层
    col = f"exc_{primary}"
    tag_groups: dict[str, Any] = {}
    for tag in ("above_ma60", "ma60_cross_within_10d", "above_ma120", "new_high_20d",
                "watch_level", "drawdown_bucket"):
        if tag not in ev.columns:
            continue
        tag_groups[tag] = {}
        for val, g in ev.groupby(tag, dropna=False):
            tag_groups[tag][str(val)] = {"n": int(len(g)), "excess_median": _stats(g[col])["median"],
                                         "excess_mean": _stats(g[col])["mean"],
                                         "ret_median": _stats(g[f"ret_{primary}"])["median"]}
    # 条件组合：MA60 × MA120
    combo: dict[str, Any] = {}
    if {"above_ma60", "above_ma120"}.issubset(ev.columns):
        for (a60, a120), g in ev.groupby(["above_ma60", "above_ma120"]):
            combo[f"ma60={bool(a60)}|ma120={bool(a120)}"] = {
                "n": int(len(g)), "excess_median": _stats(g[col])["median"],
                "ret_median": _stats(g[f"ret_{primary}"])["median"]}
    # 主题广度三分位
    breadth: dict[str, Any] = {}
    if "theme_breadth" in ev.columns and ev["theme_breadth"].notna().any():
        try:
            tert = pd.qcut(ev["theme_breadth"], 3, labels=["low", "mid", "high"], duplicates="drop")
            for val, g in ev.assign(_b=tert).groupby("_b", observed=True):
                breadth[str(val)] = {"n": int(len(g)), "excess_median": _stats(g[col])["median"],
                                     "ret_median": _stats(g[f"ret_{primary}"])["median"]}
        except ValueError:
            breadth = {}
    return {"by_horizon": by_h, "by_year": by_year, "by_tag": tag_groups,
            "ma60_x_ma120": combo, "theme_breadth_tertile": breadth}


def _q2(ev: pd.DataFrame, spec: LeaderBreakoutSpec) -> dict[str, Any]:
    primary = spec.returns.primary_horizon
    if ev.empty:
        return {"n_events": 0}
    has = ev[ev.get("has_confirm_within_lookahead", False) == True]  # noqa: E712
    no = ev[ev.get("has_confirm_within_lookahead", False) != True]  # noqa: E712
    early = _finite(has.get(f"ret_{primary}", []))
    wait = _finite(has.get("wait_ret_primary", []))
    n = min(early.size, wait.size)
    inc = early[:n] - wait[:n] if n else np.array([])
    return {
        "n_events": int(len(ev)),
        "n_with_confirm": int(len(has)),
        "n_no_confirm_within_lookahead": int(len(no)),
        "early": _stats(early),
        "wait": _stats(wait),
        "incremental": _stats(inc),
        "share_early_gt_wait": float((inc > 0).mean()) if inc.size else None,
        # 无确认样本单独报告（等待策略下不会入场）
        "no_confirm_early_ret": _stats(no.get(f"ret_{primary}", [])),
    }


def _curve_metrics(s: pd.Series) -> dict[str, Any]:
    if s is None or s.empty or len(s) < 2:
        return {"days": 0, "total": None, "annualized": None, "sharpe": None,
                "max_drawdown": None, "calmar": None}
    eq = (1.0 + s).cumprod()
    total = float(eq.iloc[-1] - 1.0)
    ann = float(eq.iloc[-1] ** (252.0 / len(s)) - 1.0)
    sd = float(s.std(ddof=1))
    vol = sd * np.sqrt(252.0)
    sharpe = float(s.mean() / sd * np.sqrt(252.0)) if sd > 0 else None
    dd = eq / eq.cummax() - 1.0
    maxdd = float(dd.min())
    calmar = float(ann / abs(maxdd)) if maxdd < 0 else None
    return {"days": int(len(s)), "total": total, "annualized": ann, "vol": float(vol),
            "sharpe": sharpe, "max_drawdown": maxdd, "calmar": calmar}


def _sleeve_curve(
    panel: PricePanel,
    ev: pd.DataFrame,
    entry_col: str,
    exit_col: str,
    weight: float,
    cost_bps: float,
) -> pd.Series:
    """研究近似 sleeve：每日等权持有活跃事件，暴露 weight；无仓位时持现金（0）。"""
    if ev.empty or entry_col not in ev.columns or exit_col not in ev.columns:
        return pd.Series(dtype=float)
    daily: dict[pd.Timestamp, list[float]] = {}
    entries: dict[pd.Timestamp, int] = {}
    exits: dict[pd.Timestamp, int] = {}
    for _, e in ev.iterrows():
        ed, xd = e.get(entry_col), e.get(exit_col)
        if ed is None or xd is None or pd.isna(ed) or pd.isna(xd):
            continue
        d0, d1 = pd.Timestamp(ed), pd.Timestamp(xd)
        r = panel.daily_returns(str(e["symbol"]), d0, d1)
        for d, v in r.items():
            daily.setdefault(pd.Timestamp(d), []).append(float(v))
        entries[d0] = entries.get(d0, 0) + 1
        exits[d1] = exits.get(d1, 0) + 1
    if not daily:
        return pd.Series(dtype=float)
    idx = sorted(set(daily) | set(entries) | set(exits))
    rets: list[float] = []
    for d in idx:
        active = daily.get(d, [])
        gross = float(np.mean(active)) if active else 0.0
        cost = weight * (cost_bps / 1e4) * (entries.get(d, 0) + exits.get(d, 0)) / max(1, len(active))
        rets.append(weight * gross - cost)
    return pd.Series(rets, index=pd.DatetimeIndex(idx)).sort_index()


def _q3(ev: pd.DataFrame, spec: LeaderBreakoutSpec, panel: PricePanel, primary: int) -> dict[str, Any]:
    if ev.empty:
        return {"weights": {}}
    cost = spec.portfolio.cost_bps
    out: dict[str, Any] = {"weights": {}, "primary_weight": spec.portfolio.primary_weight}
    for w in spec.portfolio.sleeve_weights:
        early = _sleeve_curve(panel, ev, "entry_date", "exit_date", w, cost)
        wait_ev = ev[ev.get("has_confirm_within_lookahead", False) == True]  # noqa: E712
        wait = _sleeve_curve(panel, wait_ev, "wait_entry_date", "wait_exit_date", w, cost)
        em, wm = _curve_metrics(early), _curve_metrics(wait)
        out["weights"][str(w)] = {
            "early": em, "wait": wm,
            "delta_calmar": (em.get("calmar") - wm.get("calmar")) if (em.get("calmar") is not None and wm.get("calmar") is not None) else None,
            "delta_sharpe": (em.get("sharpe") - wm.get("sharpe")) if (em.get("sharpe") is not None and wm.get("sharpe") is not None) else None,
        }
    return out


def _date_block_bootstrap(ev: pd.DataFrame, col: str, n_boot: int, seed: int, ci: float) -> dict[str, Any]:
    if ev.empty or col not in ev.columns:
        return {"point": None, "lo": None, "hi": None, "pass": False}
    groups: dict[Any, np.ndarray] = {}
    for d, g in ev.assign(_d=pd.to_datetime(ev["event_date"]).dt.date).groupby("_d"):
        arr = _finite(g[col])
        if arr.size:
            groups[d] = arr
    if not groups:
        return {"point": None, "lo": None, "hi": None, "pass": False}
    keys = list(groups)
    allv = np.concatenate([groups[k] for k in keys])
    point = float(allv.mean())
    rng = np.random.default_rng(seed)
    means = np.empty(n_boot, dtype=float)
    for b in range(n_boot):
        sample = rng.integers(0, len(keys), size=len(keys))
        cat = np.concatenate([groups[keys[i]] for i in sample])
        means[b] = cat.mean() if cat.size else np.nan
    means = means[np.isfinite(means)]
    lo = float(np.quantile(means, (1 - ci) / 2)) if means.size else None
    hi = float(np.quantile(means, 1 - (1 - ci) / 2)) if means.size else None
    return {"point": point, "lo": lo, "hi": hi, "n_dates": len(keys),
            "pass": bool(lo is not None and lo > 0)}


def _single_stock_share(ev: pd.DataFrame, col: str) -> float | None:
    pos = ev[[col, "symbol"]].copy()
    pos[col] = pd.to_numeric(pos[col], errors="coerce")
    pos = pos[pos[col] > 0]
    if pos.empty:
        return None
    total = pos[col].sum()
    if total <= 0:
        return None
    by = pos.groupby("symbol")[col].sum()
    return float(by.max() / total)


def _gates(ev: pd.DataFrame, spec: LeaderBreakoutSpec, q3: dict[str, Any]) -> dict[str, Any]:
    primary = spec.returns.primary_horizon
    col = f"exc_{primary}"
    g = spec.gates
    l1 = _date_block_bootstrap(ev, col, g.n_boot, g.seed, g.ci)
    # L2
    pos_years = 0
    year_detail: dict[str, Any] = {}
    if not ev.empty:
        yr = pd.to_datetime(ev["event_date"]).dt.year
        for y, sub in ev.assign(_y=yr).groupby("_y"):
            m = _stats(sub[col])["median"]
            year_detail[str(int(y))] = m
            if m is not None and m > 0:
                pos_years += 1
    l2 = {"positive_years": pos_years, "min_required": g.min_positive_years,
          "detail": year_detail, "pass": pos_years >= g.min_positive_years}
    # L3
    ret_stats = _stats(ev[f"ret_{primary}"]) if not ev.empty else {"win_rate": None}
    mae_stats = _stats(ev["mae_primary"]) if "mae_primary" in ev.columns else {"median": None}
    share = _single_stock_share(ev, col)
    l3 = {
        "median_mae": mae_stats.get("median"),
        "mae_ok": bool(mae_stats.get("median") is not None and mae_stats["median"] >= g.max_median_mae),
        "win_rate": ret_stats.get("win_rate"),
        "win_ok": bool(ret_stats.get("win_rate") is not None and ret_stats["win_rate"] >= g.min_win_rate),
        "single_stock_share": share,
        "concentration_ok": bool(share is not None and share <= g.max_single_stock_share),
    }
    l3["pass"] = bool(l3["mae_ok"] and l3["win_ok"] and l3["concentration_ok"])
    # L4
    w = str(spec.portfolio.primary_weight)
    wm = (q3.get("weights", {}) or {}).get(w, {})
    em, wait_m = wm.get("early", {}), wm.get("wait", {})
    calmar_ok = (em.get("calmar") is not None and wait_m.get("calmar") is not None
                 and em["calmar"] >= wait_m["calmar"])
    sharpe_ok = em.get("sharpe") is not None and em["sharpe"] >= g.min_sharpe
    l4 = {"primary_weight": spec.portfolio.primary_weight, "early": em, "wait": wait_m,
          "calmar_ge_wait": bool(calmar_ok), "sharpe_ok": bool(sharpe_ok),
          "pass": bool(calmar_ok and sharpe_ok)}
    verdict = "PASS" if (l1["pass"] and l2["pass"] and l3["pass"] and l4["pass"]) else "FAIL"
    return {"L1_statistical": l1, "L2_economic_years": l2, "L3_path_risk": l3,
            "L4_portfolio": l4, "verdict": verdict}


def _sensitivity(
    spec: LeaderBreakoutSpec,
    history: dict[str, pd.DataFrame],
    conf: pd.DataFrame,
    panel: PricePanel,
    start_date: str | None,
    end_date: str | None,
) -> dict[str, Any]:
    g = spec.gates
    grid: list[dict[str, Any]] = []
    for thr in g.thresholds:
        for cd in g.cooldowns:
            s2 = replace(spec, event=replace(spec.event, score_threshold=float(thr),
                                             cooldown_trading_days=int(cd)))
            ev2, _ = build_events(s2, history=history, conf=conf, panel=panel,
                                  start_date=start_date, end_date=end_date)
            med = _stats(ev2[f"exc_{spec.returns.primary_horizon}"])["median"] if not ev2.empty else None
            grid.append({"threshold": float(thr), "cooldown": int(cd),
                         "n_events": int(len(ev2)), "excess_median": med,
                         "positive": bool(med is not None and med > 0)})
    pos = sum(1 for r in grid if r["positive"])
    return {"grid": grid, "n_positive": pos, "n_total": len(grid),
            "pass": bool(pos >= max(1, int(np.ceil(0.5 * len(grid)))))}


def run(
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    spec_path: Path | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    spec = load_spec(spec_path)
    memberships = _theme_memberships(spec)
    symbols = sorted({s for m in memberships.values() for s, _, _ in m})
    from src.common.paths import selection_universe_path
    from src.selection.universe import load_universe_items
    from src.trend_engine import inputs as trend_inputs
    items = load_universe_items(selection_universe_path())
    history = build_stock_history(trend_inputs.stock_items(items))
    conf = theme_confirmation_series(spec)
    panel = build_price_panel(symbols)
    ev, meta = build_events(spec, history=history, conf=conf, panel=panel,
                            start_date=start_date, end_date=end_date)

    q1 = _q1(ev, spec)
    q2 = _q2(ev, spec)
    q3 = _q3(ev, spec, panel, spec.returns.primary_horizon)
    sens = _sensitivity(spec, history, conf, panel, start_date, end_date)
    gates = _gates(ev, spec, q3)

    payload: dict[str, Any] = {
        "meta": {
            "rule_id": RULE_ID, "rule_status": "FROZEN_RESEARCH_HYPOTHESIS",
            "spec_source": SPEC_SOURCE, "frozen_at": spec.frozen_at,
            "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
            "window": {"start": start_date, "end": end_date},
            "n_events": int(len(ev)), "n_symbols": int(meta.get("n_symbols", 0)),
            "themes": list(spec.event.themes),
            "caveats": [
                "theme_unconfirmed 由行业 Gate 历史重建（非 Tier Gate，2026-08 后生产口径不同）",
                "主题池用今日成分回推历史，存在幸存者偏差",
                "sleeve 为研究近似（等权活跃事件、无杠杆上限、close-to-close 日收益）",
            ],
        },
        "q1_forward_edge": q1,
        "q2_early_vs_wait": q2,
        "q3_small_position": q3,
        "sensitivity": sens,
        "gates": gates,
    }
    if persist:
        STUDY_DIR.mkdir(parents=True, exist_ok=True)
        (STUDY_DIR / "leader_breakout_result.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        if not ev.empty:
            ev.to_parquet(STUDY_DIR / "leader_breakout_events.parquet", index=False)
        if not ev.empty:
            ev.to_csv(STUDY_DIR / "leader_breakout_events.csv", index=False)
    logger.info("study done: %d events, verdict=%s", len(ev), gates["verdict"])
    return payload
