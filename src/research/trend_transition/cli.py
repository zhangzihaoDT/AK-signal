"""Lane 3 · Study 3A / 3B / 3C / state CLI。"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import webbrowser
from pathlib import Path

from . import PERSISTENCE_PRIMARY, PERSISTENCE_ROBUST, STUDY_DIR
from .study3a import run_study3a
from .study3a_report import render as render3a
from .study3b import run_study3b
from .study3b_report import render as render3b
from .study3c import run_study3c
from .study3c_report import render as render3c


def build_logger() -> logging.Logger:
    logger = logging.getLogger("research.trend_transition")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(h)
    return logger


def cmd_study3a(args: argparse.Namespace) -> int:
    logger = build_logger()
    payload = run_study3a(
        primary=args.primary_persistence,
        robust=args.robust_persistence,
        break_date=args.break_date,
        n_boot=args.n_boot,
        seed=args.seed,
    )
    html = render3a(payload)
    print(f"json     : {STUDY_DIR / (f'study3a_{args.primary_persistence}_{args.robust_persistence}.json')}")
    print(f"html     : {html}")
    sb = payload["structural_break"]
    b63 = sb["hypothesis_break"]["windows"]["63"]
    print(f"break@924 effect ±63 : {b63['pre_escape']} → {b63['post_escape']} (effect={b63['effect']})")
    print(f"date-block CI        : {sb['date_block_bootstrap']['ci95']} "
          f"cross0={sb['date_block_bootstrap']['ci_crosses_zero']}")
    print(f"data-driven argmax   : {sb['data_driven_breakpoint']['argmax_date']} "
          f"(near924={sb['data_driven_breakpoint']['near_924']})")
    from .study3a_report import _checklist

    n_pass = sum(1 for c in _checklist(payload) if c["ok"])
    print(f"PASS checklist       : {n_pass}/5")
    if args.open:
        webbrowser.open(f"file://{html}")
    return 0


def cmd_study3b(args: argparse.Namespace) -> int:
    payload = run_study3b(persistence=args.persistence, horizon=args.horizon, n_l2=args.l2)
    # 纯 renderer：只消费落盘的结果文件（summary.json），不重算不训练
    html = render3b()
    print(f"summary  : {STUDY_DIR / 'study3b_summary.json'}")
    print(f"html     : {html}")
    print(f"base rate: {payload['base_rate']:.4f}  n_trainable={payload['n_trainable']}")
    print(f"score    : {payload['score_features']}")
    print(f"verdict  : {payload['pass_gate']['verdict']}  ({payload['pass_gate']['n_pass']}/5)")
    for k, v in payload["pass_gate"]["checks"].items():
        print(f"  {k} ok={v['ok']}  {v['detail']}")
    if args.open:
        webbrowser.open(f"file://{html}")
    return 0


def cmd_study3c(args: argparse.Namespace) -> int:
    payload = run_study3c(persistence=args.persistence)
    html = render3c()
    print(f"summary  : {STUDY_DIR / 'study3c_summary.json'}")
    print(f"html     : {html}")
    print(f"as_of    : {payload['as_of']}  n_hist={payload['n_hist_rows']}")
    print(f"verdict  : {payload['pass_gate']['verdict']}  ({payload['pass_gate']['n_pass']}/5)")
    for k, v in payload["validation"]["checks"].items():
        print(f"  {k} ok={v['ok']}  {str(v.get('detail', ''))[:100]}")
    if args.open:
        webbrowser.open(f"file://{html}")
    return 0


def cmd_state(args: argparse.Namespace) -> int:
    """Application：读冻结 YAML，输出 date-stamped 状态表（不写 _latest）。

    --date 缺省 = 最新 v1_signal_daily trade_date（run-day 集成用）。
    """
    import pandas as pd

    from .study3c import run_application

    as_of = pd.Timestamp(args.date) if args.date else None
    app = run_application(as_of, persistence=args.persistence)
    cur = app["state"]
    date_str = app["as_of"].replace("-", "")
    base = STUDY_DIR / f"trend_transition_state_{date_str}"
    cur.to_parquet(f"{base}.parquet", index=False)
    with open(f"{base}.json", "w", encoding="utf-8") as f:
        json.dump({"as_of": app["as_of"], "spec": app["spec"],
                   "n_rows": int(len(cur)),
                   "lane1_overlap": app["lane1_overlap"],
                   "lane2_overlap": app["lane2_overlap"]},
                  f, ensure_ascii=False, indent=2, default=str)
    print(f"parquet  : {base}.parquet")
    print(f"json     : {base}.json")
    print(f"as_of    : {app['as_of']}  rows={len(cur)}")
    print(f"spec     : {app['spec'].get('rule_id')} ({app['spec'].get('status')})")
    print("state dist:", cur["transition_state"].value_counts().to_dict())
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="Lane 3 · Trend Transition 研究（3A / 3B / 3C / state）")
    p.add_argument("subcommand", nargs="?", choices=["study3a", "study3b", "study3c", "state"],
                   default="study3c",
                   help="study3a=断点；study3b=预测；study3c=状态分类（研究）；state=应用（读冻结 YAML）")
    p.add_argument("--open", action="store_true", help="完成后浏览器打开报告")
    p.add_argument("--persistence", type=int, default=PERSISTENCE_PRIMARY,
                   help="persistence 口径 0|3|5（默认 3）")
    p.add_argument("--date", default=None, help="state：YYYYMMDD 或 YYYY-MM-DD")
    p.add_argument("--horizon", type=int, default=120, help="3B：ESCAPE_60D/120D/250D")
    p.add_argument("--l2", type=float, default=1.0, help="3B：logistic L2 正则强度")
    p.add_argument("--primary-persistence", type=int, default=PERSISTENCE_PRIMARY)
    p.add_argument("--robust-persistence", type=int, default=PERSISTENCE_ROBUST)
    p.add_argument("--break-date", default="2024-09-24")
    p.add_argument("--n-boot", type=int, default=2000)
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()
    if args.subcommand == "study3a":
        sys.exit(cmd_study3a(args))
    if args.subcommand == "study3b":
        sys.exit(cmd_study3b(args))
    if args.subcommand == "state":
        sys.exit(cmd_state(args))
    sys.exit(cmd_study3c(args))


if __name__ == "__main__":
    main()
