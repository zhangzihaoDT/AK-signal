"""Leader Breakout Event V1 — CLI。"""

from __future__ import annotations

import argparse
import logging
import sys
import webbrowser

from . import STUDY_DIR
from .report import render_report
from .study import run


def build_logger() -> logging.Logger:
    logger = logging.getLogger("research.leader_breakout")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    return logger


def main() -> None:
    build_logger()
    p = argparse.ArgumentParser(description="Leader Breakout Event V1 (主题未确认时的龙头单点突破研究)")
    p.add_argument("--start-date", default="", help="研究区间起始 YYYY-MM-DD（缺省=最长可得）")
    p.add_argument("--end-date", default="", help="研究区间结束 YYYY-MM-DD（缺省=最新）")
    p.add_argument("--no-persist", action="store_true", help="只打印，不写产物")
    p.add_argument("--open", action="store_true", help="完成后浏览器打开报告")
    args = p.parse_args()

    payload = run(start_date=args.start_date or None,
                  end_date=args.end_date or None,
                  persist=not args.no_persist)
    html = render_report(payload) if not args.no_persist else ""

    meta = payload["meta"]
    q1 = payload["q1_forward_edge"]
    q2 = payload["q2_early_vs_wait"]
    q3 = payload["q3_small_position"]
    gates = payload["gates"]
    sens = payload["sensitivity"]

    print(f"events : {meta['n_events']} ({meta['n_symbols']} symbols)")
    print(f"window : {meta['window']}")
    primary = str(20)
    ph = q1.get("by_horizon", {}).get(primary, {})
    print(f"Q1 [{primary}D] excess median={_fmt(ph.get('excess', {}).get('median'))} "
          f"mean={_fmt(ph.get('excess', {}).get('mean'))} "
          f"ret median={_fmt(ph.get('ret', {}).get('median'))}")
    print(f"Q2 early median={_fmt(q2.get('early', {}).get('median'))} vs "
          f"wait median={_fmt(q2.get('wait', {}).get('median'))} | "
          f"incremental={_fmt(q2.get('incremental', {}).get('median'))} "
          f"(share>0={_fmt(q2.get('share_early_gt_wait'))})")
    w = str(0.33)
    wm = (q3.get("weights") or {}).get(w, {})
    print(f"Q3 w={w} early Sharpe={_ratio((wm.get('early') or {}).get('sharpe'))} "
          f"Calmar={_ratio((wm.get('early') or {}).get('calmar'))} | "
          f"wait Calmar={_ratio((wm.get('wait') or {}).get('calmar'))}")
    print("gates : " + " | ".join(
        f"{k}={'PASS' if v.get('pass') else 'FAIL'}" for k, v in
        (("L1", gates["L1_statistical"]), ("L2", gates["L2_economic_years"]),
         ("L3", gates["L3_path_risk"]), ("L4", gates["L4_portfolio"]))))
    print(f"sensitivity positive {sens.get('n_positive')}/{sens.get('n_total')}")
    print(f"verdict: {gates['verdict']}")
    if not args.no_persist:
        print(f"json  : {STUDY_DIR / 'leader_breakout_result.json'}")
        print(f"events: {STUDY_DIR / 'leader_breakout_events.parquet'}")
        print(f"report: {html}")
        if args.open and html:
            webbrowser.open(f"file://{html}")


def _fmt(v) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v) * 100:+.1f}%"
    except (TypeError, ValueError):
        return str(v)


def _ratio(v) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.2f}"
    except (TypeError, ValueError):
        return str(v)


if __name__ == "__main__":
    main()
