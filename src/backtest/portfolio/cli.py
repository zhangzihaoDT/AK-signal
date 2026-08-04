"""
Portfolio CLI — v0.6 共享账户模拟。

用法：
  python src/main.py backtest portfolio \
      --signals <historical_signals.parquet> \
      [--initial-capital 1000000] [--max-positions 5] [--max-weight 0.20] \
      [--fee 0.05] [--slippage 0.05] [--config config/strategies.yaml] [--modes A,B]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from src.common.paths import outputs_dir
from . import simulate as sim
from . import report as port_report


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


def cmd_portfolio(args: argparse.Namespace) -> int:
    signals, label = _load_signals(args.signals)
    result = sim.run_portfolios(
        signals,
        strategies_path=Path(args.config) if args.config else None,
        initial_capital=args.initial_capital,
        max_positions=args.max_positions,
        max_weight_per_asset=args.max_weight,
        fee_pct=args.fee,
        slippage_pct=args.slippage,
        modes=tuple(m.strip() for m in args.modes.split(",") if m.strip()),
    )
    out_dir = outputs_dir() / "research" / "portfolio"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = port_report.save_portfolio_json(result, out_dir, label)
    html_path = port_report.render_portfolio_html(result, out_dir, label)

    print(f"portfolio: label={label}")
    for key, v in result["single"].items():
        m = sim.nav_metrics(v["account"].nav_frame())
        print(f"  {v['label']:<14} n={v['n_filled']:>4} total={_pct(m.get('total_return_pct'))} "
              f"dd={_pct(m.get('max_drawdown_pct'))} sharpe={m.get('sharpe')}")
    for mode, v in result["combined"].items():
        m = sim.nav_metrics(v["account"].nav_frame())
        print(f"  {v['label']:<14} n={v['n_filled']:>4} total={_pct(m.get('total_return_pct'))} "
              f"dd={_pct(m.get('max_drawdown_pct'))} sharpe={m.get('sharpe')}")
    print(f"  json: {json_path}")
    print(f"  html: {html_path}")
    return 0


def _pct(v) -> str:
    return "—" if v is None else f"{v:.1f}%"


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="v0.6 Shared-account Portfolio Simulation")
    p.add_argument("--signals", default="", help="historical_signals parquet 路径")
    p.add_argument("--config", default="", help="策略配置 yaml（默认 config/strategies.yaml）")
    p.add_argument("--initial-capital", type=float, default=1_000_000.0)
    p.add_argument("--max-positions", type=int, default=5)
    p.add_argument("--max-weight", type=float, default=0.20, help="单资产权重上限")
    p.add_argument("--fee", type=float, default=0.05, help="手续费 %（单边）")
    p.add_argument("--slippage", type=float, default=0.05, help="滑点 %（单边）")
    p.add_argument("--modes", default="A,B", help="综合组合资金模式（A=统一等权 / B=AI60+HC40）")
    p.add_argument("--log-level", default="INFO")
    return p


def main() -> None:
    args = build_arg_parser().parse_args()
    sys.exit(cmd_portfolio(args))
