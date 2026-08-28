"""
Expression Regime 研究编排（v0.10）— 区间重放 → 表达事件 → counterfactual 收益 → 指标。

对区间内每个交易日重放 Layer①（ETF 横截面）/ Layer②（行业确认）/ 个股趋势（历史表），
经 evaluate_themes 得到每主题确认状态，用可插拔结构输入源判定 expression；
对每个「主题确认日」同时记录三种表达资产（核心 ETF / 龙头个股 / 等权组合），
事后计算 20/60/120D 前向收益，回答「结构判断是否预测了更优表达」：
  hit_rate          判定表达事后 ≥ 最优表达（任一）的比例
  delta_to_best     判定表达 − 事后最优表达（0 = 完美预测，负 = 判错）
  etf_vs_stock      判定 ETF 组内 ETF 相对龙头个股的超额
  combo_vs_single   CORE_PLUS 组内混合相对最优单一表达的超额
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

import numpy as np
import pandas as pd

from src.common import themes as themes_cfg
from src.common.paths import selection_universe_path
from src.research.event_study.returns import PriceBook, build_price_book
from src.research.replay import engine as replay_engine
from src.selection import selection as sel_module
from src.selection.universe import load_universe_items
from src.sw_industry_rps import confirmation as sw_confirmation
from src.sw_industry_rps import tier_confirmation as tier_conf

from . import structure as struct_mod
from .history import build_stock_trend_history, trend_snapshot_at

logger = logging.getLogger("research.expression_regime.study")

DEFAULT_HORIZONS = (20, 60, 120)

EXPRESSION_WEIGHTS = {  # 表达 → 表达资产的收益构成（等权组合为 0.5 ETF + 0.5 Leader）
    struct_mod.ETF_PRIORITY: "etf",
    struct_mod.LEADER_PRIORITY: "stock",
    struct_mod.CORE_PLUS_LEADER: "combo",
}

EVENT_COLUMNS = [
    "trade_date", "theme", "theme_label", "bucket", "bucket_label",
    "confirmation_state", "expression", "expression_reason",
    "broad", "leader_dominated",
    "median_advance_ratio", "median_leader_contribution",
    "median_participation", "median_hhi", "median_top3_share",
    "etf_code", "etf_name", "etf_rps15", "etf_trend_status",
    "leader_code", "leader_name", "leader_score", "leader_watch_level",
]


def _num(v: Any) -> float | None:
    if v is None or (isinstance(v, float) and v != v):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _theme_core_etf(rotation_df: pd.DataFrame, account_df: pd.DataFrame,
                    master_df: pd.DataFrame, theme: str) -> dict[str, Any]:
    """主题核心 ETF：按生产三级降级（严格门 → watch 门 → 不过滤）+ 评分 top1。"""
    etf_pool = sel_module.select_etf_candidates(rotation_df, account_df, master_df, theme,
                                                trend_gates=sel_module.ETF_TREND_GATES)
    dedup = sel_module._dedup_etf(etf_pool)
    if dedup.empty:
        dedup = sel_module._dedup_etf(sel_module.select_etf_candidates(
            rotation_df, account_df, master_df, theme, trend_gates=sel_module.ETF_WATCH_GATES))
    if dedup.empty:
        dedup = sel_module._dedup_etf(sel_module.select_etf_candidates(
            rotation_df, account_df, master_df, theme, trend_gates=None))
    if dedup.empty:
        return {"code": "", "name": "", "rps15": None, "trend_status": ""}
    top = dedup.sort_values("selection_score", ascending=False).iloc[0]
    return {"code": str(top.get("fund_code", "")), "name": str(top.get("fund_name", "")),
            "rps15": _num(top.get("rps15")), "trend_status": str(top.get("trend_state", "") or "")}


def _theme_leader(universe_items: Sequence[Any], theme: str, trend_df: pd.DataFrame) -> dict[str, Any]:
    """主题龙头个股：RECOMMENDED 中 strategy_score 最高（与生产 _primary_stock 一致）。"""
    leaders, high_beta, equipment = sel_module.select_stock_watchlist(
        universe_items, theme, trend_df, theme_confirmed=True)
    stock_candidates = [
        c.to_dict() for c in (leaders + high_beta + equipment)
        if c.state in (sel_module.STOCK_STATE_QUALIFIED, sel_module.STOCK_STATE_RECOMMENDED)
    ]
    primary = sel_module._primary_stock(stock_candidates)
    if not primary:
        return {"code": "", "name": "", "score": None, "watch_level": ""}
    p = primary[0]
    return {"code": str(p.get("code", "")), "name": str(p.get("name", "")),
            "score": p.get("strategy_score"), "watch_level": str(p.get("trend_status", "") or "")}


def collect_expression_events(
    start_date: str,
    end_date: str,
    *,
    structure_source: str = "tier",
    themes: Sequence[str] | None = None,
    cache: dict[str, Any] | None = None,
    stock_history: dict[str, pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """对区间内每交易日重放并收集「主题确认 + 表达判定」事件。

    Returns:
        EVENT_COLUMNS 长表（一主题确认日一行）。
    """
    cache = cache or replay_engine.build_replay_cache()
    master = cache.get("master")
    combined = cache.get("combined")
    metrics_df = cache.get("metrics_df")
    universe_items = load_universe_items(selection_universe_path())
    if stock_history is None:
        stock_history = build_stock_trend_history(universe_items)
    struct_in = struct_mod.build_structure_input(structure_source)
    is_tier_input = isinstance(struct_in, struct_mod.TierStructureInput)

    theme_keys = list(themes) if themes else list(themes_cfg.load_themes())
    dates = replay_engine.replay_calendar(cache, start_date, end_date)
    logger.info("expression regime: %d trading days in [%s, %s], structure=%s",
                len(dates), start_date, end_date, structure_source)

    all_tiers: dict[str, list[dict[str, Any]]] = {}
    events: list[dict[str, Any]] = []
    lgr = replay_engine.build_logger("ERROR")
    for t in dates:
        _l1, rotation_df, account_df = replay_engine._replay_layer1(
            t, lgr, master=master, combined=combined)
        if rotation_df.empty:
            continue
        focus = sw_confirmation.compute_focus_snapshot(metrics_df, date=t) \
            if metrics_df is not None and not metrics_df.empty else pd.DataFrame()
        trend_df = trend_snapshot_at(stock_history, t)

        # 全部主题的 Tier 确认（统一观察单元）
        tier_rows_all: list[dict[str, Any]] = []
        for tk in themes_cfg.load_themes():
            tier_rows_all.extend(tier_conf.tier_metrics_for_theme(tk, trend_df, universe_items))
        tier_cf_df = pd.DataFrame(tier_rows_all) if tier_rows_all else pd.DataFrame()

        metas = sel_module.evaluate_themes(focus, rotation_df, tier_cf_df)
        for theme_key in theme_keys:
            meta = metas.get(theme_key, {})
            if not meta.get("confirmed"):
                continue
            th_label = meta.get("label", theme_key)
            bucket = meta.get("bucket", "")
            bucket_label = meta.get("bucket_label", "")
            conf_state = meta.get("confirmation_state", "")
            if is_tier_input:
                expr, reason, feat = struct_in.expression(theme_key, tier_rows_all)
            else:
                expr, reason, feat = struct_in.expression(meta)
            etf = _theme_core_etf(rotation_df, account_df, master, theme_key)
            leader = _theme_leader(universe_items, theme_key, trend_df)
            ev = {
                "trade_date": t,
                "theme": theme_key,
                "theme_label": th_label,
                "bucket": bucket,
                "bucket_label": bucket_label,
                "confirmation_state": conf_state,
                "expression": expr,
                "expression_reason": reason,
                "broad": feat.get("broad"),
                "leader_dominated": feat.get("leader_dominated"),
                "median_advance_ratio": feat.get("median_advance_ratio"),
                "median_leader_contribution": feat.get("median_leader_contribution"),
                "median_participation": feat.get("median_participation"),
                "median_hhi": feat.get("median_hhi"),
                "median_top3_share": feat.get("median_top3_share"),
                "etf_code": etf["code"], "etf_name": etf["name"],
                "etf_rps15": etf["rps15"], "etf_trend_status": etf["trend_status"],
                "leader_code": leader["code"], "leader_name": leader["name"],
                "leader_score": leader["score"], "leader_watch_level": leader["watch_level"],
            }
            events.append(ev)
    df = pd.DataFrame(events, columns=EVENT_COLUMNS) if events else pd.DataFrame(columns=EVENT_COLUMNS)
    logger.info("expression events collected: %d (dates=%d, themes=%s)",
                len(df), len(dates), list(theme_keys))
    return df


def augment_event_returns(
    events: pd.DataFrame,
    book: PriceBook,
    horizons: tuple[int, ...],
) -> pd.DataFrame:
    """为每个事件追加三种表达（ETF / Leader / 组合）前向收益与判定质量指标。"""
    if events.empty:
        return events
    rows: list[dict[str, Any]] = []
    for _, ev in events.iterrows():
        t = str(ev["trade_date"])
        etf_ret = book.forward_returns("etf", str(ev["etf_code"]), t, horizons) if ev.get("etf_code") else {}
        stock_ret = book.forward_returns("stock", str(ev["leader_code"]), t, horizons) if ev.get("leader_code") else {}
        bench = book.benchmark_forward("etf", t, horizons)
        row: dict[str, Any] = {c: ev.get(c) for c in EVENT_COLUMNS}
        for h in horizons:
            er, sr = etf_ret.get(h), stock_ret.get(h)
            combo = (0.5 * er + 0.5 * sr) if (er is not None and sr is not None) else None
            avail = [v for v in (er, sr, combo) if v is not None]
            best = max(avail) if avail else None
            chosen_key = EXPRESSION_WEIGHTS.get(str(ev.get("expression", "")), "combo")
            chosen_map = {"etf": er, "stock": sr, "combo": combo}
            chosen = chosen_map.get(chosen_key)
            row[f"etf_{h}"] = er
            row[f"stock_{h}"] = sr
            row[f"combo_{h}"] = combo
            row[f"bench_{h}"] = bench.get(h)
            row[f"chosen_{h}"] = chosen
            row[f"best_{h}"] = best
            row[f"hit_{h}"] = (chosen is not None and best is not None and chosen >= best - 1e-12)
            row[f"delta_best_{h}"] = (chosen - best) if (chosen is not None and best is not None) else None
            row[f"etf_vs_stock_{h}"] = (er - sr) if (er is not None and sr is not None) else None
            row[f"combo_vs_single_{h}"] = (combo - max(v for v in (er, sr) if v is not None)) \
                if (combo is not None and er is not None and sr is not None) else None
        rows.append(row)
    df = pd.DataFrame(rows)
    # counterfactual 需三种表达资产齐全（ETF + Leader）才可比（combo/best 依赖两者）
    keep = (df["etf_code"].astype(str).str.len() > 0) & (df["leader_code"].astype(str).str.len() > 0)
    df = df[keep].reset_index(drop=True)
    logger.info("event returns augmented: %d events (after filter)", len(df))
    return df


def _biz_days_between(a: Any, b: Any) -> int:
    try:
        return int(np.busday_count(pd.Timestamp(a).strftime("%Y-%m-%d"),
                                   pd.Timestamp(b).strftime("%Y-%m-%d"), weekmask="1111100"))
    except Exception:
        return 0


def count_non_overlap(events: pd.DataFrame, horizon: int) -> int:
    """按 theme 分组的互不重叠事件数（同主题相邻事件间隔 ≥ horizon）。"""
    total = 0
    for _theme, g in events.groupby("theme"):
        g = g.sort_values("trade_date")
        last: Any = None
        for t in g["trade_date"]:
            if last is None or _biz_days_between(last, t) >= horizon:
                total += 1
                last = t
    return total


def _stat(s: pd.Series) -> dict[str, Any]:
    vals = pd.to_numeric(s, errors="coerce").dropna()
    if vals.empty:
        return {"n": 0, "mean": None, "median": None, "win_rate": None}
    return {"n": int(len(vals)), "mean": round(float(vals.mean()), 4),
            "median": round(float(vals.median()), 4),
            "win_rate": round(float((vals > 0).mean()), 4)}


def aggregate_summary(
    events_df: pd.DataFrame,
    horizons: tuple[int, ...],
    group_cols: tuple[str, ...] = ("expression",),
) -> pd.DataFrame:
    """按 (group_cols, horizon) 汇总判定质量与表达相对表现。"""
    if events_df.empty:
        return pd.DataFrame()
    out: list[dict[str, Any]] = []
    if group_cols:
        grouped = events_df.groupby(list(group_cols), dropna=False)
    else:
        grouped = events_df.assign(_g="all").groupby(["_g"], dropna=False)
    for keys, g in grouped:
        keys = (keys,) if isinstance(keys, str) else tuple(keys)
        for h in horizons:
            chosen = pd.to_numeric(g.get(f"chosen_{h}"), errors="coerce")
            best = pd.to_numeric(g.get(f"best_{h}"), errors="coerce")
            hit = g.get(f"hit_{h}").astype(bool) if f"hit_{h}" in g.columns else pd.Series(index=g.index, dtype=bool)
            dtb = pd.to_numeric(g.get(f"delta_best_{h}"), errors="coerce")
            evs = pd.to_numeric(g.get(f"etf_vs_stock_{h}"), errors="coerce")
            cvs = pd.to_numeric(g.get(f"combo_vs_single_{h}"), errors="coerce")
            bench = pd.to_numeric(g.get(f"bench_{h}"), errors="coerce")
            chosen_exc = (chosen - bench) if not bench.dropna().empty else chosen
            rec: dict[str, Any] = dict(zip(group_cols, keys))
            rec.update({
                "horizon": h,
                "n_events": int(len(g)),
                "n_non_overlap": count_non_overlap(g, h),
                "hit_rate": round(float(hit.mean()), 4) if len(hit) and bool(hit.notna().sum()) else None,
                "chosen": _stat(chosen),
                "best": _stat(best),
                "delta_best": _stat(dtb),
                "etf_vs_stock": _stat(evs),
                "combo_vs_single": _stat(cvs),
                "chosen_excess": _stat(chosen_exc),
            })
            out.append(rec)
    return pd.DataFrame(out)


def run_expression_regime(
    start_date: str,
    end_date: str,
    *,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    structure_source: str = "tier",
    themes: Sequence[str] | None = None,
    cache: dict[str, Any] | None = None,
    stock_history: dict[str, pd.DataFrame] | None = None,
) -> dict[str, Any]:
    """完整研究流程：收集事件 → 前向收益 → 分组汇总。

    Returns:
        {"events": df, "summary_by_expression": df, "summary_overall": df,
         "horizons": [...], "start_date", "end_date", "structure_source"}
    """
    events = collect_expression_events(
        start_date, end_date, structure_source=structure_source,
        themes=themes, cache=cache, stock_history=stock_history)
    if events.empty:
        logger.warning("no expression events in [%s, %s]", start_date, end_date)
        return {"events": events, "summary_by_expression": pd.DataFrame(),
                "summary_overall": pd.DataFrame(), "horizons": list(horizons),
                "start_date": start_date, "end_date": end_date,
                "structure_source": structure_source}

    book = build_price_book(cache)
    events_aug = augment_event_returns(events, book, horizons)
    by_expr = aggregate_summary(events_aug, horizons, ("expression",))
    overall = aggregate_summary(events_aug, horizons, ())
    logger.info("expression regime done: %d events, %d expr-summary rows",
                len(events_aug), len(by_expr))
    return {"events": events_aug, "summary_by_expression": by_expr,
            "summary_overall": overall, "horizons": list(horizons),
            "start_date": start_date, "end_date": end_date,
            "structure_source": structure_source}
