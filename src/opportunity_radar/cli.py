"""
Opportunity Radar — CLI（run --date YYYYMMDD）

只读已落盘产物（Layer① rotation / account_candidates / etf_master / three_lane），
不联网、不重算。--date 语义 = 目标 trade_date，字面精确加载、缺文件不降级。
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from src.common.paths import (
    etf_signal_daily_dir,
    etf_signal_master_dir,
    etf_signal_signals_dir,
    outputs_dir,
)
from src.opportunity_radar import radar as radar_engine


def build_logger(level: str = "INFO") -> logging.Logger:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s [radar] %(message)s", datefmt="%H:%M:%S")
    return logging.getLogger("opportunity_radar")


def _read_exact(directory: Path, pattern: str, date_str: str) -> pd.DataFrame:
    path = directory / pattern.replace("*", date_str)
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(path)
    except Exception as e:  # noqa: BLE001
        logging.getLogger("opportunity_radar").warning("read %s failed: %s", path, e)
        return pd.DataFrame()


def _read_latest(directory: Path, pattern: str) -> pd.DataFrame:
    """按文件名日期取最新（YYYYMMDD/YYYY-MM-DD 后缀）；无则空。"""
    files = sorted(directory.glob(pattern))
    if not files:
        return pd.DataFrame()
    try:
        return pd.read_parquet(files[-1])
    except Exception as e:  # noqa: BLE001
        logging.getLogger("opportunity_radar").warning("read %s failed: %s", files[-1], e)
        return pd.DataFrame()


def _resolve_trade_date(requested: str | None) -> str | None:
    """精确日期直接用；否则取最新 rotation 的文件名日期。"""
    if requested:
        return requested
    files = sorted(etf_signal_daily_dir().glob("rotation_*.parquet"))
    if not files:
        return None
    # 优先读元数据 trade_date；读不到回退文件名
    try:
        df = pd.read_parquet(files[-1], columns=["trade_date"])
        td = df["trade_date"].dropna().max()
        return pd.Timestamp(td).strftime("%Y%m%d")
    except Exception:  # noqa: BLE001
        stem = files[-1].stem.replace("rotation_", "")
        return stem if len(stem) == 8 and stem.isdigit() else None


def cmd_run(args: argparse.Namespace) -> None:
    logger = build_logger(args.log_level)
    logger.info("=" * 60)
    logger.info("OPPORTUNITY RADAR: Theme 外机会发现（Observation / Discovery）")
    logger.info("=" * 60)

    td = _resolve_trade_date(getattr(args, "date", "") or "")
    if not td:
        logger.error("no rotation data found — run etf calculate first")
        return

    # ── 输入：Layer① 全市场 + 账户趋势 ─────────────────────────────
    if getattr(args, "date", ""):
        rotation_df = _read_exact(etf_signal_daily_dir(), "rotation_*.parquet", td)
        account_df = _read_exact(etf_signal_signals_dir(), "account_candidates_*.parquet", td)
        exact = True
    else:
        rotation_df = _read_latest(etf_signal_daily_dir(), "rotation_*.parquet")
        account_df = _read_latest(etf_signal_signals_dir(), "account_candidates_*.parquet")
        exact = False
    master_path = etf_signal_master_dir() / "etf_master.parquet"
    master_df = pd.DataFrame()
    if master_path.exists():
        try:
            master_df = pd.read_parquet(master_path)
        except Exception:  # noqa: BLE001
            master_df = pd.DataFrame()

    # Lane 事实（three_lane_{trade_date}，ETF State Fusion 单一 join）
    # 对齐语义：exact 文件存在且内部 trade_date == 目标日 → 对齐消费；
    # 缺失/内部日期不一致 → lane-less（禁 fallback 前后日期），与 Selection 语义一致。
    lane_path = outputs_dir() / "etf_signal" / f"three_lane_{td}.parquet"
    lane_df = pd.DataFrame()
    lane_meta: dict[str, Any] = {"status": "lane_less", "reason": ""}
    if lane_path.exists():
        try:
            _df = pd.read_parquet(lane_path)
        except Exception as e:  # noqa: BLE001
            lane_meta = {"status": "lane_less", "reason": f"read failed: {e}"}
            _df = pd.DataFrame()
        if not _df.empty and "fund_code" in _df.columns and "trade_date" in _df.columns:
            try:
                internal = pd.Timestamp(_df["trade_date"].dropna().max()).strftime("%Y%m%d")
            except Exception:  # noqa: BLE001
                internal = None
            if internal == td:
                lane_df = _df
                lane_meta = {"status": "aligned", "trade_date": td, "reason": ""}
            else:
                lane_meta = {"status": "lane_less",
                             "reason": f"three_lane 内部 trade_date({internal}) 与目标({td})不一致"}
        else:
            lane_meta = {"status": "lane_less", "reason": "three_lane 为空或缺 fund_code/trade_date"}
    else:
        lane_meta = {"status": "lane_less", "reason": f"three_lane_{td}.parquet missing"}

    if rotation_df.empty:
        logger.error("no rotation data for trade_date %s — run etf calculate first", td)
        return
    logger.info("rotation: %d ETFs | account_candidates: %d | master: %d | lane: %s",
                len(rotation_df), len(account_df), len(master_df), lane_meta["status"])

    # 流动性门：默认透传 Selection 的既有 min_amount（Policy 参数，非新阈值）
    from src.common.spec.loaders import load_etf_selection_spec
    min_amount = float(getattr(args, "min_amount", 0) or 0)
    if min_amount <= 0:
        min_amount = float(load_etf_selection_spec().min_amount)

    payload = radar_engine.build_radar(
        rotation_df=rotation_df,
        account_df=account_df,
        master_df=master_df,
        lane_df=lane_df,
        min_amount=min_amount,
    )

    # ── 落盘：JSON 事实源 + HTML renderer ─────────────────────────
    out_dir = outputs_dir() / "opportunity_radar"
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "trade_date": td,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "load_mode": "exact" if exact else "latest",
        "lane": lane_meta,
        "min_amount": min_amount,
        "rule_version": "v1",
    }
    json_path = out_dir / f"opportunity_radar_{td}.json"
    payload_full = {"meta": meta, "summary": payload["summary"],
                    "opportunities": payload["opportunities"], "rejected": payload["rejected"]}
    json_path.write_text(_json_dumps(payload_full), encoding="utf-8")
    logger.info("radar json: %s", json_path)

    from src.opportunity_radar.report import render_radar_html
    html = render_radar_html(payload, meta)
    html_path = out_dir / f"opportunity_radar_{td}.html"
    html_path.write_text(html, encoding="utf-8")
    logger.info("radar html: %s", html_path)

    s = payload["summary"]
    logger.info("summary: full=%d mapped=%d unmapped=%d opportunity=%d (new=%d, gap=%d) rejected=%d",
                s["full_market_count"], s["mapped_count"], s["unmapped_count"],
                s["opportunity_count"], s["new_theme_count"], s["mapping_gap_count"],
                s["rejected_count"])


def _json_dumps(obj: Any) -> str:
    import json

    def _default(o: Any):
        if isinstance(o, (pd.Timestamp, pd.Timedelta)):
            return str(o)
        if isinstance(o, float):
            return o
        return str(o)
    return json.dumps(obj, ensure_ascii=False, indent=2, default=_default)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="opportunity-radar",
                                description="Opportunity Radar V1 — Theme 外机会发现（Observation）")
    sub = p.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run", help="运行当日 Opportunity Radar（读已落盘事实，不联网）")
    p_run.add_argument("--date", default="", help="目标 trade_date YYYYMMDD（默认最新 rotation 日）")
    p_run.add_argument("--min-amount", type=float, default=0.0,
                       help="流动性门（默认透传 Selection 的 etf_selection.trend.min_amount）")
    p_run.add_argument("--log-level", default="INFO")
    return p


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args(sys.argv[1:])
    cmd_run(args)


if __name__ == "__main__":
    main()
