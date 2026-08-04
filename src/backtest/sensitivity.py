"""
退出规则稳健性验证（v0.5.2 第二轮）— 不是寻找最优参数，而是验证是否存在稳定区间。

五项分析：
  1. fixed scan      固定持有期 5/10/20/40/60 —— 看是否存在 10-30 日稳定平台
  2. ma scan         MA 窗口 10/20/30/60 —— Profit Factor / 中位 / 持有期 / 大盈利贡献 / 换手
  3. by year         分 2024 / 2025 / 2026 YTD —— 每年是否为正、排除最强年份是否仍成立
  4. by etf          按 entity_code 汇总 —— 利润是否由少数 ETF 贡献（幸存者偏差）
  5. cost scan       成本 0/5/10/20 bp —— signal_exit 短持高换手是否恶化更快
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from .trade.trades import run_backtest
from .trade.strategy import entry as entry_mod

logger = logging.getLogger("backtest.sensitivity")

REFERENCE_POLICIES = ("signal_exit", "ma20_exit", "fixed_20")


def _closed(g: pd.DataFrame) -> pd.DataFrame:
    return g[(g["entry_status"] == "filled") & (g["exit_status"] == "closed")]


def _stats(g: pd.DataFrame) -> dict[str, Any]:
    closed = _closed(g)
    ret = pd.to_numeric(closed["return_pct"], errors="coerce").dropna()
    rec: dict[str, Any] = {"n": int(len(closed))}
    if ret.empty:
        return rec
    wins = ret[ret > 0]
    losses = ret[ret < 0]
    # 大盈利贡献：收益 top 10% 的盈利之和 / 总盈利
    sorted_win = wins.sort_values(ascending=False)
    big = sorted_win.head(max(1, int(len(sorted_win) * 0.1)))
    rec.update({
        "win_rate": round(float((ret > 0).mean()), 4),
        "mean_ret": round(float(ret.mean()), 4),
        "median_ret": round(float(ret.median()), 4),
        "profit_factor": round(float(wins.sum() / abs(losses.sum())), 4)
            if not losses.empty and losses.sum() != 0 else None,
        "total_units": round(float(ret.sum()), 2),
        "big_win_share": round(float(big.sum() / wins.sum()), 4) if wins.sum() else None,
        "avg_holding_days": round(float(pd.to_numeric(closed["holding_days"], errors="coerce").mean()), 1),
    })
    return rec


def _span_years(trades: pd.DataFrame) -> float:
    filled = trades[trades["entry_status"] == "filled"]
    if filled.empty:
        return 1.0
    s = pd.to_datetime(filled["entry_fill_date"], errors="coerce").min()
    e = pd.to_datetime(filled["entry_fill_date"], errors="coerce").max()
    if pd.isna(s) or pd.isna(e):
        return 1.0
    return max((e - s).days / 365.25, 1e-6)


def _policy_table(trades: pd.DataFrame) -> list[dict[str, Any]]:
    span = _span_years(trades)
    out: list[dict[str, Any]] = []
    for policy, g in trades.groupby("exit_policy"):
        rec = _stats(g)
        rec["label"] = policy
        rec["trades_per_year"] = round(rec["n"] / span, 1) if rec["n"] else 0
        out.append(rec)
    out.sort(key=lambda r: r["label"])
    return out


def _by_year(trades: pd.DataFrame) -> dict[str, Any]:
    closed = trades[(trades["entry_status"] == "filled") & (trades["exit_status"] == "closed")].copy()
    rows: list[dict[str, Any]] = []
    if not closed.empty:
        closed["year"] = pd.to_datetime(closed["entry_fill_date"], errors="coerce").dt.year.astype("Int64")
        for (policy, year), g in closed.groupby(["exit_policy", "year"]):
            rec = _stats(g)
            rec.update({"policy": policy, "year": int(year), "positive": (rec["mean_ret"] or 0) > 0})
            rows.append(rec)
    # 排除最强年份后的均值（每策略）
    exclude_best: dict[str, Any] = {}
    for policy in closed["exit_policy"].unique() if not closed.empty else []:
        sub = [r for r in rows if r["policy"] == policy]
        if not sub:
            continue
        best = max(sub, key=lambda r: r["mean_ret"] or -1e9)
        others = [r for r in sub if r["year"] != best["year"]]
        valid = [r["mean_ret"] for r in others if r["mean_ret"] is not None]
        exclude_best[policy] = {
            "best_year": int(best["year"]), "best_mean": best["mean_ret"],
            "mean_excluding_best": round(float(np.mean(valid)), 4) if valid else None,
            "n_years_excluding_best": len(others),
        }
    return {"rows": rows, "exclude_best": exclude_best}


def _by_etf(trades: pd.DataFrame, top_n: int = 5) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for policy, g in trades.groupby("exit_policy"):
        closed = _closed(g)
        if closed.empty:
            continue
        records = []
        for code, gx in closed.groupby("entity_code"):
            rec = _stats(gx)
            rec["entity_code"] = str(code)
            records.append(rec)
        per = pd.DataFrame(records) if records else pd.DataFrame(columns=["entity_code", "n"])
        per = per[per["n"] > 0]
        if per.empty:
            continue
        per["total_units"] = per["total_units"].fillna(0.0)
        grand = float(per["total_units"].sum())
        per["share"] = round(per["total_units"] / grand, 4) if grand else 0.0
        per = per.sort_values("total_units", ascending=False).reset_index(drop=True)
        top_share = round(float(per["total_units"].head(top_n).sum() / grand), 4) if grand else 0.0
        rows = per.head(top_n)[["entity_code", "n", "mean_ret", "total_units", "share"]].to_dict(orient="records")
        out.append({"policy": policy, "n_entities": int(len(per)),
                    f"top{top_n}_share": top_share, "top": rows})
    return out


def _cost_table(cost_trades: pd.DataFrame) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for label, g in cost_trades.groupby("exit_policy"):
        base = label.rsplit("_c", 1)[0]
        bp = int(label.rsplit("_c", 1)[1]) if "_c" in label else 0
        rec = _stats(g)
        rec.update({"base": base, "cost_bp": bp, "label": label})
        out.append(rec)
    # 按 base 排序，附 0bp 差值
    out.sort(key=lambda r: (r["base"], r["cost_bp"]))
    return out


def run_sensitivity(
    signals: pd.DataFrame,
    *,
    theme: str,
    entity_type: str = "etf",
    universe_mode: str = "configured",
    fixed_horizons: tuple[int, ...] = (5, 10, 20, 40, 60),
    ma_windows: tuple[int, ...] = (10, 20, 30, 60),
    costs: tuple[int, ...] = (0, 5, 10, 20),
    cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """执行五项稳健性分析，返回结构化结果。"""
    zero = {"fee": 0.0, "slippage": 0.0}

    fixed_configs = [{"label": f"fixed_{h}", "policy": "fixed_horizon",
                      "params": {"horizon": h}, **zero} for h in fixed_horizons]
    fixed_trades = run_backtest(signals, theme=theme, entity_type=entity_type,
                                exit_configs=fixed_configs, universe_mode=universe_mode,
                                cache=cache)

    ma_configs = [{"label": f"ma{w}_exit", "policy": "ma_exit",
                   "params": {"window": w}, **zero} for w in ma_windows]
    ma_trades = run_backtest(signals, theme=theme, entity_type=entity_type,
                             exit_configs=ma_configs, universe_mode=universe_mode,
                             cache=cache)

    ref_configs = [
        {"label": "signal_exit", "policy": "signal_exit", "params": {}, **zero},
        {"label": "ma20_exit", "policy": "ma_exit", "params": {"window": 20}, **zero},
        {"label": "fixed_20", "policy": "fixed_horizon", "params": {"horizon": 20}, **zero},
    ]
    ref_trades = run_backtest(signals, theme=theme, entity_type=entity_type,
                              exit_configs=ref_configs, universe_mode=universe_mode,
                              cache=cache)

    cost_configs: list[dict[str, Any]] = []
    for base_label, policy, params in [
        ("signal_exit", "signal_exit", {}),
        ("ma20_exit", "ma_exit", {"window": 20}),
        ("fixed_20", "fixed_horizon", {"horizon": 20}),
    ]:
        for bp in costs:
            cost_configs.append({"label": f"{base_label}_c{bp}", "policy": policy,
                                 "params": params, "fee": bp / 100.0, "slippage": 0.0})
    cost_trades = run_backtest(signals, theme=theme, entity_type=entity_type,
                               exit_configs=cost_configs, universe_mode=universe_mode,
                               cache=cache)

    result = {
        "theme": theme, "entity_type": entity_type,
        "universe_mode": universe_mode,
        "universe_size": entry_mod.universe_size(signals, theme, universe_mode),
        "universe_config_hash": entry_mod.universe_config_hash(universe_mode),
        "fixed_scan": _policy_table(fixed_trades),
        "ma_scan": _policy_table(ma_trades),
        "by_year": _by_year(ref_trades),
        "by_etf": _by_etf(ref_trades),
        "cost_scan": _cost_table(cost_trades),
    }
    logger.info("sensitivity complete: fixed=%d ma=%d cost=%d",
                len(result["fixed_scan"]), len(result["ma_scan"]), len(result["cost_scan"]))
    return result
