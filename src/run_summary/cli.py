"""
Run-day Summary — CLI（run --date YYYYMMDD）

只读已落盘产物：
  outputs/selection/tradable_candidates_{date}.json
  outputs/opportunity_radar/opportunity_radar_{date}.json
不联网、不重算。--date 语义 = 目标 trade_date，字面精确加载、缺文件不降级。

产物：outputs/run_summary/run_summary_{trade_date}.{json,md}
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from src.common.paths import outputs_dir
from src.run_summary.summary import build_summary, render_markdown, render_text


def build_logger(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("run_summary")
    if logger.handlers:
        return logger
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    return logger


def _latest_selection_date() -> str | None:
    out_dir = outputs_dir() / "selection"
    if not out_dir.exists():
        return None
    dates: list[str] = []
    for p in out_dir.glob("tradable_candidates_*.json"):
        m = re.search(r"(\d{8})", p.name)
        if m:
            dates.append(m.group(1))
    return max(dates) if dates else None


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logging.getLogger("run_summary").warning("read %s failed: %s", path, e)
        return None


def _fmt_date(date_str: str) -> str:
    try:
        return datetime.strptime(str(date_str), "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return str(date_str)


def cmd_run(args: argparse.Namespace) -> int:
    logger = build_logger(args.log_level)
    logger.info("=" * 60)
    logger.info("RUN-DAY SUMMARY: 当日结论汇总（Layer③ + Radar，只读消费）")
    logger.info("=" * 60)

    requested = getattr(args, "date", "") or ""
    trade_date = requested or _latest_selection_date() or ""
    if not trade_date:
        logger.error("no selection candidates found — run `make select` first")
        return 1

    selection = _load_json(
        outputs_dir() / "selection" / f"tradable_candidates_{trade_date}.json"
    )
    radar = _load_json(
        outputs_dir() / "opportunity_radar" / f"opportunity_radar_{trade_date}.json"
    )

    summary = build_summary(trade_date=trade_date, selection=selection, radar=radar)

    # 终端输出（人类可读）
    print(render_text(summary))

    # 落盘：JSON 事实源 + Markdown renderer
    out_dir = outputs_dir() / "run_summary"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"run_summary_{trade_date}.json"
    json_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    md_path = out_dir / f"run_summary_{trade_date}.md"
    md_path.write_text(render_markdown(summary), encoding="utf-8")

    logger.info("summary json: %s", json_path)
    logger.info("summary md  : %s", md_path)
    logger.info("trade_date=%s recommended=%d candidate_directions=%d broad_beta=%d",
                trade_date, len(summary["recommendations"]),
                len(summary["opportunities"]["candidate_themes"]),
                len(summary["opportunities"]["broad_beta"]))
    for m in summary["missing"]:
        logger.warning("missing: %s", m)

    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="run-summary", description="run-day 当日结论汇总（只读消费）")
    sub = p.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run", help="汇总当日 Layer③ 推荐 + Radar 机会（读已落盘事实，不联网）")
    p_run.add_argument("--date", default="", help="目标 trade_date YYYYMMDD（默认最新 selection 日）")
    p_run.add_argument("--log-level", default="INFO")
    return p


def main() -> None:
    argv = sys.argv[1:]
    if not argv:
        argv = ["run"]
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    cmd = getattr(args, "command", None)
    if cmd != "run":
        parser.error(f"unknown command: {cmd}")
    sys.exit(cmd_run(args))


if __name__ == "__main__":
    main()
