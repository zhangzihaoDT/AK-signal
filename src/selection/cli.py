"""
Layer ③ selection CLI — 交易标的筛选与表达方式选择

子命令：
  run        构建交易候选（读 Layer①/② + universe + 个股趋势 → 输出候选对象 JSON + HTML）
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.common.paths import (
    etf_signal_daily_dir, etf_signal_signals_dir, etf_signal_master_dir,
    sw_industry_confirmation_dir, stock_universe_path, outputs_dir,
)
from .universe import load_universe_items, detect_unregistered_themes, cross_theme_assets
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


def _file_trade_date(path: Path, date_col: str) -> str | None:
    """从 parquet 元数据列读取 trade_date（YYYYMMDD）；读取失败返回 None。"""
    try:
        df = pd.read_parquet(path, columns=[date_col])
        v = df[date_col].dropna()
        if len(v):
            return pd.Timestamp(v.max()).strftime("%Y%m%d")
    except Exception:
        return None
    return None


def _scan_trade_dates(directory: Path, pattern: str, date_col: str) -> dict[str, Path]:
    """把目录下的产物按 trade_date 聚合成 {trade_date_str: 文件路径}。

    只要同层存在带元数据的文件，就忽略旧的无元数据文件（run_date 命名，
    迁移期会与 trade_date 命名混排）；全部无元数据时回退为文件名日期。
    """
    out: dict[str, Path] = {}
    if not directory.exists():
        return out
    files = sorted(directory.glob(pattern))
    meta_dates: dict[str, Path] = {}
    legacy_dates: dict[str, Path] = {}
    for path in files:
        td = _file_trade_date(path, date_col)
        if td is not None:
            meta_dates[td] = path
        else:
            d = path.stem.rsplit("_", 1)[-1]
            if len(d) == 8 and d.isdigit():
                legacy_dates.setdefault(d, path)
    out = meta_dates if meta_dates else legacy_dates
    return out


def _load_latest_signal(
    directory: Path,
    pattern: str,
    date_col: str,
) -> tuple[pd.DataFrame, str | None]:
    """加载某层最新 trade_date 的产物（按元数据聚合，兼容迁移期旧文件）。"""
    scans = _scan_trade_dates(directory, pattern, date_col)
    if not scans:
        return pd.DataFrame(), None
    td = max(scans)
    try:
        return pd.read_parquet(scans[td]), td
    except Exception:
        return pd.DataFrame(), None


def _business_days_between(start: str, end: str) -> int:
    """两日期之间的交易日数（不含起始日，含结束日）。"""
    try:
        start_d = pd.Timestamp(start).strftime("%Y-%m-%d")
        end_d = pd.Timestamp(end).strftime("%Y-%m-%d")
        return int(np.busday_count(
            np.datetime64(start_d), np.datetime64(end_d),
            weekmask="1111100",  # 周一至周五
        ))
    except Exception:
        return 0


def _compute_alignment(etf_td: str | None, sw_td: str | None) -> dict[str, Any]:
    """基于两层的 trade_date 判定信号对齐状态。"""
    if not etf_td and not sw_td:
        return {"selection_date": "", "alignment_status": "no_data",
                "industry_lag_days": None}
    if not etf_td:
        return {"selection_date": sw_td or "", "alignment_status": "no_etf",
                "industry_lag_days": None}
    if not sw_td:
        return {"selection_date": etf_td, "alignment_status": "no_industry",
                "industry_lag_days": None}
    if etf_td == sw_td:
        return {"selection_date": etf_td, "alignment_status": "aligned",
                "industry_lag_days": 0}
    if sw_td < etf_td:
        return {"selection_date": etf_td, "alignment_status": "stale_industry",
                "industry_lag_days": _business_days_between(sw_td, etf_td)}
    return {"selection_date": sw_td, "alignment_status": "stale_etf",
            "industry_lag_days": _business_days_between(etf_td, sw_td)}


def _layer_status(df: pd.DataFrame, default: str = "unknown") -> str:
    """从产物 data_status 列收敛层证据等级。"""
    if df.empty or "data_status" not in df.columns:
        return default
    vals = df["data_status"].dropna().astype(str)
    if len(vals) == 0:
        return default
    if (vals == "provisional").any():
        return "provisional"
    if (vals == "confirmed").any():
        return "confirmed"
    return default


def _load_parquet_for_date(directory: Path, pattern: str, date_str: str) -> pd.DataFrame:
    """加载指定 trade_date 的 parquet；文件不存在则返回空（不降级到其他日期）。"""
    path = directory / pattern.replace("*", date_str)
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(path)
    except Exception:
        return pd.DataFrame()


def cmd_run(args: argparse.Namespace) -> None:
    logger = build_logger(args.log_level)
    logger.info("=" * 60)
    logger.info("LAYER ③: 交易标的筛选与表达方式选择")
    logger.info("=" * 60)

    # --date 语义 = 目标 trade_date（Layer1/Layer2 统一按 trade_date 精确加载）
    requested_td = getattr(args, "date", "") or None
    use_exact = requested_td is not None
    logger.info("target trade_date: %s (exact=%s)", requested_td or "latest", use_exact)

    # ── 输入：Layer ① ETF 信号（按 trade_date 对齐） ────────────────
    if use_exact:
        # 精确模式：字面按 trade_date 文件名加载，缺文件即空、不降级（可复现）
        rotation_df = _load_parquet_for_date(etf_signal_daily_dir(), "rotation_*.parquet", requested_td)
        account_df = _load_parquet_for_date(etf_signal_signals_dir(), "account_candidates_*.parquet", requested_td)
        rot_td = requested_td if not rotation_df.empty else None
        ac_td = requested_td if not account_df.empty else None
    else:
        rotation_df, rot_td = _load_latest_signal(etf_signal_daily_dir(), "rotation_*.parquet", "trade_date")
        account_df, ac_td = _load_latest_signal(etf_signal_signals_dir(), "account_candidates_*.parquet", "trade_date")
    master_df = pd.DataFrame()
    master_path = etf_signal_master_dir() / "etf_master.parquet"
    if master_path.exists():
        try:
            master_df = pd.read_parquet(master_path)
        except Exception:
            master_df = pd.DataFrame()
    if rotation_df.empty:
        logger.error("no rotation data for trade_date %s — run etf calculate first",
                     requested_td or (rot_td or "latest"))
        return
    logger.info("Layer① rotation: %d ETFs (trade_date=%s, status=%s)",
                len(rotation_df), rot_td, _layer_status(rotation_df))
    logger.info("Layer① account_candidates: %d rows (trade_date=%s, status=%s)",
                len(account_df), ac_td, _layer_status(account_df))

    # ── 输入：Layer ② 行业确认（按 trade_date 对齐） ───────────────
    if use_exact:
        confirmation_df = _load_parquet_for_date(sw_industry_confirmation_dir(), "confirmation_*.parquet", requested_td)
        sw_td = requested_td if not confirmation_df.empty else None
    else:
        confirmation_df, sw_td = _load_latest_signal(sw_industry_confirmation_dir(), "confirmation_*.parquet", "date")
    if not confirmation_df.empty:
        logger.info("Layer② confirmation: %d industries (trade_date=%s, status=%s)",
                    len(confirmation_df), sw_td, _layer_status(confirmation_df))
    else:
        logger.warning("Layer② confirmation 为空（trade_date=%s）— 方向门控将不通过", sw_td or "latest")

    # ── 对齐判定 ────────────────────────────────────────────────────
    if use_exact:
        # 精确模式：各层均按 requested_td 字面加载，etf_td 固定为 requested
        etf_td = requested_td if not rotation_df.empty else None
        alignment = _compute_alignment(etf_td, sw_td)
        alignment["selection_date"] = requested_td
    else:
        layer_tds = [t for t in (rot_td, ac_td) if t]
        etf_td = max(layer_tds) if layer_tds else None
        alignment = _compute_alignment(etf_td, sw_td)
    sel_date = alignment["selection_date"] or requested_td or date.today().strftime("%Y%m%d")
    logger.info("alignment: %s | selection_date=%s | industry_lag_days=%s",
                alignment.get("alignment_status"), sel_date, alignment.get("industry_lag_days"))

    # ── 输入：个股趋势（调用 Trend Engine 计算，不读独立报告） ─────
    universe_items = load_universe_items(stock_universe_path())
    logger.info("universe: %d assets", len(universe_items))

    # ── 配置健康检查：未注册主题 / 跨主题资产 ─────────────────────
    # 未注册 theme = 配置关系不完整（资产不进入任何候选）。默认告警继续并标记 degraded；
    # --strict 下阻止发布，避免未注册主题静默进入正式报告。
    unregistered = detect_unregistered_themes(stock_universe_path())
    cross_assets = cross_theme_assets(stock_universe_path())
    config_issues: dict[str, Any] = {}
    if unregistered:
        config_issues["unregistered_themes"] = unregistered
        logger.warning("unregistered themes (asset pool has no confirmation/selection): %s", unregistered)
    if cross_assets:
        config_issues["cross_theme_assets"] = cross_assets
        logger.info("cross-theme assets (primary attribution = first bucket order): %s", list(cross_assets))
    if unregistered and getattr(args, "strict", False):
        logger.error("strict mode: %d unregistered theme(s) in stock_universe.yaml — aborting publish", len(unregistered))
        return

    trend_df = pd.DataFrame()
    if universe_items:
        from src.trend_engine.engine import compute_trends
        trend_df = compute_trends(
            universe_items,
            offline=getattr(args, "offline", False),
            log_level=args.log_level,
        )
        logger.info("trend engine: %d assets analyzed", len(trend_df))

    # ── 构建候选对象 ───────────────────────────────────────────────
    candidates = selection.build_candidates(
        rotation_df=rotation_df,
        account_df=account_df,
        master_df=master_df,
        confirmation_df=confirmation_df,
        universe_items=universe_items,
        trend_df=trend_df,
    )

    # ── 输出：结构化候选 JSON + HTML 可视化（按 selection_date 命名） ─
    meta: dict[str, Any] = {
        "alignment": alignment,
        "layers": {
            "etf": {"trade_date": etf_td, "data_status": _layer_status(rotation_df)},
            "account_candidates": {"trade_date": ac_td, "data_status": _layer_status(account_df)},
            "sw_industry": {"trade_date": sw_td, "data_status": _layer_status(confirmation_df)},
        },
    }
    if config_issues:
        meta["config_issues"] = config_issues
        meta["degraded"] = "config_issues"
    out_dir = outputs_dir() / "selection"
    json_path = selection.save_candidates_json(candidates, out_dir, sel_date, meta=meta)
    html_path = sel_report.render_selection_html(candidates, out_dir, sel_date, meta=meta)

    # 控制台摘要
    for bucket in candidates.get("buckets", []):
        logger.info("[bucket] %s（%s）| 确认主题=%d/%d",
                    bucket.get("bucket_label", ""), bucket.get("objective", ""),
                    bucket.get("n_confirmed", 0), bucket.get("n_themes", 0))
        for sub in bucket.get("themes", []):
            n_core = len(sub.get("core_etf", []))
            n_sub = len(sub.get("sub_industry_etf", []))
            n_cand = len(sub.get("stock_candidates", []))
            wl = sub.get("stock_watchlist", {})
            n_wl = sum(len(wl.get(t, [])) for t in ("leaders", "high_beta", "equipment"))
            logger.info("  [%s] %s | %s | 核心ETF=%d 细分ETF=%d 个股候选=%d 观察池=%d",
                        sub.get("theme", ""), sub.get("theme_label", ""),
                        sub.get("expression_label", ""), n_core, n_sub, n_cand, n_wl)
    logger.info("recommended_actions: %d", len(candidates.get("recommended_actions", [])))

    logger.info("candidates json: %s", json_path)
    logger.info("candidates html: %s", html_path)


def cmd_universe(args: argparse.Namespace) -> None:
    """查看分层资产池（bucket → theme → tier → assets）。"""
    universe_items = load_universe_items(stock_universe_path())
    logger = build_logger(args.log_level)
    by_bucket: dict[str, list] = {}
    for item in universe_items:
        by_bucket.setdefault(item.bucket, []).append(item)
    for bucket_key in sorted(by_bucket, key=lambda k: _bucket_order(k)):
        items = by_bucket[bucket_key]
        bucket_label = items[0].bucket_label
        logger.info("【%s / %s】 %d assets", bucket_key, bucket_label, len(items))
        by_theme: dict[str, list] = {}
        for item in items:
            by_theme.setdefault(item.theme, []).append(item)
        for theme_key in by_theme:
            theme_items = by_theme[theme_key]
            theme_label = theme_items[0].theme_label
            logger.info("  └─ %s / %s（%d）", theme_key, theme_label, len(theme_items))
            by_tier: dict[str, list] = {}
            for item in theme_items:
                by_tier.setdefault(item.tier, []).append(item)
            for tier_key in by_tier:
                names = "、".join(f"{i.asset.name}({i.asset.symbol})" for i in by_tier[tier_key])
                logger.info("      └─ %s: %s", tier_key, names)


def _bucket_order(bucket_key: str) -> int:
    return {"core": 0, "quality": 1, "tactical": 2}.get(bucket_key, 9)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Layer ③ 交易标的筛选与表达方式选择")
    sub = p.add_subparsers(dest="command")
    p_run = sub.add_parser("run", help="构建交易候选（读 Layer①/② + 调 Trend Engine → 输出候选对象）")
    p_run.add_argument("--offline", action="store_true", help="仅用缓存行情，不联网")
    p_run.add_argument("--date", default="", help="目标 trade_date YYYYMMDD（默认各层最新 trade_date 并对齐）")
    p_run.add_argument("--strict", action="store_true",
                       help="严格模式：asset pool 存在未注册 theme 时中止发布（默认告警+标记 degraded 继续）")
    p_run.add_argument("--log-level", default="INFO")
    p_univ = sub.add_parser("universe", help="查看分层资产池（bucket → theme → tier）")
    p_univ.add_argument("--log-level", default="INFO")
    return p


def main() -> None:
    args = build_arg_parser().parse_args()
    command = getattr(args, "command", None)
    if command == "run":
        cmd_run(args)
    elif command == "universe":
        cmd_universe(args)
    else:
        cmd_run(args)


if __name__ == "__main__":
    main()
