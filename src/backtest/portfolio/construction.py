"""
Portfolio Construction 实验（v0.6）— 不改入场/出场规则，只改组合构建方式。

五组实验（每组仅变一个维度，其余取基线）：
  top_n          按入场分取 Top-N 实体（3/5/10）
  score_weight   等权 vs 按入场分加权（RPS15，score_reference=50）
  max_positions  最大持仓（3/5/8）
  ai_ratio       AI/HC 配置比例（50/60/70%）
  deploy_ratio   现金比例（100%/80%/60% 资金动用）

若这些组合优化后仍跑不赢 HS300 → 问题在 Strategy；若稳定跑赢 → 提升来源是资金配置。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

from src.common.paths import config_dir, raw_dir
from src.research.replay import engine as replay_engine
from .engine import PortfolioAccount, build_close_prices
from .nav import (
    benchmark_series, benchmark_on_calendar, build_trading_calendar,
    nav_metrics, relative_metrics, requested_range,
)
from .simulate import load_strategies, strategy_trades

logger = logging.getLogger("backtest.portfolio.construction")

# 组合构建基线：equal 权重 1/max_positions=20%，上限放宽到 30% 以允许 score-weight 上倾
BASE_MAX_WEIGHT = 0.30
SCORE_REFERENCE = 50.0


def _top_n_trades(trades: pd.DataFrame, n: int | None) -> pd.DataFrame:
    """仅保留入场分最高的 N 个实体（universe 缩减）。"""
    if n is None:
        return trades
    filled = trades[trades["entry_status"] == "filled"]
    if filled.empty:
        return trades
    per_entity = filled.groupby("entity_code")["entry_score"].max()
    top = set(per_entity.sort_values(ascending=False).head(n).index)
    return trades[trades["entity_code"].isin(top)]


def _run_one(
    trades: pd.DataFrame,
    closes: dict[str, pd.Series],
    calendar: list[str],
    bench_norm: pd.Series,
    label: str,
    *,
    initial_capital: float,
    max_positions: int,
    score_weighted: bool,
    deploy_ratio: float,
    fee_pct: float,
    slippage_pct: float,
) -> dict[str, Any]:
    acc = PortfolioAccount(
        initial_capital=initial_capital, max_positions=max_positions,
        max_weight_per_asset=BASE_MAX_WEIGHT, fee_pct=fee_pct, slippage_pct=slippage_pct,
        label=label, score_weighted=score_weighted, score_reference=SCORE_REFERENCE,
        deploy_ratio=deploy_ratio)
    acc.run(trades, closes, calendar)
    nav = acc.nav_frame()
    return {
        "label": label, "nav": nav,
        "metrics": nav_metrics(nav),
        "relative": relative_metrics(nav, bench_norm),
        "contribution": acc.contribution(),
    }


def run_construction_experiments(
    signals: pd.DataFrame,
    *,
    strategies_path: Path | None = None,
    initial_capital: float | None = None,
    fee_pct: float | None = None,
    slippage_pct: float | None = None,
    benchmark: str = "sh000300",
    benchmark_fallback: str = "510300",
    start_date: str = "",
    end_date: str = "",
    cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """运行五组组合构建实验（AI-20 + HC-20 综合账户）。

    资金/成本默认来自统一 Strategy Specification（portfolio.yaml / execution.yaml）。
    """
    from src.common.spec.loaders import load_execution_spec, load_portfolio_spec

    pspec = load_portfolio_spec()
    espec = load_execution_spec()
    initial_capital = initial_capital if initial_capital is not None else pspec.initial_capital
    fee_pct = fee_pct if fee_pct is not None else espec.fee_pct
    slippage_pct = slippage_pct if slippage_pct is not None else espec.slippage_pct

    strategies = load_strategies(strategies_path)
    if "ai_20" not in strategies or "hc_20" not in strategies:
        raise ValueError("config/strategy_spec.yaml 需包含 ai_20 与 hc_20")
    cache = cache or replay_engine.build_replay_cache()
    closes = build_close_prices(cache)

    r_start, r_end = requested_range(signals)
    cal_start, cal_end = start_date or r_start, end_date or r_end
    calendar = build_trading_calendar(cache, cal_start, cal_end)
    bench_close, _meta = benchmark_series(cache, benchmark, start=cal_start, end=cal_end,
                                          fallback=benchmark_fallback, allow_fallback=True)
    bench_norm = benchmark_on_calendar(bench_close, calendar)

    # 策略成交序列只构建一次（入场/出场规则不变）
    ai_trades = strategy_trades(signals, strategies["ai_20"], cache)
    hc_trades = strategy_trades(signals, strategies["hc_20"], cache)

    def run(label: str, *, top_n: int | None, score: bool, max_pos: int,
            ai_ratio: float, deploy: float) -> dict[str, Any]:
        combined = pd.concat([
            _top_n_trades(ai_trades, top_n).assign(weight=ai_ratio),
            _top_n_trades(hc_trades, top_n).assign(weight=round(1 - ai_ratio, 3)),
        ], ignore_index=True)
        return _run_one(combined, closes, calendar, bench_norm, label,
                        initial_capital=initial_capital, max_positions=max_pos,
                        score_weighted=score, deploy_ratio=deploy,
                        fee_pct=fee_pct, slippage_pct=slippage_pct)

    base = dict(top_n=None, score=False, max_pos=5, ai_ratio=0.5, deploy=1.0)
    baseline = run("baseline", **base)

    experiments = {
        "top_n": [run(f"top_{n}", **{**base, "top_n": n}) for n in (3, 5, 10)],
        "score_weight": [
            run("equal", **base),
            run("score_weight", **{**base, "score": True}),
        ],
        "max_positions": [run(f"max_{n}", **{**base, "max_pos": n}) for n in (3, 5, 8)],
        "ai_ratio": [run(f"ai{int(r * 100)}", **{**base, "ai_ratio": r}) for r in (0.5, 0.6, 0.7)],
        "deploy_ratio": [run(f"cash{int(r * 100)}", **{**base, "deploy": r}) for r in (1.0, 0.8, 0.6)],
    }

    logger.info("construction experiments done: top_n=%d score=%d max_pos=%d ai=%d cash=%d",
                len(experiments["top_n"]), len(experiments["score_weight"]),
                len(experiments["max_positions"]), len(experiments["ai_ratio"]),
                len(experiments["deploy_ratio"]))
    return {
        "baseline": baseline,
        "experiments": experiments,
        "params": dict(initial_capital=initial_capital, fee_pct=fee_pct,
                       slippage_pct=slippage_pct, max_weight_per_asset=BASE_MAX_WEIGHT,
                       score_reference=SCORE_REFERENCE,
                       calendar_start=cal_start, calendar_end=cal_end,
                       trading_days=len(calendar),
                       benchmark_symbol=benchmark, benchmark_source=_meta.get("source", ""),
                       benchmark_fallback_used=_meta.get("fallback_used", False)),
    }


def _metrics_row(entry: dict[str, Any]) -> dict[str, Any]:
    m = entry["metrics"]
    r = entry["relative"]
    return {
        "label": entry["label"],
        "total": m["total_return_pct"], "annualized": m["annualized_pct"],
        "max_dd": m["max_drawdown_pct"], "sharpe": m["sharpe"], "calmar": m["calmar"],
        "excess": r["excess_pct"], "daily_outp": r["daily_outperformance_rate"],
        "bench": r["bench_total_pct"],
    }


def summarize(result: dict[str, Any]) -> dict[str, Any]:
    """实验结果 → 每维度一行一配置的对比表。"""
    out: dict[str, Any] = {"baseline": _metrics_row(result["baseline"]), "dimensions": {}}
    for dim, entries in result["experiments"].items():
        out["dimensions"][dim] = [_metrics_row(e) for e in entries]
    return out
