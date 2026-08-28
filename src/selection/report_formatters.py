"""Layer③ 报告逻辑 cell formatter 注册表（v2）。

Spec 引用 formatter 名称（列定义 `fmt` / `header_fmt`），此处注册实现。
签名：`fn(row: dict) -> str`（返回可信 HTML 片段）；header formatter 签名 `fn(row=None) -> str`。
只做「一个值怎么变成 HTML」，不做「生成哪些列」——结构归 Spec。
"""

from __future__ import annotations

from typing import Any, Callable


def _num(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):g}"
    except (TypeError, ValueError):
        return str(v)


def _pct(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.0%}"
    except (TypeError, ValueError):
        return str(v)


def _money(v: Any) -> str:
    if v is None:
        return "—"
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    if x >= 1e8:
        return f"{x / 1e8:.1f}亿"
    if x >= 1e4:
        return f"{x / 1e4:.1f}万"
    return f"{x:g}"


def _num_row(row: dict[str, Any]) -> str:
    return _num(row.get("key", row.get("rps15")))


def _pct_row(row: dict[str, Any]) -> str:
    return _pct(row.get("key"))


def _money_row(row: dict[str, Any]) -> str:
    return _money(row.get("key"))


# ── 基础数值 ─────────────────────────────────────────────────────────

def fmt_num(row: dict[str, Any]) -> str:
    v = row.get("value", row.get("key"))
    return _num(v)


def fmt_pct(row: dict[str, Any]) -> str:
    v = row.get("value", row.get("key"))
    return _pct(v)


def fmt_liquidity(row: dict[str, Any]) -> str:
    return _money(row.get("liquidity"))


def fmt_change_1d(row: dict[str, Any]) -> str:
    sc = row.get("score_change_1d")
    if sc is None:
        return "—"
    return f"{sc:+d}"


_LEVEL_RANK = {"S": 4, "A": 3, "B": 2, "C": 1}


def fmt_trajectory(row: dict[str, Any]) -> str:
    """变化：`C→B ↑ · 1d`（方向由状态串推导，如 C→B = 转好）。"""
    state_chg = str(row.get("state_change", "") or "")
    days = row.get("days_in_state")
    parts: list[str] = []
    if state_chg:
        arrow = ""
        if len(state_chg) >= 3:
            f, t = state_chg[0], state_chg[-1]
            arrow = " ↑" if _LEVEL_RANK.get(t, 0) > _LEVEL_RANK.get(f, 0) else (" ↓" if _LEVEL_RANK.get(t, 0) < _LEVEL_RANK.get(f, 0) else "")
        parts.append(f"[{state_chg}{arrow}]")
    if days:
        parts.append(f"{days}d")
    return " ".join(parts) if parts else "—"


# ── 语义 cell（v0.10 asset_state） ───────────────────────────────────

def fmt_technical(row: dict[str, Any]) -> str:
    diag = row.get("technical_diagnostics")
    if diag:
        from src.common.asset_state import technical_text
        return technical_text(diag)
    flags = row.get("risk_flags") or []
    return " · ".join(RISK_FLAG_SHORT.get(f, f) for f in flags) if flags else "—"


def fmt_blocking(row: dict[str, Any]) -> str:
    flags = row.get("blocking_flags")
    if flags:
        from src.common.asset_state import blocking_text
        return blocking_text(flags)
    return "—"


def fmt_data_quality(row: dict[str, Any]) -> str:
    flags = row.get("data_quality_flags")
    if flags:
        from src.common.asset_state import data_quality_text
        return data_quality_text(flags)
    if row.get("data_status") in ("stale", "missing"):
        return "数据滞后" if row.get("data_status") == "stale" else "数据缺失"
    return "—"


def fmt_evidence(row: dict[str, Any]) -> str:
    from src.research.baskets.stage_log import evidence_stage_cn
    stage = str(row.get("evidence_stage", "") or "")
    if not stage:
        return "—"
    rev = str(row.get("revenue_evidence", "") or "")
    return evidence_stage_cn(stage) + (" · 收入确认" if rev.upper() == "CONFIRMED" else "")


# ── 结论 / 状态 ──────────────────────────────────────────────────────

def _conclusion_tag(txt: str) -> str:
    """结论 tag（与 ETF 强度列同一套 tag 体系）：推荐强 / 合格·持有观察 / 破位弱。"""
    cls = {"推荐": "tag-strong", "合格": "tag-observe", "持有": "tag-observe",
           "观察": "tag-none", "等待": "tag-none", "破位": "tag-weak",
           "仅监控": "tag-none"}.get(txt, "tag-none")
    return f"<span class='tag {cls}'>{txt}</span>"


def _monitor_conclusion_text(row: dict[str, Any]) -> str:
    if row.get("selection_status") == "unavailable":
        return "等待"
    if row.get("participation") == "monitor_only":
        return "仅监控"
    signal = str(row.get("signal", "") or "")
    state = str(row.get("state", ""))
    if row.get("position_level") == "BREAKDOWN":
        return "破位"
    if signal in ("STRONG_BUY", "BUY"):
        return "推荐" if state == "RECOMMENDED" else "合格"
    if signal == "HOLD":
        return "持有"
    if signal == "WATCH":
        return "观察"
    if signal == "WAIT":
        if row.get("data_status") == "stale" or row.get("reason") == "剔除观察" or row.get("trend_status") == "C":
            return "等待"
        return "观察"
    if state == "RECOMMENDED":
        return "推荐"
    if state == "QUALIFIED":
        return "合格"
    if row.get("reason") == "剔除观察" or row.get("trend_status") == "C":
        return "等待"
    return "观察"


def fmt_monitor_conclusion(row: dict[str, Any]) -> str:
    return _conclusion_tag(_monitor_conclusion_text(row))


def _etf_conclusion_text(row: dict[str, Any]) -> str:
    if row.get("position_level") == "BREAKDOWN":
        return "破位"
    if row.get("recommended"):
        return "推荐"
    signal = str(row.get("signal", "") or "")
    if signal == "HOLD":
        return "持有"
    if signal == "WATCH":
        return "观察"
    return "等待"


def fmt_etf_conclusion(row: dict[str, Any]) -> str:
    return _conclusion_tag(_etf_conclusion_text(row))


def fmt_trend_heat(row: dict[str, Any]) -> str:
    """趋势：状态字母 tag + 趋势分（向 ETF 强度列对齐的 tag 体系）。"""
    score = row.get("score_trend")
    lvl = str(row.get("trend_status", "") or "")
    if score is None:
        return "—"
    if not lvl or lvl == "UNKNOWN":
        return _num(score)
    return f"<span class='tag {_trend_tag(row)}'>{lvl}</span> · {_num(score)}"


def _trend_tag(row: dict[str, Any]) -> str:
    score = row.get("score_trend")
    lvl = str(row.get("trend_status", "") or "")
    if lvl == "S" or (lvl == "A" and (score is None or score >= 70)):
        return "tag-strong"
    if lvl == "B":
        return "tag-observe"
    if lvl == "C" or row.get("risk_flags") or row.get("risk_gate_passed") is False:
        return "tag-weak"
    return "tag-none"


def fmt_position_level(row: dict[str, Any]) -> str:
    level = str(row.get("position_level", "") or "")
    cls = {"BREAKDOWN": "color:#C62828;font-weight:600", "HIGH": "color:#E65100",
           "LOW": "color:#1565C0", "MID": "color:var(--zh-text)", "UNKNOWN": ""}.get(level, "")
    txt = {"BREAKDOWN": "破位", "HIGH": "高位", "LOW": "低位", "MID": "中性",
           "UNKNOWN": "未知"}.get(level, level)
    return f"<span style='{cls}'>{txt}</span>" if cls else txt


def _position_metric() -> str:
    try:
        from src.common.spec.loaders import load_stock_selection_spec
        return load_stock_selection_spec().historical_position.metric
    except Exception:
        return "ma60_deviation"


def fmt_position_pct(row: dict[str, Any]) -> str:
    v = row.get("position_pct")
    if v is None:
        return "—"
    try:
        return f"{float(v):g}%"
    except (TypeError, ValueError):
        return str(v)


def fmt_position_header(row: dict[str, Any] | None = None) -> str:
    return "偏离MA60%" if _position_metric() == "ma60_deviation" else "位置分位"


def fmt_leadership(row: dict[str, Any]) -> str:
    """主题内地位的人类语义（展示层）：机器码 LEADER/CORE/NON_CORE 转可读。

    ETF 的 CORE = 卫星/细分产品地位（rank≤satellite_rank_max），
    直接显示 "CORE" 会被误读为「结构核心 ETF」，故展示为「细分核心」。
    底层 leadership_level 机器码不变。
    """
    lvl = str(row.get("leadership_level", "") or "")
    if lvl == "LEADER":
        return "龙头"
    if lvl == "CORE":
        return "细分核心" if str(row.get("asset_type", "") or "") == "etf" else "核心"
    if lvl == "NON_CORE":
        return "非核心"
    return "—" if not lvl else lvl


# ── ETF 状态 ─────────────────────────────────────────────────────────

ETF_TREND_STATUS_CN = {
    "BUY_CANDIDATE": "趋势达标", "STRONG_WATCH": "趋势达标", "WATCH": "观察",
    "BELOW_TREND_GATE": "未达趋势门", "OUT_OF_SCOPE": "未达趋势门", "UNKNOWN": "—",
}
TRADE_STATE_CN = {"STRONG_BUY": "买入", "BUY": "买入", "HOLD": "持有",
                  "WATCH": "观察", "WAIT": "等待"}


def fmt_etf_trend_status(row: dict[str, Any]) -> str:
    ts = str(row.get("trend_status", "") or "")
    return ETF_TREND_STATUS_CN.get(ts, "—" if not ts else ts)


def fmt_trade_state(row: dict[str, Any]) -> str:
    signal = str(row.get("signal", "") or "")
    return TRADE_STATE_CN.get(signal, "—" if not signal else signal)


# ── 展示状态 tag（02 矩阵 状态 列） ──────────────────────────────────

DISPLAY_STATE_TAG = {
    "degraded": ("🟡 已确认 · 降级", "tag-weak"),
    "changed": ("🟡 已确认 · 有变化", "tag-observe"),
    "actionable": ("🟢 已确认", "tag-strong"),
    "watch": ("⚪ 观察", "tag-none"),
    "normal": ("🟢 已确认", "tag-strong"),
}


def fmt_display_state_tag(row: dict[str, Any]) -> str:
    ds = str(row.get("display_state", "normal"))
    text, cls = DISPLAY_STATE_TAG.get(ds, (ds, "tag-none"))
    return f"<span class='tag {cls}'>{text}</span>"


# ── 06 审计分类（摘要计数 ↔ 列表行 一一对应） ─────────────────────────

AUDIT_CATEGORY_CN = {
    "breakdown": "破位", "blocked": "阻塞", "qualified_unselected": "合格未选",
    "recommended": "推荐", "normal": "正常观察", "monitor_only": "仅监控",
    "dynamic_watch": "动态观察",
}
AUDIT_CATEGORY_TAG = {
    "breakdown": "tag-weak", "blocked": "tag-weak", "qualified_unselected": "tag-observe",
    "recommended": "tag-strong", "normal": "tag-none", "monitor_only": "tag-none",
    "dynamic_watch": "tag-none",
}


def fmt_audit_category(row: dict[str, Any]) -> str:
    """审计分类 tag：把摘要计数标记到每一行，保证摘要 ↔ 列表一一对应。"""
    cat = str(row.get("_audit_category", "") or "")
    label = AUDIT_CATEGORY_CN.get(cat, cat)
    cls = AUDIT_CATEGORY_TAG.get(cat, "tag-none")
    return f"<span class='tag {cls}'>{label}</span>"


# ── 06 决策审计：第一层（Decision Audit） ───────────────────────────

ROLE_SHORT = {
    "LEADER": "龙头", "HIGH_BETA": "高弹性", "UPSTREAM": "设备与上游",
    "CORE_ETF": "核心ETF", "SUB_INDUSTRY_ETF": "细分ETF",
}


def fmt_theme_role(row: dict[str, Any]) -> str:
    """主题 / 角色：`高现金流 · 龙头`（role 参与候选结构判断，tier_label 下沉详情）。"""
    theme = str(row.get("_theme_label") or row.get("theme_label") or row.get("theme") or "")
    role = str(row.get("role", "") or "")
    label = ROLE_SHORT.get(role, role)
    if theme and label:
        return f"{theme} · {label}"
    return theme or label or "—"


def fmt_participation(row: dict[str, Any]) -> str:
    """参与属性（provenance）：MONITOR_ONLY 显式标注，不抢风险主分类。"""
    return "仅监控" if str(row.get("participation", "") or "") == "monitor_only" else "—"


def fmt_audit_reason(row: dict[str, Any]) -> str:
    """「为什么」= 当前结论的首要原因（非「未推荐原因」）。"""
    if row.get("selection_status") == "unavailable":
        return "数据缺失"
    if row.get("participation") == "monitor_only":
        return "不参与交易候选"
    if row.get("recommended"):
        return "—"
    signal = str(row.get("signal", "") or "")
    state = str(row.get("state", ""))
    if row.get("position_level") == "BREAKDOWN":
        pct = row.get("position_pct")
        pct_txt = "—" if pct is None else f"{float(pct):g}%"
        return f"中期破位 · MA60 {pct_txt}"
    if signal == "HOLD":
        return "高位不追"
    if signal == "WATCH":
        lead = str(row.get("leadership_level", "") or "")
        pos = str(row.get("position_level", "") or "")
        return f"{lead}×{pos}，仅观察" if lead and pos else "仅观察"
    flags = row.get("blocking_flags") or []
    if flags:
        from src.common.asset_state import blocking_text
        return blocking_text(flags)
    if state == "QUALIFIED":
        return "排名/角色未进入推荐"
    if row.get("data_status") == "stale":
        return "数据滞后"
    return str(row.get("reason", "") or "") or "未达趋势门"


# ── 06 决策审计：ETF（决策链 = 强度 → 趋势门 → 位置 → 流动性 → 排名） ──

ETF_ROLE_CN = {"CORE_ETF": "核心ETF", "SUB_INDUSTRY_ETF": "卫星ETF"}


def fmt_etf_theme_role(row: dict[str, Any]) -> str:
    """主题 / 角色（ETF 化）：核心/卫星产品 vs 动态观察。"""
    theme = str(row.get("_theme_label") or row.get("theme_label") or row.get("theme") or "")
    if str(row.get("monitoring_source", "") or "") == "watchlist":
        label = "动态观察"
    else:
        role = str(row.get("role", "") or "")
        label = ETF_ROLE_CN.get(role, role)
    if theme and label:
        return f"{theme} · {label}"
    return theme or label or "—"


def fmt_etf_audit_reason(row: dict[str, Any]) -> str:
    """ETF「为什么」= 当前结论的首要原因（含 ETF 特有分支：同主题有更优产品）。"""
    if row.get("selection_status") == "unavailable":
        return "数据缺失"
    if row.get("recommended"):
        return "—"
    if row.get("position_level") == "BREAKDOWN":
        pct = row.get("position_pct")
        pct_txt = "—" if pct is None else f"{float(pct):g}%"
        return f"中期破位 · MA60 {pct_txt}"
    signal = str(row.get("signal", "") or "")
    if signal == "HOLD":
        return "高位不追"
    if signal == "WATCH":
        return "仅观察"
    flags = [str(f) for f in (row.get("blocking_flags") or [])]
    if "LOW_LIQUIDITY" in flags:
        return "成交额不足"
    if "DEDUP_LOST" in flags:
        return "同主题有更优产品"
    if "POSITION_HIGH" in flags:
        return "高位不追"
    if "BELOW_TREND_GATE" in flags or str(row.get("trend_status", "") or "") == "BELOW_TREND_GATE":
        r15 = row.get("rps15")
        return f"RPS15 {float(r15):g} · 未达趋势门" if r15 is not None else "未达趋势门"
    # 趋势达标但未入选：排名未进入核心产品
    if str(row.get("state", "")) == "RECOMMENDED" and not row.get("recommended"):
        rank = row.get("theme_rank")
        return f"同主题排名第 {rank}，未进入核心产品" if rank is not None else "排名未进入核心产品"
    if str(row.get("monitoring_source", "") or "") == "watchlist":
        return "动态观察，不在主题核心产品池"
    rr = str(row.get("reject_reason", "") or "")
    if rr:
        return rr
    return "未达趋势门"


def fmt_etf_strength(row: dict[str, Any]) -> str:
    """强度：RPS15 + 趋势状态（按 gate 着色）。"""
    r15 = row.get("rps15")
    rtxt = "—" if r15 is None else f"{float(r15):g}"
    ts = str(row.get("trend_status", "") or "")
    cn = ETF_TREND_STATUS_CN.get(ts, "")
    if cn and cn != "—":
        cls = {"趋势达标": "tag-strong", "观察": "tag-observe", "未达趋势门": "tag-none"}.get(cn, "tag-none")
        return f"RPS15 {rtxt} <span class='tag {cls}'>{cn}</span>"
    return f"RPS15 {rtxt}"


def fmt_etf_position(row: dict[str, Any]) -> str:
    """位置：位置等级 + 乖离%（破位红色强调）。"""
    level = str(row.get("position_level", "") or "")
    if level == "BREAKDOWN":
        return "<span style='color:#C62828;font-weight:600'>破位</span>"
    txt = {"HIGH": "高位", "LOW": "低位", "MID": "中性", "UNKNOWN": "未知"}.get(level, level)
    pct = row.get("position_pct")
    pct_txt = "—" if pct is None else f"{float(pct):g}%"
    return f"{txt} · {pct_txt}"


# ── 注册表 ───────────────────────────────────────────────────────────

RISK_FLAG_SHORT = {
    "跌破MA20": "MA20↓", "跌破MA60": "MA60↓", "跌破MA120": "MA120↓",
    "MACD转弱": "MACD弱", "RS为负": "RS负", "RSI偏热": "RSI热", "RSI偏弱": "RSI弱",
}

FORMATTERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "num": fmt_num,
    "pct": fmt_pct,
    "liquidity": fmt_liquidity,
    "change_1d": fmt_change_1d,
    "trajectory": fmt_trajectory,
    "technical": fmt_technical,
    "blocking": fmt_blocking,
    "data_quality": fmt_data_quality,
    "evidence": fmt_evidence,
    "monitor_conclusion": fmt_monitor_conclusion,
    "etf_conclusion": fmt_etf_conclusion,
    "trend_heat": fmt_trend_heat,
    "position_level": fmt_position_level,
    "position_pct": fmt_position_pct,
    "etf_trend_status": fmt_etf_trend_status,
    "trade_state": fmt_trade_state,
    "display_state_tag": fmt_display_state_tag,
    "audit_category": fmt_audit_category,
    "theme_role": fmt_theme_role,
    "participation": fmt_participation,
    "audit_reason": fmt_audit_reason,
    "etf_theme_role": fmt_etf_theme_role,
    "etf_audit_reason": fmt_etf_audit_reason,
    "etf_strength": fmt_etf_strength,
    "etf_position": fmt_etf_position,
    "leadership": fmt_leadership,
}

HEADER_FORMATTERS: dict[str, Callable[[Any], str]] = {
    "position_header": fmt_position_header,
}
