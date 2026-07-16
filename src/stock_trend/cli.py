"""
stock_trend CLI

职责：个股模块参数解析、子命令处理、调用 pipeline
不包含：业务编排、指标计算
"""

from __future__ import annotations

import argparse

from .pipeline import run_stock_trend


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="A股技术趋势监控（极简版）")
    p.add_argument("--start-date", default="20180101", help="起始日期 YYYYMMDD")
    p.add_argument("--end-date", default="", help="结束日期 YYYYMMDD（默认到最新）")
    p.add_argument("--adjust", default="qfq", choices=["", "qfq", "hfq"], help="复权方式")
    p.add_argument("--force", action="store_true", help="忽略缓存重新拉取")
    p.add_argument("--refresh-all", action="store_true", help="强制全部尝试在线刷新")
    p.add_argument("--refresh-needed", action="store_true", help="只更新需要更新的资产（默认行为）")
    p.add_argument("--refresh-missing", action="store_true", help="只更新无缓存资产")
    p.add_argument("--offline", action="store_true", help="完全不联网，只用缓存生成报告")
    p.add_argument("--only-symbols", default="", help="仅运行指定 symbols，逗号分隔；可用 CN:600519 或 CN_600519 指定市场")
    p.add_argument("--plot-last-n", type=int, default=180, help="图表展示最近 N 个交易日")
    p.add_argument("--log-level", default="INFO", help="日志级别")
    return p


def main() -> None:
    args = build_arg_parser().parse_args()
    run_stock_trend(
        start_date=args.start_date,
        end_date=args.end_date or "22220101",
        adjust=args.adjust,
        force=args.force,
        refresh_all=bool(getattr(args, "refresh_all", False)),
        refresh_missing=bool(getattr(args, "refresh_missing", False)),
        offline=bool(getattr(args, "offline", False)),
        only_symbols=getattr(args, "only_symbols", ""),
        plot_last_n=args.plot_last_n,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
