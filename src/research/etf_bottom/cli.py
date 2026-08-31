"""Study 1 Price Bottom + Price Bottom Map CLI。"""

from __future__ import annotations

import argparse
import logging
import sys
import webbrowser
from pathlib import Path

from . import STUDY_DIR
from .report import render_report, render_robustness_report
from .study import run


def build_logger() -> logging.Logger:
    logger = logging.getLogger("research.etf_bottom")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(h)
    return logger


def cmd_run(args: argparse.Namespace) -> int:
    logger = build_logger()
    payload = run()
    html = render_report(payload)
    print(f"study1 result : {STUDY_DIR / 'study1_result.json'}")
    print(f"events        : {STUDY_DIR / 'events.parquet'}")
    print(f"report        : {html}")
    if "robustness" in payload:
        rb = render_robustness_report(payload)
        print(f"study1b report: {rb}")
        if args.open:
            webbrowser.open(f"file://{html}")
    elif args.open:
        webbrowser.open(f"file://{html}")
    return 0


def cmd_price_map(args: argparse.Namespace) -> int:
    from .price_map import build_price_map, write_products
    from .price_map_report import render_price_map

    as_of = args.date
    df = build_price_map(as_of)
    paths = write_products(df, as_of)
    html = render_price_map(df, as_of)
    print(f"price-map csv  : {paths['csv']}")
    print(f"price-map pq   : {paths['parquet']}")
    print(f"price-map json : {paths['json']}")
    print(f"price-map html : {html}")
    counts = df["bottom_state"].value_counts().to_dict()
    print("bottom_state:", counts)
    print("long_term_bottom:", int(df["long_term_bottom"].sum()))
    if args.open:
        webbrowser.open(f"file://{html}")
    return 0


def cmd_state_odds(args: argparse.Namespace) -> int:
    from .state_odds import run_state_odds
    from .state_odds_report import render_state_odds

    payload = run_state_odds()
    html = render_state_odds(payload)
    print(f"state-odds json : {STUDY_DIR / 'state_odds_result.json'}")
    print(f"state-odds events: {STUDY_DIR / 'state_odds_events.parquet'}")
    print(f"state-odds html  : {html}")
    summary = payload["summary"]
    for st in ("DEEP_BOTTOM", "RECOVERING_FROM_BOTTOM", "RECENT_BOTTOM"):
        m = summary.get(st, {}).get("_meta", {})
        print(f"  {st}: events={m.get('n_events')} etfs={m.get('n_etfs')}")
    if args.open:
        webbrowser.open(f"file://{html}")
    return 0


def cmd_drilldown(args: argparse.Namespace) -> int:
    from .drilldown import run_drilldown
    from .drilldown_report import render_drilldown

    payload = run_drilldown()
    html = render_drilldown(payload)
    print(f"drilldown json : {STUDY_DIR / 'state_odds_drilldown.json'}")
    print(f"drilldown events: {STUDY_DIR / 'state_odds_drilldown_events.parquet'}")
    print(f"drilldown html  : {html}")
    print("support_summary:", payload["support_summary"])
    if args.open:
        webbrowser.open(f"file://{html}")
    return 0


def cmd_episodes(args: argparse.Namespace) -> int:
    from .episodes import run_episodes
    from .episodes_report import render_episodes

    payload = run_episodes()
    html = render_episodes(payload)
    print(f"episodes json  : {STUDY_DIR / 'bottom_episodes.json'}")
    print(f"episodes pq    : {STUDY_DIR / 'bottom_episodes.parquet'}")
    print(f"episodes html  : {html}")
    print("summary:")
    for cluster, s in payload["summary"].items():
        print(f"  {cluster}: {s['n_episodes_historical']} 历史周期, {s['n_episodes_up']}/{s['n_episodes_historical']} 上涨")
    if args.open:
        webbrowser.open(f"file://{html}")
    return 0


def cmd_context_match(args: argparse.Namespace) -> int:
    from .context_match import run_context_matching
    from .context_report import render_context

    payload = run_context_matching()
    html = render_context(payload)
    print(f"context json   : {STUDY_DIR / 'context_matching.json'}")
    print(f"context pq     : {STUDY_DIR / 'context_features.parquet'}")
    print(f"context html   : {html}")
    print("matches:")
    for eid, m in payload["matches"].items():
        top = m["top3_equal"][0]
        print(f"  {eid}: 最近似 {top['episode_id']} (dist={top['distance_equal']}, ret120={top['ret120_median']})")
    if args.open:
        webbrowser.open(f"file://{html}")
    return 0


def cmd_replication(args: argparse.Namespace) -> int:
    from .replication import run_replication
    from .replication_report import render_replication

    payload = run_replication()
    html = render_replication(payload)
    print(f"replication json : {STUDY_DIR / 'context_replication.json'}")
    print(f"replication pq   : {STUDY_DIR / 'context_replication_events.parquet'}")
    print(f"replication html : {html}")
    print("Layer3 verdict:")
    for r in payload["layer3"]:
        print(f"  {r['feature']:20s} broad_spread={r['broad_q1_q5_spread']} 年内({r['year_positive_count']}正/{r['year_negative_count']}负) → {r['year_verdict']}")
    if args.open:
        webbrowser.open(f"file://{html}")
    return 0


def cmd_repair(args: argparse.Namespace) -> int:
    from .repair_structure import run_repair
    from .repair_structure_report import render_repair

    payload = run_repair()
    html = render_repair(payload)
    print(f"repair json : {STUDY_DIR / 'repair_structure.json'}")
    print(f"repair html : {html}")
    print("adjudication:", payload["adjudication"]["verdict"])
    print(payload["adjudication"]["summary"])
    if args.open:
        webbrowser.open(f"file://{html}")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="Study 1 Price Bottom / Price Bottom Map / State Odds")
    p.add_argument("--open", action="store_true", help="完成后浏览器打开报告")
    p.add_argument("--date", default="2026-08-28", help="价格地图锚点日期（默认 2026-08-28 正式快照）")
    p.add_argument("--price-map", action="store_true", help="运行 Price Bottom Map（横截面低位地图）而非 Study 1")
    p.add_argument("--state-odds", action="store_true", help="运行 Study 2 State Odds（进入底部状态后的前向收益）")
    p.add_argument("--drilldown", action="store_true", help="运行 Study 2A Drilldown（当前底部 ETF 逐只历史赔率）")
    p.add_argument("--episodes", action="store_true", help="运行 Study 2B Episodes（产业底部周期压缩）")
    p.add_argument("--context-match", action="store_true", help="运行 Study 2C Context Matching（当前底部 vs 历史 context）")
    p.add_argument("--context-replication", action="store_true", help="运行 Study 2D Replication（大样本复现 2C 发现）")
    p.add_argument("--repair", action="store_true", help="运行 Study 2E Repair Structure（pos120 结构验证）")
    p.set_defaults(func=cmd_run)
    args = p.parse_args()
    if args.repair:
        sys.exit(cmd_repair(args))
    if args.context_replication:
        sys.exit(cmd_replication(args))
    if args.context_match:
        sys.exit(cmd_context_match(args))
    if args.episodes:
        sys.exit(cmd_episodes(args))
    if args.drilldown:
        sys.exit(cmd_drilldown(args))
    if args.state_odds:
        sys.exit(cmd_state_odds(args))
    if args.price_map:
        sys.exit(cmd_price_map(args))
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
