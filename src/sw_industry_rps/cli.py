from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import data_source, storage, metrics, regimes, validator, report


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def build_logger(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("sw_industry_rps")
    if logger.handlers:
        return logger
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    return logger


def load_config() -> dict:
    import yaml
    cfg_path = project_root() / "config" / "sw_industry_rps.yaml"
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    return {}


def cmd_bootstrap(args: argparse.Namespace) -> None:
    logger = build_logger(args.log_level)
    root = project_root()
    raw_dir = root / "data" / "raw" / "sw_industry"
    cfg = load_config()

    started_at = datetime.now(timezone.utc)

    logger.info("fetching industry master list...")
    master = data_source.fetch_industry_master()
    storage.save_master(master, raw_dir)
    logger.info("saved %d industries to master", len(master))

    expected = len(master)
    max_industries = cfg.get("bootstrap", {}).get("max_industries")
    min_days = cfg.get("bootstrap", {}).get("min_days", 250)
    start_date = cfg.get("bootstrap", {}).get("start_date", "20200101")
    codes = master["industry_code"].tolist()
    if max_industries:
        codes = codes[:max_industries]

    logger.info("universe: %d industries (config max_industries=%s)", len(codes), max_industries)

    name_map = dict(zip(master["industry_code"], master["industry_name"]))

    success = 0
    skipped_existing = 0
    failed: list[dict] = []
    total_rows = 0

    for i, code in enumerate(codes):
        existing = storage.load_industry_raw(raw_dir, code)
        if not existing.empty and len(existing) >= min_days:
            skipped_existing += 1
            total_rows += len(existing)
            logger.info("[%d/%d] %s (%s) — skipped_existing (%d rows)",
                        i + 1, len(codes), code, name_map.get(code, ""), len(existing))
            continue

        logger.info("[%d/%d] fetching %s (%s)...", i + 1, len(codes), code, name_map.get(code, ""))
        t0 = time.monotonic()
        try:
            df = data_source.fetch_industry_hist(
                code,
                start_date=start_date,
                max_retries=3,
                base_delay=2.0,
                max_delay=30.0,
            )
            elapsed = time.monotonic() - t0
            if not df.empty:
                df["industry_name"] = name_map.get(code, "")
                storage.save_industry_raw(df, raw_dir, code)
                success += 1
                total_rows += len(df)
                dr = f"{df['trade_date'].min().date()} -> {df['trade_date'].max().date()}"
                logger.info("  ✓ %s: %d rows, %s (%.1fs)", code, len(df), dr, elapsed)
            else:
                failed.append({"code": code, "name": name_map.get(code, ""), "error": "empty response", "elapsed": round(elapsed, 1)})
                logger.warning("  ✗ %s: empty response (%.1fs)", code, elapsed)
        except Exception as e:
            elapsed = time.monotonic() - t0
            failed.append({"code": code, "name": name_map.get(code, ""), "error": str(e), "elapsed": round(elapsed, 1)})
            logger.warning("  ✗ %s: %s (%.1fs)", code, e, elapsed)

        delay = random.uniform(2.0, 4.0)
        if (i + 1) % 10 == 0:
            logger.info("  checkpoint: %d success, %d skipped, %d failed, ~%.0f rows",
                        success, skipped_existing, len(failed), total_rows)
        time.sleep(delay)

    finished_at = datetime.now(timezone.utc)
    duration = (finished_at - started_at).total_seconds()

    failed_path = raw_dir / "bootstrap_failures.json"
    if failed:
        failed_path.write_text(
            json.dumps({"failed": failed, "total": len(codes), "timestamp": finished_at.isoformat()},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.warning("failures saved: %s (%d industries)", failed_path, len(failed))
    elif failed_path.exists():
        failed_path.unlink()

    logger.info("")
    logger.info("=" * 60)
    logger.info("BOOTSTRAP AUDIT")
    logger.info("  expected:         %d", expected)
    logger.info("  success:          %d", success)
    logger.info("  skipped_existing: %d", skipped_existing)
    logger.info("  failed:           %d", len(failed))
    logger.info("  total_rows:       %d", total_rows)
    logger.info("  started_at:       %s", started_at.strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("  finished_at:      %s", finished_at.strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("  duration:         %.1fs (%.1fmin)", duration, duration / 60)
    logger.info("=" * 60)
    logger.info("")


def cmd_update(args: argparse.Namespace) -> None:
    logger = build_logger(args.log_level)
    root = project_root()
    raw_dir = root / "data" / "raw" / "sw_industry"
    cfg = load_config()
    start_date = cfg.get("bootstrap", {}).get("start_date", "20200101")

    master = storage.load_master(raw_dir)
    if master.empty:
        logger.error("no master data found, run bootstrap first")
        return

    codes = master["industry_code"].tolist()
    today_str = datetime.now().strftime("%Y%m%d")

    success = 0
    for code in codes:
        cached = storage.load_industry_raw(raw_dir, code)
        last_date = cached["trade_date"].max() if not cached.empty else None

        if last_date is not None:
            inc_start = (last_date + pd.Timedelta(days=1)).strftime("%Y%m%d")
            if inc_start >= today_str:
                continue
        else:
            inc_start = start_date

        try:
            df_new = data_source.fetch_industry_hist(code, start_date=inc_start, end_date=today_str)
            if not df_new.empty:
                merged = storage.merge_incremental(cached, df_new)
                storage.save_industry_raw(merged, raw_dir, code)
                success += 1
                logger.info("updated %s: %d -> %d rows", code, len(cached), len(merged))
        except Exception as e:
            logger.warning("update failed for %s: %s", code, e)

        time.sleep(2)

    logger.info("update complete: %d/%d industries updated", success, len(codes))


def cmd_calculate(args: argparse.Namespace) -> None:
    logger = build_logger(args.log_level)
    root = project_root()
    raw_dir = root / "data" / "raw" / "sw_industry"
    processed_dir = root / "data" / "processed" / "sw_industry"
    cfg = load_config()
    windows = cfg.get("rps", {}).get("windows", [5, 10, 15])

    master = storage.load_master(raw_dir)
    if master.empty:
        logger.error("no master data, run bootstrap first")
        return

    all_hist: list[pd.DataFrame] = []
    for code in master["industry_code"].tolist():
        df = storage.load_industry_raw(raw_dir, code)
        if not df.empty:
            df["industry_code"] = code
            name_row = master[master["industry_code"] == code]
            if not name_row.empty:
                df["industry_name"] = name_row.iloc[0]["industry_name"]
            all_hist.append(df)

    if not all_hist:
        logger.error("no raw data found")
        return

    combined = pd.concat(all_hist, ignore_index=True)
    logger.info("computing metrics for %d rows across %d industries",
                len(combined), combined["industry_code"].nunique())

    result = metrics.compute_all_metrics(combined, windows=windows)
    prior_metrics = storage.load_metrics(processed_dir)

    if not prior_metrics.empty:
        prior_max_date = prior_metrics["trade_date"].max()
        new_dates = result[result["trade_date"] > prior_max_date]
        if not new_dates.empty:
            final = pd.concat([prior_metrics, new_dates], ignore_index=True)
            final = final.drop_duplicates(subset=["trade_date", "industry_code"], keep="last")
            final = final.sort_values(["trade_date", "industry_code"]).reset_index(drop=True)
        else:
            logger.info("no new data to calculate")
            final = prior_metrics
    else:
        final = result

    final = final.sort_values(["trade_date", "industry_code"]).reset_index(drop=True)

    logger.info("applying regime identification...")
    strong_threshold = cfg.get("regimes", {}).get("strong_threshold", 90)
    observe_threshold = cfg.get("regimes", {}).get("observe_threshold", 80)
    strong_streak_min = cfg.get("regimes", {}).get("strong_streak_min_days", 3)
    delta_acc = cfg.get("regimes", {}).get("delta_acceleration", 10)
    final = regimes.identify_all_regimes(
        final,
        strong_threshold=strong_threshold,
        observe_threshold=observe_threshold,
        strong_streak_min=strong_streak_min,
        delta_acceleration=delta_acc,
    )

    storage.save_metrics(final, processed_dir)
    logger.info("metrics saved: %d rows", len(final))

    latest_date = final["trade_date"].max()
    snapshot = final[final["trade_date"] == latest_date].copy()
    storage.save_snapshot(snapshot, processed_dir)

    rotation_days = cfg.get("report", {}).get("rotation_days", 20)
    pivot = build_rotation_matrix(final, rotation_days)
    storage.save_rotation_matrix(pivot, processed_dir)

    logger.info("calculate complete: latest date=%s", latest_date.date())


def build_rotation_matrix(metrics_df: pd.DataFrame, rotation_days: int = 20) -> pd.DataFrame:
    if metrics_df.empty:
        return pd.DataFrame()
    latest = metrics_df["trade_date"].max()
    cutoff = latest - pd.Timedelta(days=rotation_days * 2)
    recent = metrics_df[metrics_df["trade_date"] >= cutoff].copy()
    pivot = recent.pivot_table(
        index="industry_code", columns="trade_date", values="RPS15", aggfunc="first"
    )
    date_cols = sorted(pivot.columns, reverse=True)[:rotation_days]
    pivot = pivot[list(reversed(date_cols))]
    pivot = pivot.sort_values(pivot.columns[-1], ascending=False)
    return pivot.reset_index()


def cmd_report(args: argparse.Namespace) -> None:
    logger = build_logger(args.log_level)
    root = project_root()
    processed_dir = root / "data" / "processed" / "sw_industry"
    reports_dir = root / "outputs" / "sw_industry_rps"
    cfg = load_config()
    rotation_days = cfg.get("report", {}).get("rotation_days", 20)

    metrics_df = storage.load_metrics(processed_dir)
    if metrics_df.empty:
        logger.error("no metrics data, run calculate first")
        return

    latest_date = metrics_df["trade_date"].max()
    date_str = latest_date.strftime("%Y%m%d")
    snapshot = metrics_df[metrics_df["trade_date"] == latest_date].copy()

    snapshot_file = storage.save_snapshot(snapshot, processed_dir)

    master = storage.load_master(project_root() / "data" / "raw" / "sw_industry")
    expected_size = len(master) if not master.empty else None

    metrics_valid = validator.validate_metrics(metrics_df, expected_universe_size=expected_size)
    logger.info("metrics validation: status=%s, issues=%d",
                metrics_valid.status, len(metrics_valid.issues))
    for issue in metrics_valid.issues:
        logger.warning("validation issue: %s", issue)

    if metrics_valid.status == "failed":
        logger.error("metrics validation failed, not generating report")
        return

    csv_path, html_path = report.build_html(
        snapshot=snapshot,
        metrics=metrics_df,
        validator_result=metrics_valid,
        report_date=date_str,
        reports_dir=reports_dir,
        rotation_days=rotation_days,
    )
    logger.info("report saved: %s", csv_path)
    logger.info("report saved: %s", html_path)

    if metrics_valid.status == "usable":
        report.save_latest_html(html_path, reports_dir)
        logger.info("latest html updated: %s", html_path)
    else:
        logger.warning("data quality=%s, not updating latest.html", metrics_valid.status)

    rotation = storage.load_metrics(processed_dir)
    if not rotation.empty:
        rotation_path = processed_dir / "rotation_matrix.csv"
        if rotation_path.exists():
            logger.info("rotation matrix available: %s", rotation_path)


def cmd_validate(args: argparse.Namespace) -> None:
    logger = build_logger(args.log_level)
    root = project_root()
    raw_dir = root / "data" / "raw" / "sw_industry"
    processed_dir = root / "data" / "processed" / "sw_industry"

    master = storage.load_master(raw_dir)
    if master.empty:
        logger.error("no master data found")
        return

    raw_data: dict[str, pd.DataFrame] = {}
    for code in master["industry_code"].tolist():
        df = storage.load_industry_raw(raw_dir, code)
        if not df.empty:
            raw_data[code] = df

    raw_valid = validator.validate_raw_data(raw_data, master)
    logger.info("raw data validation: status=%s, valid=%d/%d, rows=%d",
                raw_valid.status, raw_valid.valid_industries,
                raw_valid.total_industries, raw_valid.total_raw_rows)
    for issue in raw_valid.issues:
        logger.warning("raw issue: %s", issue)
    if raw_valid.failed_industries:
        logger.warning("failed industries: %s", raw_valid.failed_industries[:10])

    metrics_df = storage.load_metrics(processed_dir)
    met_valid = validator.validate_metrics(metrics_df, latest_trade_date=raw_valid.date_max, expected_universe_size=len(master))
    logger.info("metrics validation: status=%s, rows=%d",
                met_valid.status, met_valid.total_processed_rows)
    for issue in met_valid.issues:
        logger.warning("metrics issue: %s", issue)

    logger.info("validation complete")


def cmd_run_day(args: argparse.Namespace) -> None:
    logger = build_logger(args.log_level)
    logger.info("=== run-day started ===")

    logger.info("step 1: update")
    cmd_update(args)

    logger.info("step 2: validate")
    cmd_validate(args)

    logger.info("step 3: calculate")
    cmd_calculate(args)

    logger.info("step 4: report")
    cmd_report(args)

    logger.info("=== run-day complete ===")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="申万二级行业 RPS 监控")
    sub = p.add_subparsers(dest="command", required=True)

    p_bootstrap = sub.add_parser("bootstrap", help="初始化行业列表并拉取历史数据")
    p_bootstrap.add_argument("--log-level", default="INFO")

    p_update = sub.add_parser("update", help="增量更新行情")
    p_update.add_argument("--log-level", default="INFO")

    p_calc = sub.add_parser("calculate", help="离线计算 RPS 指标和状态")
    p_calc.add_argument("--log-level", default="INFO")

    p_report = sub.add_parser("report", help="生成 HTML/CSV 报告")
    p_report.add_argument("--log-level", default="INFO")

    p_validate = sub.add_parser("validate", help="检查数据质量")
    p_validate.add_argument("--log-level", default="INFO")

    p_run = sub.add_parser("run-day", help="依次执行 update → calculate → report")
    p_run.add_argument("--log-level", default="INFO")

    return p


def main() -> None:
    args = build_arg_parser().parse_args()
    dispatch = {
        "bootstrap": cmd_bootstrap,
        "update": cmd_update,
        "calculate": cmd_calculate,
        "report": cmd_report,
        "validate": cmd_validate,
        "run-day": cmd_run_day,
    }
    fn = dispatch.get(args.command)
    if fn:
        fn(args)


if __name__ == "__main__":
    main()
