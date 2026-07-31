"""
Layer ③ stock_trend CLI — A股 AI/科技/半导体 ETF 与个股趋势

子命令：
  run        运行每日分层趋势扫描（读 config/stock_universe.yaml，注入行业确认）
  universe   展示分层资产池（theme → tier → assets）
  report     从已有数据离线重渲染报告
"""

from __future__ import annotations

import argparse
import sys

from .pipeline import run_stock_trend
from . import universe
from src.common.paths import stock_universe_path


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Layer ③ A股 AI/科技/半导体 趋势监控")
    p.add_argument("--start-date", default="20180101", help="起始日期 YYYYMMDD")
    p.add_argument("--end-date", default="", help="结束日期 YYYYMMDD（默认到最新）")
    p.add_argument("--adjust", default="qfq", choices=["", "qfq", "hfq"], help="复权方式")
    p.add_argument("--force", action="store_true", help="忽略缓存重新拉取")
    p.add_argument("--refresh-all", action="store_true", help="强制全部尝试在线刷新")
    p.add_argument("--refresh-missing", action="store_true", help="只更新无缓存资产")
    p.add_argument("--offline", action="store_true", help="完全不联网，只用缓存生成报告")
    p.add_argument("--only-symbols", default="", help="仅运行指定 symbols，逗号分隔；可用 CN:600519 或 CN_600519 指定市场")
    p.add_argument("--plot-last-n", type=int, default=180, help="图表展示最近 N 个交易日")
    p.add_argument("--log-level", default="INFO", help="日志级别")

    sub = p.add_subparsers(dest="command")

    # ── run ─────────────────────────────────────────────
    p_run = sub.add_parser("run", help="运行每日分层趋势扫描")
    p_run.add_argument("--start-date", default="20180101", help="起始日期 YYYYMMDD")
    p_run.add_argument("--end-date", default="", help="结束日期 YYYYMMDD（默认到最新）")
    p_run.add_argument("--adjust", default="qfq", choices=["", "qfq", "hfq"], help="复权方式")
    p_run.add_argument("--force", action="store_true", help="忽略缓存重新拉取")
    p_run.add_argument("--refresh-all", action="store_true", help="强制全部尝试在线刷新")
    p_run.add_argument("--refresh-missing", action="store_true", help="只更新无缓存资产")
    p_run.add_argument("--offline", action="store_true", help="完全不联网，只用缓存生成报告")
    p_run.add_argument("--only-symbols", default="", help="仅运行指定 symbols，逗号分隔；可用 CN:600519 或 CN_600519 指定市场")
    p_run.add_argument("--plot-last-n", type=int, default=180, help="图表展示最近 N 个交易日")
    p_run.add_argument("--log-level", default="INFO", help="日志级别")

    # ── universe ─────────────────────────────────────────
    p_univ = sub.add_parser("universe", help="展示分层资产池")
    p_univ.add_argument("--theme", default="", help="仅显示指定 theme（ai_tech / automotive）")
    p_univ.add_argument("--log-level", default="INFO")

    # ── report ───────────────────────────────────────────
    p_report = sub.add_parser("report", help="从已有数据离线重渲染报告")
    p_report.add_argument("--plot-last-n", type=int, default=180, help="图表展示最近 N 个交易日")
    p_report.add_argument("--log-level", default="INFO")

    return p


def cmd_universe(args: argparse.Namespace) -> None:
    items = universe.load_universe_items(stock_universe_path())
    if not items:
        print("universe 为空")
        return
    theme_filter = getattr(args, "theme", "") or ""
    by_theme: dict[str, list[universe.UniverseItem]] = {}
    for item in items:
        if theme_filter and item.theme != theme_filter:
            continue
        by_theme.setdefault(item.theme, []).append(item)

    for theme, theme_items in by_theme.items():
        label = theme_items[0].theme_label
        print(f"\n[{theme}] {label}（{len(theme_items)} 只）")
        by_tier: dict[str, list[universe.UniverseItem]] = {}
        for it in theme_items:
            by_tier.setdefault(it.tier, []).append(it)
        for tier, tier_items in by_tier.items():
            print(f"  ├─ {tier_items[0].tier_label} ({tier})")
            for it in tier_items:
                ind = f" 行业:{it.sw_industry}" if it.sw_industry else ""
                print(f"      {it.asset.symbol:<10} {it.asset.name:<16} {it.asset.market:<4} {ind}")


def cmd_report(args: argparse.Namespace) -> None:
    run_stock_trend(
        offline=True,
        plot_last_n=args.plot_last_n,
        log_level=args.log_level,
    )


def main() -> None:
    args = build_arg_parser().parse_args()

    # 向后兼容：无子命令或未知子命令 → 视为 run（旧入口 python src/main.py stock [flags]）
    command = getattr(args, "command", None)
    if command == "universe":
        cmd_universe(args)
        return
    if command == "report":
        cmd_report(args)
        return

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
