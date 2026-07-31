"""
Layer 3 selection CLI — 交易标的筛选与表达方式选择

子命令：
  run        构建交易候选（读 Layer①/② + universe + 个股趋势 → 输出候选对象 JSON + HTML）
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from src.common.paths import (
    etf_signal_daily_dir, etf_signal_signals_dir, etf_signal_master_dir,
    sw_industry_confirmation_dir, stock_universe_path, stock_trend_output_dir,
    outputs_dir,
)
from src.stock_trend.universe import load_universe_items
from . import selection, report as sel_report


def build_logger(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("selection")
    if logger.handlers:
        return logger
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    return logger


def _load_latest_parquet(directory: Path, pattern: str) -> pd.DataFrame:
    files = sorted(directory.glob(pattern)) if directory.exists() else []
    if not files:
        return pd.DataFrame()
    try:
        return pd.read_parquet(files[-1])
    except Exception:
        return pd.DataFrame()


def cmd_run(args: argparse.Namespace) -> None:
    logger = build_logger(args.log_level)
    logger.info("=" * 60)
    logger.info("LAYER 3: 交易标的筛选与表达方式选择")
    logger.info("=" * 60)

    date_str = date.today().strftime("%Y%m%d")

    # ── 输入：Layer ① ETF 信号 ───────────────────────────────
    rotation_df = _load_latest_parquet(etf_signal_daily_dir(), "rotation_*.parquet")
    account_df = _load_latest_parquet(etf_signal_signals_dir(), "account_candidates_*.parquet")
    master_df = _load_latest_parquet(etf_signal_master_dir(), "etf_master.parquet")
    if rotation_df.empty:
        logger.error("no rotation data — run etf calculate first")
        return
    logger.info("Layer① rotation: %d ETFs", len(rotation_df))

    # ── 输入：Layer ② 行业确认 ───────────────────────────────
    confirmation_df = _load_latest_parquet(sw_industry_confirmation_dir(), "confirmation_*.parquet")
    if not confirmation_df.empty:
        logger.info("Layer② confirmation: %d industries", len(confirmation_df))
    else:
        logger.warning("Layer② confirmation 为空 — 方向门控将不通过")

    # ── 输入：个股趋势报告 + universe ─────────────────────────
    universe_items = load_universe_items(stock_universe_path())
    trend_csv = None
    trend_files = sorted(stock_trend_output_dir().glob("trend_report_*.csv")) if stock_trend_output_dir().exists() else []
    if trend_files:
        trend_csv = pd.read_csv(trend_files[-1])
    logger.info("universe: %d assets, stock_trend report: %s",
                len(universe_items), trend_files[-1].name if trend_files else "无")

    # ── 构建候选对象 ───────────────────────────────────────────
    candidates = selection.build_candidates(
        rotation_df=rotation_df,
        account_df=account_df,
        master_df=master_df,
        confirmation_df=confirmation_df,
        universe_items=universe_items,
        stock_trend_report=trend_csv if trend_csv is not None else pd.DataFrame(),
    )

    # ── 输出：结构化候选 JSON + HTML 可视化 ────────────────────
    out_dir = outputs_dir() / "selection"
    json_path = selection.save_candidates_json(candidates, out_dir, date_str)
    html_path = sel_report.render_selection_html(candidates, out_dir, date_str)

    # 控制台摘要
    for sub in candidates.get("subthemes", []):
        n_core = len(sub.get("core_etf", []))
        n_sub = len(sub.get("sub_industry_etf", []))
        n_lead = len(sub.get("leaders", []))
        n_hb = len(sub.get("high_beta", []))
        n_eq = len(sub.get("equipment", []))
        logger.info("[%s] %s | %s | 核心ETF=%d 细分ETF=%d 龙头=%d 高弹性=%d 上游=%d",
                    sub.get("subtheme", ""), sub.get("subtheme_label", ""),
                    sub.get("expression_label", ""), n_core, n_sub, n_lead, n_hb, n_eq)

    logger.info("candidates json: %s", json_path)
    logger.info("candidates html: %s", html_path)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Layer 3 交易标的筛选与表达方式选择")
    sub = p.add_subparsers(dest="command")
    p_run = sub.add_parser("run", help="构建交易候选（读 Layer①/② + universe → 输出候选对象）")
    p_run.add_argument("--log-level", default="INFO")
    return p


def main() -> None:
    args = build_arg_parser().parse_args()
    command = getattr(args, "command", None)
    if command == "run" or command is None:
        cmd_run(args)


if __name__ == "__main__":
    main()
