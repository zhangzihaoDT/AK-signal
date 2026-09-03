"""
Layer ③ selection CLI — 交易标的筛选与表达方式选择

子命令：
  run        构建交易候选（读 Layer①/② + universe + 个股趋势 → 输出候选对象 JSON + HTML）
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.common.paths import (
    etf_signal_daily_dir, etf_signal_signals_dir, etf_signal_master_dir,
    sw_industry_confirmation_dir, selection_universe_path, outputs_dir,
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


def _load_stock_metrics_input(
    sel_date: str,
    use_exact: bool,
    requested_td: str | None,
) -> tuple[pd.DataFrame, str | None]:
    """加载个股趋势产物 stock_metrics_{trade_date}.parquet。

    精确模式：只认 selection_date 对应的文件；
    非精确模式：取最新文件（与 selection_date 的滞后在调用方记录）。
    均不联网、不降级到其他日期构造数据。
    """
    from src.trend_engine import inputs as trend_inputs

    if use_exact:
        df = trend_inputs.load_stock_metrics(requested_td or sel_date)
        return df, (requested_td or sel_date) if not df.empty else None
    td = trend_inputs.latest_stock_metrics_trade_date()
    if not td:
        return pd.DataFrame(), None
    return trend_inputs.load_stock_metrics(td), td


def _missing_trend_row(item: Any) -> dict[str, Any]:
    """构造一条 data_status=missing 的趋势行（局部降级占位）。"""
    return {
        "symbol": item.asset.symbol,
        "name": item.asset.name,
        "market": item.asset.market,
        "close": None,
        "score_trend": 0,
        "score": 0,
        "watch_level": "",
        "action": "",
        "risk_flags": "",
        "data_status": "missing",
        "source": "",
    }


def _ensure_stock_trend_df(
    stock_items: list[Any],
    metrics_df: pd.DataFrame,
    sel_date: str,
) -> pd.DataFrame:
    """保证每个股票资产都有一行趋势数据；缺失的资产补 data_status=missing 占位。"""
    if stock_items and metrics_df.empty:
        return pd.DataFrame([_missing_trend_row(it) for it in stock_items])

    rows: list[dict[str, Any]] = []
    for it in stock_items:
        symbol = it.asset.symbol
        if not metrics_df.empty and "symbol" in metrics_df.columns:
            m = metrics_df[metrics_df["symbol"].astype(str) == symbol]
            if not m.empty:
                rows.append(m.iloc[0].to_dict())
                continue
        rows.append(_missing_trend_row(it))
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for c in ("symbol", "name", "market", "data_status"):
        if c not in df.columns:
            df[c] = ""
    df["symbol"] = df["symbol"].astype(str)
    df["data_status"] = df["data_status"].astype(str).str.strip().fillna("missing")
    return df.reset_index(drop=True)


def _load_lane_input(sel_date: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    """加载 three_lane_{sel_date}.parquet（ETF State Fusion，Layer③ Phase 0 输入）。

    对齐语义（canonical，锁定）：exact `three_lane_{trade_date}` 存在且内部
    trade_date 与文件名一致 → 对齐消费（lane_lag_days=0）；exact 缺失或内部日期
    不一致 → lane-less（lane_trade_date=None / lane_lag_days=None），**禁止 fallback
    到前后日期**（避免静默拼错 trade_date）。
    """
    meta: dict[str, Any] = {
        "status": "lane_less",
        "trade_date": None,
        "lane_lag_days": None,
        "reason": "",
    }
    path = outputs_dir() / "etf_signal" / f"three_lane_{sel_date}.parquet"
    if not path.exists():
        meta["reason"] = "exact three_lane 文件缺失"
        return pd.DataFrame(), meta
    try:
        df = pd.read_parquet(path)
    except Exception as e:  # noqa: BLE001
        meta["reason"] = f"three_lane 读取失败: {e}"
        return pd.DataFrame(), meta
    if df.empty or "fund_code" not in df.columns:
        meta["reason"] = "three_lane 为空或缺 fund_code"
        return pd.DataFrame(), meta
    internal = None
    if "trade_date" in df.columns:
        try:
            internal = pd.Timestamp(df["trade_date"].dropna().max()).strftime("%Y%m%d")
        except Exception:  # noqa: BLE001
            internal = None
    if internal is None or internal != sel_date:
        meta["reason"] = f"内部 trade_date({internal}) 与目标({sel_date})不一致"
        return pd.DataFrame(), meta
    meta.update({"status": "aligned", "trade_date": sel_date, "lane_lag_days": 0})
    return df, meta


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

    # ── 输入：Layer ② Tier 确认（v0.9.1，配置了 tiers 的主题统一 Gate）──
    tier_confirmation_df = pd.DataFrame()
    try:
        from src.sw_industry_rps import tier_confirmation as _tc
        tier_td = requested_td if use_exact else (sw_td or "")
        if tier_td:
            tier_confirmation_df = _tc.load_tier_confirmation(tier_td)
        if tier_confirmation_df.empty and sw_td:
            tier_confirmation_df = _tc.load_tier_confirmation(sw_td)
        if not tier_confirmation_df.empty:
            logger.info("Layer② tier confirmation: %d tiers (trade_date=%s)",
                        len(tier_confirmation_df), tier_td)
        else:
            logger.warning("Layer② tier confirmation 为空 — 配置了 tiers 的主题确认回退行业 Gate")
    except Exception as e:
        logger.warning("tier confirmation load failed (fallback to industry gate): %s", e)

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

    # ── 输入：Lane 事实（three_lane 单一 join，ETF State Fusion） ──
    # exact three_lane_{sel_date} 对齐消费；缺失/日期不符 → lane-less（禁 fallback 前后日期）
    lane_df, lane_meta = _load_lane_input(sel_date)
    if lane_df.empty:
        logger.warning("Lane input: %s（lane-less，ETF 无三 Lane 上下文）", lane_meta.get("reason"))
    else:
        logger.info("Lane input: %d ETFs (trade_date=%s, status=aligned)",
                    len(lane_df), lane_meta.get("trade_date"))

    # ── 输入：个股趋势（调用 Trend Engine 计算，不读独立报告） ─────
    universe_items = load_universe_items(selection_universe_path())
    logger.info("universe: %d assets", len(universe_items))

    # ── 配置健康检查：未注册主题 / 跨主题资产 ─────────────────────
    # 未注册 theme = 配置关系不完整（资产不进入任何候选）。默认告警继续并标记 degraded；
    # --strict 下阻止发布，避免未注册主题静默进入正式报告。
    unregistered = detect_unregistered_themes(selection_universe_path())
    cross_assets = cross_theme_assets(selection_universe_path())
    config_issues: dict[str, Any] = {}
    if unregistered:
        config_issues["unregistered_themes"] = unregistered
        logger.warning("unregistered themes (asset pool has no confirmation/selection): %s", unregistered)
    if cross_assets:
        config_issues["cross_theme_assets"] = cross_assets
        logger.info("cross-theme assets (primary attribution = first bucket order): %s", list(cross_assets))
    if unregistered and getattr(args, "strict", False):
        logger.error("strict mode: %d unregistered theme(s) in selection_universe.yaml — aborting publish", len(unregistered))
        return

    trend_df = pd.DataFrame()
    _sel_t0 = time.monotonic()
    if universe_items:
        from src.trend_engine import inputs as trend_inputs
        stock_items = trend_inputs.stock_items(universe_items)
        etf_items = [it for it in universe_items if not trend_inputs.is_stock_item(it)]

        # ── 输入：个股趋势（读取预计算产物 stock_metrics，不联网） ──
        # Layer③ 只消费已落盘的 stock_metrics_{trade_date}.parquet；
        # 缺少输入时不自动重试，按 missing 局部降级（不阻塞整体）。
        allow_online = getattr(args, "allow_online_fetch", False)
        online_fetches = 0
        stock_metrics_df, metrics_td = _load_stock_metrics_input(sel_date, use_exact, requested_td)
        if stock_metrics_df.empty:
            if allow_online:
                logger.warning("stock trend inputs missing for %s — building online (--allow-online-fetch)", sel_date)
                trend_inputs.build_stock_metrics(
                    stock_items, trade_date=sel_date, offline=False, log_level=args.log_level)
                stock_metrics_df, metrics_td = trend_inputs.load_stock_metrics(sel_date), sel_date
                online_fetches = 1
            else:
                logger.warning("stock trend inputs missing for %s — 全部个股标记 unavailable（run `make stock-metrics` 或 `--allow-online-fetch`）",
                               sel_date)
        if metrics_td and metrics_td != sel_date:
            lag = _business_days_between(metrics_td, sel_date)
            logger.warning("stock trend input 滞后: input_trade_date=%s selection_date=%s lag_days=%s",
                           metrics_td, sel_date, lag)
        trend_df = _ensure_stock_trend_df(stock_items, stock_metrics_df, sel_date)

        # ── 覆盖率报告：ETF 复用 Layer① / 个股趋势输入 / 缺失降级 ──
        etf_codes = set(rotation_df["fund_code"].astype(str)) if not rotation_df.empty else set()
        etf_covered = sum(1 for it in etf_items if it.asset.symbol in etf_codes)
        loaded_syms = set(trend_df.loc[trend_df["data_status"] != "missing", "symbol"].astype(str))
        stock_loaded = sum(1 for it in stock_items if it.asset.symbol in loaded_syms)
        degraded_assets = sorted(
            [it.asset.symbol for it in stock_items if it.asset.symbol not in loaded_syms]
            + [it.asset.symbol for it in etf_items if it.asset.symbol not in etf_codes]
        )
        coverage = {
            "etf_reused": f"{etf_covered}/{len(etf_items)}",
            "stock_inputs_loaded": f"{stock_loaded}/{len(stock_items)}",
            "selection_coverage": f"{etf_covered + stock_loaded}/{len(universe_items)}",
            "selection_coverage_pct": round((etf_covered + stock_loaded) / len(universe_items) * 100, 1)
            if universe_items else 0.0,
            "degraded_assets": degraded_assets,
            "stock_input_trade_date": metrics_td,
            "online_fetches": online_fetches,
        }
        elapsed = time.monotonic() - _sel_t0
        logger.info("Layer① ETF metrics reused: %s", coverage["etf_reused"])
        logger.info("Stock trend inputs loaded: %s", coverage["stock_inputs_loaded"])
        logger.info("Selection coverage: %s", coverage["selection_coverage"])
        logger.info("Online fetches: %d", online_fetches)
        if degraded_assets:
            logger.warning("degraded assets (%d): %s", len(degraded_assets), ", ".join(degraded_assets))
        logger.info("Selection completed in %.1fs", elapsed)
    else:
        stock_items = []
        etf_items = []
        coverage = {
            "etf_reused": "0/0", "stock_inputs_loaded": "0/0",
            "selection_coverage": "0/0", "selection_coverage_pct": 0.0,
            "degraded_assets": [], "stock_input_trade_date": None, "online_fetches": 0,
        }

    # ── 构建候选对象（Selection Engine） ─────────────────────────────
    # v0.11 Phase 1：Layer③ 每日发布输出收敛 ETF-only（include_stocks=False）；
    # engine 默认 include_stocks=True 供 research replay/parity 重放历史个股。
    candidates = selection.build_candidates(
        rotation_df=rotation_df,
        account_df=account_df,
        master_df=master_df,
        confirmation_df=confirmation_df,
        universe_items=universe_items,
        trend_df=trend_df,
        tier_confirmation_df=tier_confirmation_df,
        trade_date=sel_date,
        lane_df=lane_df,
        include_stocks=False,
    )

    # ── 推荐结构（Recommendation Builder：纯排版，不制造新事实） ────
    from . import recommendation as rec_builder
    recommendation = rec_builder.build_recommendation(candidates)

    # ── 输出：结构化推荐 JSON + HTML 可视化（按 selection_date 命名） ─
    meta: dict[str, Any] = {
        "alignment": alignment,
        "lane": lane_meta,
        "scope": "etf_only",   # v0.11 Phase 1：Layer③ 每日发布输出收敛 ETF-only（个股保留在 Layer② 作确认输入）
        "layers": {
            "etf": {"trade_date": etf_td, "data_status": _layer_status(rotation_df)},
            "account_candidates": {"trade_date": ac_td, "data_status": _layer_status(account_df)},
            "sw_industry": {"trade_date": sw_td, "data_status": _layer_status(confirmation_df)},
        },
    }
    if config_issues:
        meta["config_issues"] = config_issues
        meta["degraded"] = "config_issues"
    if coverage:
        meta["coverage"] = coverage
        if coverage.get("degraded_assets"):
            meta["degraded"] = meta.get("degraded", "coverage") or "coverage"
    out_dir = outputs_dir() / "selection"
    json_path = selection.save_candidates_json(recommendation, out_dir, sel_date, meta=meta)
    # v2 跨日对比（05 风险与变化）：按 date 取上一份 Layer③ JSON（fail-soft，缺/损坏不阻塞）
    prev = None
    try:
        from .report_changes import load_previous, resolve_previous_path
        prev_path = resolve_previous_path(out_dir, sel_date)
        if prev_path:
            prev = load_previous(prev_path)
            logger.info("05 跨日对比：previous=%s", prev_path.name)
    except Exception as exc:
        logger.warning("previous selection load skipped: %s", exc)
    html_path = sel_report.render_selection_html(recommendation, out_dir, sel_date, meta=meta, prev=prev)

    # 控制台摘要
    for bucket in recommendation.get("buckets", []):
        logger.info("[bucket] %s（%s）| 确认主题=%d/%d",
                    bucket.get("bucket_label", ""), bucket.get("objective", ""),
                    bucket.get("n_confirmed", 0), bucket.get("n_themes", 0))
        for sub in bucket.get("themes", []):
            rec = sub.get("recommendation", {})
            n_core = len(rec.get("etf", []))
            n_cand = len(rec.get("stocks", []))
            wl = sub.get("watchlist", {})
            n_wl = len(wl.get("etf", [])) + len(wl.get("stocks", []))
            logger.info("  [%s] %s | %s | 推荐ETF=%d 推荐个股=%d 观察池=%d",
                        sub.get("theme", ""), sub.get("theme_label", ""),
                        sub.get("today", {}).get("expression_label", ""), n_core, n_cand, n_wl)
    logger.info("recommended_actions: %d", len(recommendation.get("recommended_actions", [])))

    logger.info("candidates json: %s", json_path)
    logger.info("candidates html: %s", html_path)


def cmd_stock_metrics(args: argparse.Namespace) -> None:
    """构建个股趋势指标产物 outputs/stock_metrics/stock_metrics_{date}.parquet。

    属于 Observation 层（Market Observation → Stock / Tier）：
    Layer② Tier 确认 与 Layer③ Selection 共同消费此产物。
    默认离线（读缓存，确定性）；--allow-online-fetch 用于手工补数。
    """
    from src.trend_engine import inputs as trend_inputs

    logger = build_logger(args.log_level)
    logger.info("=" * 60)
    logger.info("STOCK METRICS: 个股趋势指标构建（Observation）")
    logger.info("=" * 60)

    requested_td = getattr(args, "date", "") or ""
    trade_date = requested_td
    if not trade_date:
        # 默认对齐最新 Layer① rotation 的 trade_date
        rotation_df, rot_td = _load_latest_signal(etf_signal_daily_dir(), "rotation_*.parquet", "trade_date")
        trade_date = rot_td or trend_inputs.latest_stock_metrics_trade_date() or date.today().strftime("%Y%m%d")
    logger.info("target trade_date: %s", trade_date)

    universe_items = load_universe_items(selection_universe_path())
    allow_online = getattr(args, "allow_online_fetch", False)
    df = trend_inputs.build_stock_metrics(
        universe_items,
        trade_date=trade_date,
        offline=not allow_online,
        log_level=args.log_level,
    )
    # Observation 新鲜度校验：stale/missing 记录到 run 警告，由 final-check 呈现
    counts = df["data_status"].value_counts().to_dict() if not df.empty else {}
    if counts.get("stale", 0) or counts.get("missing", 0):
        from src.common import warnings as run_warnings
        run_warnings.record(
            "stock_inputs",
            f"个股 Observation 新鲜度：{counts.get('stale', 0)} stale / {counts.get('missing', 0)} missing"
            f"（{allow_online and 'online' or 'offline'} 构建）",
        )
        run_warnings.save_warnings(trade_date)
        logger.warning("stock observation freshness: %d stale / %d missing", counts.get("stale", 0), counts.get("missing", 0))


def cmd_universe(args: argparse.Namespace) -> None:
    """查看分层资产池（bucket → theme → tier → assets）。"""
    universe_items = load_universe_items(selection_universe_path())
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
    p_run = sub.add_parser("run", help="构建交易候选（读 Layer①/② + 预计算个股趋势 → 输出候选对象）")
    p_run.add_argument("--offline", action="store_true",
                       help="离线模式（默认即为离线；保留兼容）")
    p_run.add_argument("--allow-online-fetch", action="store_true",
                       help="个股趋势输入缺失时允许在线补数（手工模式；默认禁止联网）")
    p_run.add_argument("--date", default="", help="目标 trade_date YYYYMMDD（默认各层最新 trade_date 并对齐）")
    p_run.add_argument("--strict", action="store_true",
                       help="严格模式：asset pool 存在未注册 theme 时中止发布（默认告警+标记 degraded 继续）")
    p_run.add_argument("--log-level", default="INFO")
    p_inputs = sub.add_parser("stock-metrics", help="构建个股趋势指标产物 stock_metrics/stock_metrics_{date}.parquet（Observation 层）")
    p_inputs.add_argument("--date", default="", help="目标 trade_date YYYYMMDD（默认最新 Layer① rotation 的 trade_date）")
    p_inputs.add_argument("--allow-online-fetch", action="store_true",
                          help="允许在线补数（默认仅用缓存）")
    p_inputs.add_argument("--log-level", default="INFO")
    p_univ = sub.add_parser("universe", help="查看分层资产池（bucket → theme → tier）")
    p_univ.add_argument("--log-level", default="INFO")
    return p


def main() -> None:
    args = build_arg_parser().parse_args()
    command = getattr(args, "command", None)
    if command == "run":
        cmd_run(args)
    elif command == "stock-metrics":
        cmd_stock_metrics(args)
    elif command == "universe":
        cmd_universe(args)
    else:
        cmd_run(args)


if __name__ == "__main__":
    main()
