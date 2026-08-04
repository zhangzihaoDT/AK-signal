"""
Replay CLI — 历史信号重放与 parity 校验。

子命令：
  single   单日期重放（纯离线，输出 historical_signals_{date}.parquet）
  parity   单日期重放 + 与正式产物逐字段一致性校验（输出 replay_validation_{date}.json）
  range    区间重放（[start,end] 逐交易日，输出 historical_signals_{start}_{end}.parquet）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.common.paths import outputs_dir
from src.research.replay import engine as replay_engine
from src.research.replay import range as replay_range
from src.research.validation import parity as replay_parity


def _replay_dir() -> Path:
    return outputs_dir() / "research"


def cmd_single(args: argparse.Namespace) -> int:
    if not getattr(args, "date", ""):
        print("error: --date YYYYMMDD required")
        return 2
    df = replay_engine.replay_single_date(args.date, out_dir=_replay_dir(), log_level=args.log_level)
    return 0 if not df.empty else 1


def cmd_parity(args: argparse.Namespace) -> int:
    if not getattr(args, "date", ""):
        print("error: --date YYYYMMDD required")
        return 2
    df = replay_engine.replay_single_date(args.date, out_dir=_replay_dir(), log_level=args.log_level)
    report = replay_parity.check_parity(args.date, df, out_dir=_replay_dir())
    print(replay_parity.format_report(report))
    return 0 if report["ok"] else 1


def cmd_range(args: argparse.Namespace) -> int:
    if not getattr(args, "start", "") or not getattr(args, "end", ""):
        print("error: --start and --end YYYYMMDD required")
        return 2
    df = replay_range.replay_range(
        args.start, args.end,
        layers=args.layers, out_dir=_replay_dir(), log_level=args.log_level,
        resume=not getattr(args, "no_resume", False),
    )
    return 0 if not df.empty else 1


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="v0.5 Historical Signal Replay")
    sub = p.add_subparsers(dest="command")
    p_single = sub.add_parser("single", help="单日期历史信号重放")
    p_single.add_argument("--date", default="", help="目标 trade_date YYYYMMDD（必填）")
    p_single.add_argument("--log-level", default="INFO")
    p_parity = sub.add_parser("parity", help="重放 + 与正式产物一致性校验")
    p_parity.add_argument("--date", default="", help="目标 trade_date YYYYMMDD（必填）")
    p_parity.add_argument("--log-level", default="INFO")
    p_range = sub.add_parser("range", help="区间历史信号重放")
    p_range.add_argument("--start", default="", help="起始 trade_date YYYYMMDD（必填）")
    p_range.add_argument("--end", default="", help="结束 trade_date YYYYMMDD（必填）")
    p_range.add_argument("--layers", default="123", help="参与层位（默认 123；12 跳过 Layer③ 慢路径）")
    p_range.add_argument("--no-resume", action="store_true",
                         help="忽略已完成日期，全部重放（默认 resume：相同 rule_version+config_hash 跳过）")
    p_range.add_argument("--log-level", default="INFO")
    return p


def main() -> None:
    args = build_arg_parser().parse_args()
    command = getattr(args, "command", "parity")
    if command == "single":
        sys.exit(cmd_single(args))
    elif command == "parity":
        sys.exit(cmd_parity(args))
    elif command == "range":
        sys.exit(cmd_range(args))
    else:
        sys.exit(cmd_parity(args))
