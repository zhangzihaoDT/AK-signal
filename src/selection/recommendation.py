"""
Layer ③ — Investment Recommendation Builder（v0.5.0）

定位：Selection Engine（selection.py，纯算法，制造事实+Policy）之上的纯排版层。
职责：把 build_candidates() 输出的候选对象按「投资决策顺序」重组为推荐结构——
      每主题 5 块：① 今天怎么做 → ② 为什么 → ③ 买什么 → ④ 为什么选它 → ⑤ 观察。

铁律：本层不制造任何新事实。不重算 RPS/趋势分、不联网、不做新筛选；
      只读取引擎已落盘的字段并按决策顺序重新组织/格式化。
      ETF RPS（相对全市场横截面）与行业 RPS（相对 124 申万横截面）标尺不同，不混用。
"""

from __future__ import annotations

from typing import Any

RECOMMENDATION_VERSION = "0.5.0"

ACTION_LABELS = {"BUY": "买入", "OBSERVE": "观察", "WAIT": "等待"}


def _num(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return round(float(v), 1)
    except (TypeError, ValueError):
        return None


def _etf_reject_reason(a: dict[str, Any]) -> str:
    """ETF 未推荐原因（Policy 标注，基于引擎 reason_codes）。"""
    codes = a.get("reason_codes") or []
    if "dedup_lost" in codes:
        return "同类方向已有代表入选"
    if "below_trend_gate" in codes and "low_liquidity" in codes:
        return "未达趋势门且流动性不足"
    if "below_trend_gate" in codes:
        return "未达趋势门"
    if "low_liquidity" in codes:
        return "流动性不足"
    if not a.get("recommended"):
        return "排名未进推荐"
    return ""


def _stock_reject_reason(a: dict[str, Any]) -> str:
    """个股未推荐原因（Policy 标注，基于既有状态/风险字段 + 四段信号）。"""
    codes = a.get("reason_codes") or []
    if a.get("selection_status") == "unavailable":
        return "数据缺失"
    if "stale_data" in codes:
        return "数据滞后，信号降级"
    flags = a.get("risk_flags") or []
    if "risk_warning" in codes or (a.get("risk_gate_passed") is False and flags):
        return "风险警戒" + (f"（{'、'.join(flags)}）" if flags else "")
    # v0.9.0 四段信号：趋势/主题都过但信号是 HOLD/WATCH → 解释为什么未推荐
    if a.get("state") == "RECOMMENDED" and not a.get("recommended"):
        if a.get("position_level") == "BREAKDOWN":
            return _breakdown_reject_reason(a)
        if a.get("signal") == "HOLD":
            return _hold_reject_reason(a)
        if a.get("signal") == "WATCH":
            return "主题内非龙头/非核心，或位置中性，暂不买入"
        if a.get("signal"):
            return f"信号 {a.get('signal')}，暂不买入"
    if a.get("state") == "QUALIFIED":
        return "趋势合格，待主题确认"
    if a.get("state") == "WATCH":
        return "未达趋势资格"
    return a.get("reason", "") or ""


def _position_metric_label() -> str:
    """位置指标的人类可读标签（展示层，不改变事实）。"""
    try:
        from src.common.spec.loaders import load_stock_selection_spec
        metric = load_stock_selection_spec().historical_position.metric
        return "60 日线乖离" if metric == "ma60_deviation" else "历史价格分位"
    except Exception:
        return "历史位置"


def _hold_reject_reason(a: dict[str, Any]) -> str:
    """HOLD 未推荐原因：追高不买（position_pct 为分位或乖离率，措辞按 metric 区分）。"""
    pct = a.get("position_pct")
    pct_txt = "—" if pct is None else f"{pct:g}%"
    if _position_metric_label() == "60 日线乖离":
        return f"现价高于 60 日线 {pct_txt}，追高不买"
    return f"历史高位（{pct_txt} 分位），持有不追高"


def _breakdown_reject_reason(a: dict[str, Any]) -> str:
    """BREAKDOWN 未推荐原因：现价深破 60 日线，中期趋势破坏。"""
    pct = a.get("position_pct")
    pct_txt = "—" if pct is None else f"{abs(pct):g}%"
    return f"现价低于 60 日线 {pct_txt}，中期趋势破坏，暂不买入"


def _stock_short_reason(a: dict[str, Any]) -> str:
    """个股观察池短原因（如「跌破MA20」「等待突破」）。"""
    r = a.get("reason", "") or ""
    flags = a.get("risk_flags") or []
    if r in ("风险警戒", "剔除观察"):
        return "、".join(flags) if flags else "风险警戒"
    return r


def _etf_watch_entry(a: dict[str, Any]) -> dict[str, Any]:
    """观察池 ETF：附未推荐原因，不改变事实字段。"""
    entry = dict(a)
    entry["reject_reason"] = _etf_reject_reason(a)
    return entry


def _stock_watch_entry(a: dict[str, Any]) -> dict[str, Any]:
    """观察池个股：附未推荐原因，不改变事实字段。"""
    entry = dict(a)
    entry["reject_reason"] = _stock_reject_reason(a)
    return entry


def _rationale_etf(a: dict[str, Any]) -> dict[str, Any]:
    """④ 为什么选它（ETF）：核心选择依据 = RPS15（相对全市场横截面）+ 流动性。"""
    return {
        "code": a.get("code", ""),
        "name": a.get("name", ""),
        "rps15": _num(a.get("rps15")),
        "rps20": _num(a.get("rps20")),
        "liquidity": a.get("liquidity"),
        "return_5d": _num(a.get("return_5d")),
        "return_20d": _num(a.get("return_20d")),
        "rank_change_5d": _num(a.get("rank_change_5d")),
        "reason": a.get("reason", ""),
    }


def _rationale_stock(a: dict[str, Any]) -> dict[str, Any]:
    """④ 为什么选它（个股）：核心选择依据 = 趋势分（0-100 绝对技术）。"""
    return {
        "code": a.get("code", ""),
        "name": a.get("name", ""),
        "score_trend": _num(a.get("score_trend")),
        "trend_status": a.get("trend_status", ""),
        "days_in_state": a.get("days_in_state"),
        "state_change": a.get("state_change", ""),
        "reason": a.get("reason", ""),
    }


def _today_block(theme_obj: dict[str, Any], top_action: dict[str, Any]) -> dict[str, Any]:
    """① 今天怎么做：action（BUY/OBSERVE/WAIT）+ 表达方式 + 确认状态。"""
    confirmed = theme_obj.get("confirmed", False)
    expr = theme_obj.get("expression", "")
    if not confirmed:
        action = "WAIT"
        action_label = ACTION_LABELS.get(action, action)
        summary = "主题未确认，今日不建仓，仅观察"
    else:
        action = "BUY" if expr not in ("WATCHLIST_ONLY",) else "OBSERVE"
        action_label = ACTION_LABELS.get(action, action)
        summary = f"{theme_obj.get('confirmation_breadth', '')}，{theme_obj.get('expression_label', '')}"
    return {
        "action": action,
        "action_label": action_label,
        "expression": expr,
        "expression_label": theme_obj.get("expression_label", ""),
        # 表达可执行性（observability）：结构表达 ≠ 可执行表达
        "structural_expression": theme_obj.get("structural_expression", expr),
        "execution_expression": theme_obj.get("execution_expression", expr),
        "expression_status": theme_obj.get("expression_status", ""),
        "fallback_reason": theme_obj.get("fallback_reason", ""),
        "eligible_etf_count": theme_obj.get("eligible_etf_count", 0),
        "eligible_stock_count": theme_obj.get("eligible_stock_count", 0),
        "etf_pool_total": theme_obj.get("etf_pool_total", 0),
        "confirmation_state": theme_obj.get("confirmation_state", ""),
        "confirmation_breadth": theme_obj.get("confirmation_breadth", ""),
        "stage": theme_obj.get("stage", ""),
        "summary": summary,
        # 顶层行动建议（方向级），供阅读；标的不下渗
        "top_action": {
            "level": top_action.get("level", ""),
            "summary": top_action.get("summary", ""),
        },
    }


def _why_block(theme_obj: dict[str, Any]) -> dict[str, Any]:
    """② 为什么：确认证据 + 上涨结构 + 表达理由。"""
    m = theme_obj.get("metrics", {})
    why: dict[str, Any] = {
        "confirmed": theme_obj.get("confirmed", False),
        "n_observe": m.get("n_observe", 0),
        "n_watch": m.get("n_watch", 0),
        "n_total": m.get("n_total", 0),
        "n_strong": m.get("n_strong", 0),
        "observing_industries": theme_obj.get("observing_industries", []),
        "median_participation": m.get("median_participation"),
        "median_hhi": m.get("median_hhi"),
        "median_top3_share": m.get("median_top3_share"),
        "median_rps15": m.get("median_rps15"),
        "strongest_industry_rps15": m.get("strongest_industry_rps15"),
        "etf_median_rps15": m.get("etf_median_rps15"),
        "confirm_evidence": theme_obj.get("confirm_evidence", {}),
        "confirmation_reason": theme_obj.get("confirmation_reason", ""),
    }
    if not theme_obj.get("confirmed", False):
        # 未确认：给出「还差多少」的距离口径（行业真实确认门 vs ETF 自身强势门槛分开）
        why["distance_to_industry_confirm"] = theme_obj.get("distance_to_industry_confirm")
        why["distance_to_etf_strength"] = theme_obj.get("distance_to_etf_strength")
        why["strongest_etf"] = theme_obj.get("strongest_etf")
    else:
        why["expression_reason"] = theme_obj.get("expression_reason", "")
    return why


def _recommendation_block(theme_obj: dict[str, Any]) -> dict[str, Any]:
    """③ 买什么：今天真正可操作的内容（推荐 ETF + 推荐个股）。"""
    core = theme_obj.get("core_etf", [])
    sub = theme_obj.get("sub_industry_etf", [])
    stocks = theme_obj.get("stock_candidates", [])
    etf_recs = [a for a in (core + sub) if a.get("recommended")]
    stock_recs = [a for a in stocks if a.get("recommended")]
    return {
        "expression": theme_obj.get("expression", ""),
        "expression_label": theme_obj.get("expression_label", ""),
        "expression_reason": theme_obj.get("expression_reason", ""),
        "etf": etf_recs,
        "stocks": stock_recs,
        "primary_etf": theme_obj.get("primary_etf", []),
        "primary_stock": theme_obj.get("primary_stock", []),
    }


def _rationale_block(theme_obj: dict[str, Any]) -> dict[str, Any]:
    """④ 为什么选它：逐推荐资产给出选择依据（ETF=横截面 RPS15+流动性；个股=趋势分）。"""
    rec = _recommendation_block(theme_obj)
    return {
        "etf": [_rationale_etf(a) for a in rec["etf"]],
        "stocks": [_rationale_stock(a) for a in rec["stocks"]],
    }


def _watchlist_block(theme_obj: dict[str, Any], max_etf: int = 6, max_stocks: int = 8) -> dict[str, Any]:
    """⑤ 观察：还有哪些值得观察 + 为什么没进推荐。

    ETF 来自引擎 etf_pool（全部关键词命中），排除已推荐后按 selection_score 取 top N；
    个股来自 stock_watchlist 全量，排除已推荐后保留 WATCH/QUALIFIED 及降级项。
    """
    rec_codes = {a.get("code") for a in theme_obj.get("core_etf", []) + theme_obj.get("sub_industry_etf", [])}
    pool = theme_obj.get("etf_pool", [])
    watch_etf = [
        _etf_watch_entry(a) for a in pool
        if a.get("code") not in rec_codes and not a.get("recommended")
    ]
    watch_etf.sort(key=lambda a: -(a.get("selection_score") if a.get("selection_score") is not None else -1e9))
    watch_etf = watch_etf[:max_etf]

    wl = theme_obj.get("stock_watchlist", {})
    all_stocks = [a for tier in ("leaders", "high_beta", "equipment") for a in wl.get(tier, [])]
    stock_rec_codes = {a.get("code") for a in theme_obj.get("stock_candidates", []) if a.get("recommended")}
    watch_stocks = [
        _stock_watch_entry(a) for a in all_stocks
        if a.get("code") not in stock_rec_codes and not a.get("recommended")
    ]
    # 观察池排序：趋势合格 > 风险警戒（有原因可读）> 未达标；同组按趋势分降序
    watch_stocks.sort(key=lambda a: (
        a.get("state") != "QUALIFIED",
        a.get("state") != "WATCH",
        -(a.get("score_trend") if a.get("score_trend") is not None else -1e9),
        a.get("code", ""),
    ))
    watch_stocks = watch_stocks[:max_stocks]
    return {"etf": watch_etf, "stocks": watch_stocks}


def _etf_monitoring_block(theme_obj: dict[str, Any], max_watch: int = 6) -> list[dict[str, Any]]:
    """附录 ETF 监控：推荐 ETF + 主题动态观察 ETF，不混入个股监控表。"""
    core = theme_obj.get("core_etf", [])
    sub = theme_obj.get("sub_industry_etf", [])
    rec_codes = {a.get("code") for a in core + sub}
    out: list[dict[str, Any]] = []
    for a in core + sub:
        out.append({**a, "monitoring_source": "recommendation", "reject_reason": ""})
    for a in _watchlist_block(theme_obj, max_etf=max_watch, max_stocks=0)["etf"]:
        if a.get("code") not in rec_codes:
            out.append({**a, "monitoring_source": "watchlist"})
    return out


def _theme_recommendation(theme_obj: dict[str, Any], top_action: dict[str, Any]) -> dict[str, Any]:
    """单主题推荐对象：5 块结构，不制造新事实。"""
    return {
        "theme": theme_obj.get("theme", ""),
        "theme_label": theme_obj.get("theme_label", ""),
        "bucket": theme_obj.get("bucket", ""),
        "bucket_label": theme_obj.get("bucket_label", ""),
        "objective": theme_obj.get("objective", ""),
        "confirmed": theme_obj.get("confirmed", False),
        "today": _today_block(theme_obj, top_action),
        "why": _why_block(theme_obj),
        "recommendation": _recommendation_block(theme_obj),
        "rationale": _rationale_block(theme_obj),
        "watchlist": _watchlist_block(theme_obj),
        "etf_monitoring": _etf_monitoring_block(theme_obj),
        "monitoring": theme_obj.get("stock_watchlist", {}),
        # 附录/概览透传：最强 ETF 摘要（引擎事实原值）
        "strongest_etf": theme_obj.get("strongest_etf"),
        "distance_to_industry_confirm": theme_obj.get("distance_to_industry_confirm"),
        "distance_to_etf_strength": theme_obj.get("distance_to_etf_strength"),
        # 表达可执行性（observability）：结构表达 → 可执行表达 → 降级原因
        "structural_expression": theme_obj.get("structural_expression", theme_obj.get("expression", "")),
        "execution_expression": theme_obj.get("execution_expression", theme_obj.get("expression", "")),
        "expression_status": theme_obj.get("expression_status", ""),
        "fallback_reason": theme_obj.get("fallback_reason", ""),
        "eligible_etf_count": theme_obj.get("eligible_etf_count", 0),
        "eligible_stock_count": theme_obj.get("eligible_stock_count", 0),
        "etf_pool_total": theme_obj.get("etf_pool_total", 0),
    }


def build_recommendation(engine: dict[str, Any]) -> dict[str, Any]:
    """把 Selection Engine 输出重组为「投资推荐」结构（纯排版，不产生新事实）。

    engine = build_candidates() 的返回值（layer3 候选对象）。
    顶层保留 action（BUY/OBSERVE/WAIT，方向级）供 final-validation 等消费；
    每主题内部按 ①今天怎么做 → ②为什么 → ③买什么 → ④为什么选它 → ⑤观察 组织。
    """
    top_action = engine.get("action", {}) or {}
    buckets: list[dict[str, Any]] = []
    for bucket_obj in engine.get("buckets", []):
        themes = [_theme_recommendation(t, top_action) for t in bucket_obj.get("themes", [])]
        buckets.append({
            "bucket": bucket_obj.get("bucket", ""),
            "bucket_label": bucket_obj.get("bucket_label", ""),
            "objective": bucket_obj.get("objective", ""),
            "n_themes": bucket_obj.get("n_themes", len(themes)),
            "n_confirmed": sum(1 for t in themes if t.get("confirmed")),
            "themes": themes,
        })
    return {
        "version": RECOMMENDATION_VERSION,
        "role": "recommendation",
        "engine": {
            "version": engine.get("version", ""),
            "module": "selection.build_candidates",
        },
        "direction": engine.get("direction", {}),
        "action": top_action,
        "summary": engine.get("summary", {}),
        "closest_theme": engine.get("closest_theme"),
        "recommended_actions": engine.get("recommended_actions", []),
        "buckets": buckets,
    }
