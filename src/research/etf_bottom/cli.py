"""Study 1 Price Bottom + Price Bottom Map CLI。"""

from __future__ import annotations

import argparse
import logging
import sys
import webbrowser
from pathlib import Path

import pandas as pd

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

    as_of = args.date or "2026-08-28"  # 缺省 = 正式研究快照
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


def cmd_scan(args: argparse.Namespace) -> int:
    from .scanner import run_scan
    from .scanner_report import render_scan

    payload = run_scan(args.date or None)  # date 缺省 → 自动取最新 raw 交易日
    html = render_scan(payload)
    a = payload["layer_a_market_bottom_map"]
    print(f"scan json  : {STUDY_DIR / ('scan_' + str(payload['as_of']).replace('-', '') + '.json')}")
    print(f"scan pq/csv: {STUDY_DIR / ('scan_' + str(payload['as_of']).replace('-', '') + '.parquet')}")
    print(f"scan html  : {html}")
    print("Layer A:", {k: a[k] for k in ("reliable_total", "cohort", "long_term_bottom_total",
                                         "deep_total", "recovering_total", "target_total",
                                         "near_miss_total")})
    print("transition:", a["transition_counts"])
    if args.open:
        webbrowser.open(f"file://{html}")
    return 0


def cmd_refresh_v1(args: argparse.Namespace) -> int:
    """轻量刷新 v1_signal_daily.parquet（每日 run-day，供 Lane 3 状态机消费）。"""
    from .backtest_v1 import refresh_v1_signal_daily

    out = refresh_v1_signal_daily(end=args.end_date or None)
    df = pd.read_parquet(out)
    print(f"v1_signal_daily : {out}")
    print(f"rows            : {len(df)}")
    print(f"date range      : {df['trade_date'].min().date()} -> {df['trade_date'].max().date()}")
    print(f"funds           : {df['fund_code'].nunique()}")
    return 0


def cmd_backtest_v1(args: argparse.Namespace) -> int:
    from .backtest_v1 import run_backtest_v1
    from .backtest_v1_report import render_v1_backtest

    end = args.end_date or "2026-08-31"
    payload = run_backtest_v1(start=args.start_date, end=end)
    html = render_v1_backtest(payload)
    inc = payload["incidence"]
    print(f"v1 json : {STUDY_DIR / 'backtest_v1' / 'v1_incidence_summary.json'}")
    print(f"v1 html : {html}")
    print(f"verdict : {payload['verdict']}")
    print("incidence:", {k: inc[k] for k in ("total_trade_days", "target_days", "target_day_rate",
                                             "total_target_signal_days", "unique_target_etfs",
                                             "max_targets_single_day")})
    print("longest_zero_target_streak:", payload["zero_target_streaks"]["longest_zero_target_streak"])
    print("target_events:", payload["target_events"])
    print("forward_comparison:")
    for r in payload["forward_comparison"]:
        print(f"  {r['stage']:<20} n={r['n_events']:<5} med120={r['ret120_median']} "
              f"win120={r['win120']} exc120={r['excess120_median']}")
    if args.open:
        webbrowser.open(f"file://{html}")
    return 0


def cmd_current_eval(args: argparse.Namespace) -> int:
    from .current_eval import run_current_eval
    from .current_eval_report import render_current_odds, _sorted_etfs

    payload = run_current_eval()
    html = render_current_odds(payload)
    print(f"eval json : {STUDY_DIR / 'current_watch_eval.json'}")
    print(f"odds csv  : {STUDY_DIR / 'current_odds_table.csv'}")
    print(f"odds html : {html}")
    print("stage_summary:", payload["stage_summary"])
    print("cut_points source:", payload["cut_points_source"])
    print("\n当前赔率表（8 列，emoji 仅展示层，数据层为稳定枚举）:")
    print(f"{'ETF':<22}{'stage':<16}{'n':<4}{'med120':>9}{'win':>6}{'payoff':>8}{'evidence':<20}{'odds'}")
    for e in _sorted_etfs(payload["etfs"]):
        h = e.get("history", {})
        med = f"{h.get('median_120d', 0)*100:+.1f}%" if h.get("median_120d") is not None else "—"
        win = f"{h.get('win_rate', 0)*100:.0f}%" if h.get("win_rate") is not None else "—"
        pay = f"{h.get('payoff_ratio'):.2f}" if h.get("payoff_ratio") is not None else "—"
        n = h.get("n", 0) if h.get("n") is not None else "—"
        mark = _odds_mark(e.get("odds_assessment", "unreliable"))
        etf = f"{e['fund_code']}·{e['fund_name']}"
        print(f"{etf:<22}{e['stage']:<16}{str(n):<4}"
              f"{med:>9}{win:>6}{pay:>8}{e.get('evidence_label', ''):<20}{mark}")
    if args.open:
        webbrowser.open(f"file://{html}")
    return 0


def _odds_mark(odds: str) -> str:
    """数据层枚举 → 展示符号（仅 CLI/HTML 层，不写回数据产物）。"""
    return {
        "strong_observe": "★", "watch_structure": "🟢", "position_only": "🟡",
        "cautious": "🔴", "out_of_domain_good": "⚪", "out_of_domain_unknown": "🟡",
        "out_of_domain_bad": "🔴", "unreliable": "⚠️",
    }.get(odds, odds)


def main() -> None:
    p = argparse.ArgumentParser(description="Study 1 Price Bottom / Price Bottom Map / State Odds")
    p.add_argument("--open", action="store_true", help="完成后浏览器打开报告")
    p.add_argument("--date", default="", help="锚点日期 YYYY-MM-DD（price-map 缺省 2026-08-28 快照；scan 缺省自动取最新 raw 交易日）")
    p.add_argument("--price-map", action="store_true", help="运行 Price Bottom Map（横截面低位地图）而非 Study 1")
    p.add_argument("--state-odds", action="store_true", help="运行 Study 2 State Odds（进入底部状态后的前向收益）")
    p.add_argument("--drilldown", action="store_true", help="运行 Study 2A Drilldown（当前底部 ETF 逐只历史赔率）")
    p.add_argument("--episodes", action="store_true", help="运行 Study 2B Episodes（产业底部周期压缩）")
    p.add_argument("--context-match", action="store_true", help="运行 Study 2C Context Matching（当前底部 vs 历史 context）")
    p.add_argument("--context-replication", action="store_true", help="运行 Study 2D Replication（大样本复现 2C 发现）")
    p.add_argument("--repair", action="store_true", help="运行 Study 2E Repair Structure（pos120 结构验证）")
    p.add_argument("--current-eval", action="store_true", help="评估当前关注 ETF 的 Repair-Retest V1 阶段（读 2E 冻结 cut points）")
    p.add_argument("--scan", action="store_true", help="全市场 Repair-Retest V1 每日扫描（Application，三层：Map/Scanner/Odds）")
    p.add_argument("--refresh-v1", action="store_true", help="轻量刷新 v1_signal_daily.parquet（每日 run-day，供 Lane 3 状态机消费）")
    p.add_argument("--backtest-v1", action="store_true", help="Repair-Retest V1 历史触发频率回测（Application Backtest / Signal Incidence）")
    p.add_argument("--start-date", default="2022-01-01", help="回测区间起始日 YYYY-MM-DD")
    p.add_argument("--end-date", default="", help="refresh-v1 / 回测区间结束日 YYYY-MM-DD（缺省自动取最新 raw 交易日）")
    p.set_defaults(func=cmd_run)
    args = p.parse_args()
    if args.scan:
        sys.exit(cmd_scan(args))
    if args.refresh_v1:
        sys.exit(cmd_refresh_v1(args))
    if args.backtest_v1:
        sys.exit(cmd_backtest_v1(args))
    if args.current_eval:
        sys.exit(cmd_current_eval(args))
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
