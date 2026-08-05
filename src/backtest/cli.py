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
from .trade import trades as bt_trades
from .trade import metrics as bt_metrics
from . import sensitivity as bt_sensitivity
from . import matrix as bt_matrix


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
        ma_window=args.ma_window,
        fee=args.fee,
        slippage=args.slippage,
        universe_mode=args.universe_mode,
        start_date=args.start,
        end_date=args.end,
    )

    out_dir = _out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"{args.theme}_{args.entity_type}_{label}_{args.universe_mode}"
    trades_path = out_dir / f"trades_{suffix}.parquet"
    trades.to_parquet(trades_path, index=False)

    metrics = bt_metrics.compute_metrics(trades)
    metrics["universe_mode"] = args.universe_mode
    metrics["universe_size"] = int(trades["universe_size"].iloc[0]) if not trades.empty else 0
    metrics["universe_config_hash"] = trades["universe_config_hash"].iloc[0] if not trades.empty else ""
    metrics_path = out_dir / f"metrics_{suffix}.json"
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    html_path = bt_metrics.render_report_html(trades, metrics, out_dir, suffix)

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


def cmd_sensitivity(args: argparse.Namespace) -> int:
    signals, label = _load_signals(args.signals)
    result = bt_sensitivity.run_sensitivity(
        signals,
        theme=args.theme,
        entity_type=args.entity_type,
        universe_mode=args.universe_mode,
        fixed_horizons=tuple(int(x) for x in args.horizons.split(",") if x.strip().isdigit())
        or (5, 10, 20, 40, 60),
        ma_windows=tuple(int(x) for x in args.ma_windows.split(",") if x.strip().isdigit())
        or (10, 20, 30, 60),
        costs=tuple(int(x) for x in args.costs.split(",") if x.strip().isdigit())
        or (0, 5, 10, 20),
    )
    out_dir = _out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"{args.theme}_{args.entity_type}_{label}_{args.universe_mode}"
    json_path = out_dir / f"sensitivity_{suffix}.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    html_path = bt_metrics.render_sensitivity_html(
        result, out_dir, suffix)

    print(f"sensitivity: fixed={len(result['fixed_scan'])} ma={len(result['ma_scan'])} "
          f"cost={len(result['cost_scan'])} | universe={result['universe_mode']}"
          f"({result['universe_size']}) hash={result['universe_config_hash']}")
    print(f"  json: {json_path}")
    print(f"  html: {html_path}")
    return 0


def cmd_matrix(args: argparse.Namespace) -> int:
    signals, label = _load_signals(args.signals)
    out_dir = _out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    matrix = bt_matrix.build_matrix(out_dir, label)
    json_path = out_dir / f"matrix_{label}.json"
    json_path.write_text(json.dumps(matrix, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    html_path = bt_matrix.render_matrix_html(matrix, out_dir, label)
    print(f"matrix: {len(matrix['groups'])} groups (label={label})")
    for g in matrix["groups"]:
        if g.get("missing"):
            print(f"  {g['name']}: 缺 sensitivity 报告")
        else:
            f = g["fixed_20"]
            print(f"  {g['name']:<24} n={f.get('n', 0):>4} mean={_pct_pct(f.get('mean_ret'))} "
                  f"win={_pct_fraction(f.get('win_rate'))} top5={_pct_fraction(g.get('top5_share'))}")
    print(f"  html: {html_path}")
    return 0


def cmd_construction(args: argparse.Namespace) -> int:
    signals, label = _load_signals(args.signals)
    from .portfolio import construction as bt_construction
    from .portfolio.metrics import render_construction_html
    result = bt_construction.run_construction_experiments(
        signals,
        initial_capital=args.initial_capital,
        fee_pct=args.fee,
        slippage_pct=args.slippage,
        benchmark=args.benchmark,
        benchmark_fallback=args.benchmark_fallback,
    )
    out_dir = _out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"construction_{label}.json"
    json_path.write_text(json.dumps(bt_construction.summarize(result), ensure_ascii=False,
                                    indent=2, default=str), encoding="utf-8")
    html_path = render_construction_html(result, out_dir, label)

    print(f"construction: baseline total={_pct_pct(result['baseline']['metrics']['total_return_pct'])}")
    for dim, entries in result["experiments"].items():
        best = max(entries, key=lambda e: e["metrics"]["sharpe"] or -9)
        print(f"  {dim:<14} best={best['label']} (sharpe={best['metrics']['sharpe']}, "
              f"total={_pct_pct(best['metrics']['total_return_pct'])})")
    print(f"  json: {json_path}")
    print(f"  html: {html_path}")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="v0.5.2 Trade-level Backtest")
    sub = p.add_subparsers(dest="command")
    p_trades = sub.add_parser("trades", help="逐笔交易模拟（独立等名义本金）")
    p_trades.add_argument("--signals", default="", help="historical_signals parquet 路径")
    p_trades.add_argument("--theme", required=True, help="主题（如 ai_infrastructure）")
    p_trades.add_argument("--entity-type", default="etf", choices=["etf", "stock"],
                          help="实体类型（第一轮：etf）")
    p_trades.add_argument("--exit-policy", default="signal_exit",
                          help="退出策略，逗号分隔或 all（signal_exit,ma_exit,fixed_horizon）")
    p_trades.add_argument("--horizon", type=int, default=20, help="fixed_horizon 持有交易日数")
    p_trades.add_argument("--ma-window", type=int, default=20, help="ma_exit 窗口（默认 20）")
    p_trades.add_argument("--fee", type=float, default=0.0, help="手续费（% of notional，双边各计）")
    p_trades.add_argument("--slippage", type=float, default=0.0, help="滑点（% of price）")
    p_trades.add_argument("--universe-mode", choices=["configured", "theme-matched"],
                          default="configured",
                          help="资产范围：configured=固定资产池（默认）；theme-matched=全市场关键词")
    p_trades.add_argument("--start", default="", help="入场信号日起点 YYYYMMDD")
    p_trades.add_argument("--end", default="", help="入场信号日终点 YYYYMMDD")
    p_trades.add_argument("--log-level", default="INFO")

    p_sens = sub.add_parser("sensitivity", help="退出规则稳健性验证（固定/MA/分年/分ETF/成本）")
    p_sens.add_argument("--signals", default="", help="historical_signals parquet 路径")
    p_sens.add_argument("--theme", required=True, help="主题")
    p_sens.add_argument("--entity-type", default="etf", choices=["etf", "stock"])
    p_sens.add_argument("--universe-mode", choices=["configured", "theme-matched"],
                        default="configured",
                        help="资产范围：configured=固定资产池（默认）；theme-matched=全市场关键词")
    p_sens.add_argument("--horizons", default="5,10,20,40,60", help="固定持有期扫描")
    p_sens.add_argument("--ma-windows", default="10,20,30,60", help="MA 窗口扫描")
    p_sens.add_argument("--costs", default="0,5,10,20", help="成本扫描（bp）")
    p_sens.add_argument("--log-level", default="INFO")

    p_matrix = sub.add_parser("matrix", help="四组对比矩阵（configured vs theme-matched × AI/HC）")
    p_matrix.add_argument("--signals", default="", help="historical_signals parquet 路径（决定 label）")
    p_matrix.add_argument("--log-level", default="INFO")

    p_port = sub.add_parser("portfolio", help="v0.6 共享账户组合模拟（单策略 + Core+Quality）")
    p_port.add_argument("--signals", default="", help="historical_signals parquet 路径")
    p_port.add_argument("--config", default="", help="策略配置 yaml（默认 config/strategies.yaml）")
    p_port.add_argument("--initial-capital", type=float, default=_pspec().initial_capital)
    p_port.add_argument("--max-positions", type=int, default=_pspec().max_positions)
    p_port.add_argument("--max-weight", type=float, default=_pspec().max_weight_per_asset)
    p_port.add_argument("--fee", type=float, default=_espec().fee_pct, help="手续费 %（单边）")
    p_port.add_argument("--slippage", type=float, default=_espec().slippage_pct, help="滑点 %（单边）")
    p_port.add_argument("--modes", default="A,B", help="综合组合资金模式（A/B）")
    p_port.add_argument("--benchmark", default="sh000300", help="基准（默认 sh000300 真指数）")
    p_port.add_argument("--benchmark-fallback", default="510300", help="基准覆盖不足的显式 fallback")
    p_port.add_argument("--no-benchmark-fallback", action="store_true",
                        help="基准覆盖不足时不静默回退")
    p_port.add_argument("--start", default="", help="研究区间起点 YYYYMMDD")
    p_port.add_argument("--end", default="", help="研究区间终点 YYYYMMDD")
    p_port.add_argument("--log-level", default="INFO")

    p_con = sub.add_parser("construction", help="v0.6 组合构建实验（Top-N/加权/持仓/比例/现金）")
    p_con.add_argument("--signals", default="", help="historical_signals parquet 路径")
    p_con.add_argument("--initial-capital", type=float, default=_pspec().initial_capital)
    p_con.add_argument("--fee", type=float, default=_espec().fee_pct)
    p_con.add_argument("--slippage", type=float, default=_espec().slippage_pct)
    p_con.add_argument("--benchmark", default="sh000300")
    p_con.add_argument("--benchmark-fallback", default="510300")
    p_con.add_argument("--log-level", default="INFO")
    return p


def _pspec():
    from src.common.spec.loaders import load_portfolio_spec
    return load_portfolio_spec()


def _espec():
    from src.common.spec.loaders import load_execution_spec
    return load_execution_spec()


def main() -> None:
    args = build_arg_parser().parse_args()
    command = getattr(args, "command", "trades")
    if command == "trades":
        sys.exit(cmd_trades(args))
    elif command == "sensitivity":
        sys.exit(cmd_sensitivity(args))
    elif command == "matrix":
        sys.exit(cmd_matrix(args))
    elif command == "portfolio":
        from .portfolio.cli import cmd_portfolio
        sys.exit(cmd_portfolio(args))
    elif command == "construction":
        sys.exit(cmd_construction(args))
    else:
        sys.exit(cmd_trades(args))
