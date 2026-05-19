from __future__ import annotations

import argparse
import logging
from datetime import date
from pathlib import Path

import pandas as pd

import fetch_data
import indicators
import portfolio
import report
import scoring
import watchlist


def build_logger(level: str) -> logging.Logger:
    logger = logging.getLogger("a_stock_monitor")
    if logger.handlers:
        return logger
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_stock_pool(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path, dtype=str).fillna("")
    return df


def calc_change(curr_score: int, prev_score: int | None, prev_available: bool) -> str:
    if not prev_available:
        return "无变化"

    if prev_score is None:
        bucket = scoring.trend_bucket(curr_score)
        if bucket == "strong":
            return "新增强趋势"
        if bucket == "observe":
            return "新增观察"
        return "无变化"

    prev_bucket = scoring.trend_bucket(prev_score)
    curr_bucket = scoring.trend_bucket(curr_score)

    if prev_bucket == curr_bucket:
        if curr_bucket == "strong":
            return "维持强趋势"
        if curr_bucket == "observe":
            return "维持观察"
        return "无变化"

    if curr_bucket == "strong":
        return "新增强趋势"
    if prev_bucket == "strong":
        return "降级"
    if prev_bucket == "observe" and curr_bucket == "weak":
        return "退出观察"
    if prev_bucket == "weak" and curr_bucket == "observe":
        return "新增观察"
    return "无变化"


def calc_watch_level(
    score_value: int,
    relative_strength_20d: float | None,
    ma20: float | None,
    ma60: float | None,
    volume_ratio: float | None,
) -> str:
    if score_value < 30:
        return "C"
    if score_value < 70:
        return "B"

    rs = relative_strength_20d
    if rs is None:
        return "B"

    cond_ma = ma20 is not None and ma60 is not None and ma20 > ma60
    cond_vol = volume_ratio is not None and volume_ratio > 1.2
    if rs > 0.15 and cond_ma and cond_vol:
        return "S"
    if rs > 0:
        return "A"
    return "B"


def calc_action(
    score_value: int,
    watch_level: str,
    relative_strength_20d: float | None,
    volume_ratio: float | None,
    price_near_ma20: bool | None,
    drawdown_from_high: float | None,
    change: str,
) -> str:
    if drawdown_from_high is not None and drawdown_from_high >= 0.15:
        return "风险警戒"
    if score_value < 30 or watch_level == "C":
        return "剔除观察"

    rs = relative_strength_20d
    vr = volume_ratio
    if watch_level in ["S", "A"]:
        if vr is not None and vr > 1.5:
            return "继续跟踪"
        if change == "新增强趋势":
            return "重点观察"
        if rs is not None and rs > 0:
            return "重点观察"
        return "继续跟踪"

    if watch_level == "B":
        if price_near_ma20:
            return "等待突破"
        return "观察等待"

    return "无变化"


def run(args: argparse.Namespace) -> tuple[Path, Path]:
    root = project_root()
    logger = build_logger(args.log_level)
    report_date = date.today()

    pool_path = root / "config" / "stock_pool.csv"
    pool = load_stock_pool(pool_path)

    raw_dir = root / "data" / "raw"
    processed_dir = root / "data" / "processed"
    reports_dir = root / "data" / "reports"
    processed_dir.mkdir(parents=True, exist_ok=True)

    end_date = args.end_date or "22220101"
    fetch_cfg = fetch_data.FetchConfig(start_date=args.start_date, end_date=end_date, adjust=args.adjust)

    bench_raw = fetch_data.load_or_fetch_hs300(raw_dir=raw_dir, cfg=fetch_cfg, logger=logger, force=args.force)
    bench = bench_raw[["date", "close"]].rename(columns={"close": "bench_close"}).copy() if not bench_raw.empty else pd.DataFrame()
    if not bench.empty:
        bench["bench_return_20d"] = bench["bench_close"].pct_change(20)

    prev_summary = report.load_previous_summary(reports_dir, report_date)
    prev_available = not prev_summary.empty
    prev_score_by_symbol: dict[str, int] = {}
    if not prev_summary.empty and "symbol" in prev_summary.columns:
        for _, r in prev_summary.iterrows():
            sym = str(r.get("symbol", "")).strip()
            if not sym or sym.upper() == "TBD":
                continue
            try:
                prev_score_by_symbol[sym] = int(float(r.get("score", 0)))
            except Exception:
                continue

    rows: list[dict[str, object]] = []
    charts: dict[str, object] = {}

    for _, r in pool.iterrows():
        name = r.get("name", "").strip()
        symbol = r.get("symbol", "").strip()
        note = r.get("note", "").strip()

        # stock_pool.csv 中对“长鑫存储”等暂无法确认的标的，使用 TBD 作为占位符（待确认代码或替代标的）
        if symbol.upper() == "TBD" or not symbol:
            logger.warning("%s: %s", name, "待确认代码或替代标的")
            rows.append(
                {
                    "name": name,
                    "symbol": symbol,
                    "score": 0,
                    "label": "待确认",
                    "watch_level": "",
                    "action": "无变化",
                    "change": "无变化",
                    "relative_strength_20d": None,
                    "reason": "待确认代码或替代标的",
                    "note": "待确认代码或替代标的",
                }
            )
            continue

        df_raw = fetch_data.load_or_fetch(symbol, raw_dir=raw_dir, cfg=fetch_cfg, logger=logger, force=args.force)
        if df_raw.empty:
            rows.append(
                {
                    "name": name,
                    "symbol": symbol,
                    "score": 0,
                    "label": "无数据",
                    "watch_level": "",
                    "action": "无变化",
                    "change": "无变化",
                    "relative_strength_20d": None,
                    "reason": "无数据",
                    "note": note,
                }
            )
            continue

        df_ind = indicators.add_indicators(df_raw)
        if not bench.empty:
            df_ind = pd.merge_asof(
                df_ind.sort_values("date"),
                bench[["date", "bench_return_20d"]].sort_values("date"),
                on="date",
                direction="backward",
            )
            df_ind["relative_strength_20d"] = df_ind["return_20d"] - df_ind["bench_return_20d"]
        else:
            df_ind["relative_strength_20d"] = pd.NA

        processed_path = processed_dir / f"{symbol}.csv"
        df_ind.to_csv(processed_path, index=False, encoding="utf-8")

        score_value, _details = scoring.score_latest_row(df_ind)
        label = scoring.score_trend_label(score_value)
        change = calc_change(score_value, prev_score_by_symbol.get(symbol), prev_available)

        latest_row = df_ind.iloc[-1]
        latest_date = latest_row["date"]
        rs20_raw = latest_row.get("relative_strength_20d")
        rs20 = float(rs20_raw) if pd.notna(rs20_raw) else None
        ma20_raw = latest_row.get("ma20")
        ma60_raw = latest_row.get("ma60")
        ma20 = float(ma20_raw) if pd.notna(ma20_raw) else None
        ma60 = float(ma60_raw) if pd.notna(ma60_raw) else None
        vr_raw = latest_row.get("volume_ratio")
        volume_ratio = float(vr_raw) if pd.notna(vr_raw) else None
        pnm_raw = latest_row.get("price_near_ma20")
        price_near_ma20 = bool(pnm_raw) if pd.notna(pnm_raw) else None
        dd_raw = latest_row.get("drawdown_from_high")
        drawdown_from_high = float(dd_raw) if pd.notna(dd_raw) else None

        watch_level_value = calc_watch_level(score_value, rs20, ma20, ma60, volume_ratio)
        action_value = calc_action(
            score_value,
            watch_level_value,
            rs20,
            volume_ratio,
            price_near_ma20,
            drawdown_from_high,
            change,
        )
        rows.append(
            {
                "name": name,
                "symbol": symbol,
                "date": latest_date.date() if hasattr(latest_date, "date") else latest_date,
                "close": float(df_ind.iloc[-1]["close"]),
                "score": score_value,
                "label": label,
                "watch_level": watch_level_value,
                "action": action_value,
                "change": change,
                "relative_strength_20d": rs20,
                "reason": _details.get("reason", ""),
                "note": note,
            }
        )

        df_plot = df_ind.tail(args.plot_last_n).copy()
        charts[f"{name}({symbol})"] = report.build_price_chart(df_plot, title=f"{name}({symbol}) - 趋势评分 {score_value} / 100")

    summary = pd.DataFrame(rows)
    if not summary.empty and "score" in summary.columns:
        if "watch_level" in summary.columns:
            rank = {"S": 0, "A": 1, "B": 2, "C": 3}
            summary["_wl_rank"] = summary["watch_level"].map(rank).fillna(9)
            summary = summary.sort_values(["_wl_rank", "score", "symbol"], ascending=[True, False, True]).drop(columns=["_wl_rank"])
        else:
            summary = summary.sort_values(["score", "symbol"], ascending=[False, True])

    portfolio_summary = portfolio.build_portfolio_summary(summary)
    portfolio_json = portfolio.write_portfolio_summary(report_date, portfolio_summary, out_dir=reports_dir)
    logger.info("portfolio summary saved: %s", portfolio_json)

    watchlist_path = root / "data" / "watchlist.csv"
    wl_df = watchlist.update_watchlist(report_date, summary, reports_dir=reports_dir, watchlist_path=watchlist_path)
    logger.info("watchlist saved: %s (%d rows)", watchlist_path, len(wl_df))

    csv_path, html_path = report.write_daily_report(report_date, summary, charts, out_dir=reports_dir, portfolio_summary=portfolio_summary)
    logger.info("report saved: %s", html_path)
    return csv_path, html_path


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="A股技术趋势监控（极简版）")
    p.add_argument("--start-date", default="20180101", help="起始日期 YYYYMMDD")
    p.add_argument("--end-date", default="", help="结束日期 YYYYMMDD（默认到最新）")
    p.add_argument("--adjust", default="qfq", choices=["", "qfq", "hfq"], help="复权方式")
    p.add_argument("--force", action="store_true", help="忽略缓存重新拉取")
    p.add_argument("--plot-last-n", type=int, default=180, help="图表展示最近 N 个交易日")
    p.add_argument("--log-level", default="INFO", help="日志级别")
    return p


def main() -> None:
    args = build_arg_parser().parse_args()
    run(args)


if __name__ == "__main__":
    main()
