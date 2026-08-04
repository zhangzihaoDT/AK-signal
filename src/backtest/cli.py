"""
Backtest CLI — 交易层逐笔模拟（v0.5.2）。

用法：
  python src/main.py backtest trades \
      --signals <historical_signals.parquet> \
      --theme ai_infrastructure --entity-type etf \
      --exit-policy signal_exit,ma20_exit,fixed_horizon
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from src.common.paths import outputs_dir
from . import trades as bt_trades
from . import metrics as bt_metrics


def _out_dir() -> Path:
    return outputs_dir() / "research" / "backtest"


def _load_signals(path: str | None) -> tuple[pd.DataFrame, str]:
    if path:
        p = Path(path)
    else:
        p = sorted((outputs_dir() / "research").glob("historical_signals_*.parquet"))[-1] \
            if (outputs_dir() / "research").exists() else None
    if p is None or not p.exists():
        print(f"error: signals file not found: {p}")
        sys.exit(2)
    return pd.read_parquet(p), p.stem.removeprefix("historical_signals_")


def cmd_trades(args: argparse.Namespace) -> int:
    signals, label = _load_signals(args.signals)
    policies = tuple(x.strip() for x in args.exit_policy.split(",") if x.strip())
    if args.exit_policy.lower() == "all":
        policies = ("signal_exit", "ma20_exit", "fixed_horizon")

    trades = bt_trades.run_backtest(
        signals,
        theme=args.theme,
        entity_type=args.entity_type,
        exit_policies=policies,
        horizon=args.horizon,
        fee=args.fee,
        slippage=args.slippage,
        start_date=args.start,
        end_date=args.end,
    )

    out_dir = _out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    trades_path = out_dir / f"trades_{args.theme}_{args.entity_type}_{label}.parquet"
    trades.to_parquet(trades_path, index=False)

    metrics = bt_metrics.compute_metrics(trades)
    metrics_path = out_dir / f"metrics_{args.theme}_{args.entity_type}_{label}.json"
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    html_path = bt_metrics.render_report_html(
        trades, metrics, out_dir, f"{args.theme}_{args.entity_type}_{label}")

    print(f"backtest: {len(trades)} trades, {len(policies)} policies")
    for policy, m in metrics.get("policies", {}).items():
        r = m.get("mean_return_pct")
        print(f"  {policy}: n={m.get('n_closed', 0)} win={_pct_fraction(m.get('win_rate'))} "
              f"mean={_pct_pct(m.get('mean_return_pct'))} total_units={m.get('total_return_units')}")
    print(f"  trades: {trades_path}")
    print(f"  metrics: {metrics_path}")
    print(f"  html: {html_path}")
    return 0


def _pct_fraction(v) -> str:
    return "—" if v is None else f"{v * 100:.1f}%"


def _pct_pct(v) -> str:
    return "—" if v is None else f"{v:.2f}%"


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="v0.5.2 Trade-level Backtest")
    sub = p.add_subparsers(dest="command")
    p_trades = sub.add_parser("trades", help="逐笔交易模拟（独立等名义本金）")
    p_trades.add_argument("--signals", default="", help="historical_signals parquet 路径")
    p_trades.add_argument("--theme", required=True, help="主题（如 ai_infrastructure）")
    p_trades.add_argument("--entity-type", default="etf", choices=["etf", "stock"],
                          help="实体类型（第一轮：etf）")
    p_trades.add_argument("--exit-policy", default="signal_exit",
                          help="退出策略，逗号分隔或 all（signal_exit,ma20_exit,fixed_horizon）")
    p_trades.add_argument("--horizon", type=int, default=20, help="fixed_horizon 持有交易日数")
    p_trades.add_argument("--fee", type=float, default=0.0, help="手续费（% of notional，双边各计）")
    p_trades.add_argument("--slippage", type=float, default=0.0, help="滑点（% of price）")
    p_trades.add_argument("--start", default="", help="入场信号日起点 YYYYMMDD")
    p_trades.add_argument("--end", default="", help="入场信号日终点 YYYYMMDD")
    p_trades.add_argument("--log-level", default="INFO")
    return p


def main() -> None:
    args = build_arg_parser().parse_args()
    command = getattr(args, "command", "trades")
    if command == "trades":
        sys.exit(cmd_trades(args))
    else:
        sys.exit(cmd_trades(args))
