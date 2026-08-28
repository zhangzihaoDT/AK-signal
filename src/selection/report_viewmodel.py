"""Layer③ 报告 ReportViewModel（纯内存展示层，v2）。

定位：把 recommendation JSON（决策 contract）+ previous JSON（跨日）+ 变化日志，
组装成「为人阅读服务」的展示模型。**绝不写回 recommendation 对象**。

派生字段（classification / display_state / narrative / execution / change / one_liner）
全部只存在于本模块的对象里，核心 JSON contract 零污染。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .report_changes import ChangeEntry, build_changes

ACTION_LABELS = {"BUY": "买入", "OBSERVE": "观察", "WAIT": "等待"}

# 口径说明（恒定展示文案，非决策）
CALIBRE_TEXT = (
    "ETF 的 RPS15 是相对全市场 ETF 横截面的百分位（Layer① rotation）；"
    "行业的 RPS15 是相对 124 个申万二级行业横截面的百分位（Layer②）。两者标尺不同，不可直接对比。"
    "主题确认 = 任一焦点行业 RPS15 达 Layer② 观察门槛（存在性判定）；"
    "ETF 候选按 ETF 自身动量（趋势门）+ 主题确认选出，不要求 ETF 对应行业也确认。"
    "本报告展示的 RPS/趋势分是 Layer①/② 的事实（原值保留）；「推荐 / 观察 / 未推荐原因」是 Layer③ 的策略决策。"
)


@dataclass
class ReportClassification:
    """多标签事实：一个主题可同时 actionable + changed + degraded（不互斥）。"""
    actionable: bool = False
    changed: bool = False
    degraded: bool = False
    watch: bool = False

    def display_state(self, priority: list[str]) -> str:
        for label in priority:
            if label in ("normal", "confirmed"):
                continue
            if getattr(self, label, False):
                return label
        return "normal"


@dataclass
class ThemeState:
    theme: str
    theme_label: str
    bucket_label: str
    confirmed: bool
    classification: ReportClassification
    display_state: str
    action: str
    primary: str
    change: str
    theme_obj: dict[str, Any]


@dataclass
class Narrative:
    theme_label: str
    display_state: str
    title: str
    argument: str
    evidence: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class ExecutionCard:
    theme_label: str
    display_state: str
    mode: str                      # compact / expanded
    primary: list[dict[str, Any]]
    alternatives: list[dict[str, Any]]
    not_consider: list[dict[str, Any]]
    note: str = ""


@dataclass
class ReportViewModel:
    selection_date: str
    action: dict[str, Any]
    one_liner: str
    actionable: list[dict[str, Any]]
    themes: list[ThemeState]
    narratives: list[Narrative]
    execution: list[ExecutionCard]
    changes: list[ChangeEntry]
    comparison_status: str
    audit_stock: list[dict[str, Any]]
    audit_etf: list[dict[str, Any]]
    audit_summary: list[tuple[str, str, int]]
    audit_etf_summary: list[tuple[str, str, int]]
    etf_product_availability: list[tuple[str, int, int]]
    audit_calibre: str
    meta: dict[str, Any]


def _meaningful_blocks(row: dict[str, Any]) -> list[str]:
    """「有意义的阻塞」：排除纯 BELOW_TREND_GATE（未达趋势门是常态门控，非异常）。

    摘要分类的「阻塞」= 真正值得审计的阻塞（风险警戒/流动性/破位/信号等）；
    仅 below_trend_gate 的 WATCH 资产归「正常观察」，避免摘要被常态门控淹没。
    """
    flags = [str(f) for f in (row.get("blocking_flags") or [])]
    return [f for f in flags if f != "BELOW_TREND_GATE"]


# ETF 特有：物料阻塞集合（排除纯 BELOW_TREND_GATE 常态门控、DEDUP_LOST 去重、SIGNAL_WATCH 观察态）
_ETF_MATERIAL_BLOCKS = {"LOW_LIQUIDITY", "POSITION_HIGH", "RISK_WARNING"}
_ETF_TREND_GATES = {"BUY_CANDIDATE", "STRONG_WATCH", "WATCH"}


def _etf_audit_category(row: dict[str, Any]) -> str:
    """ETF 审计分类（单标签互斥，ETF 特有，不复用个股分类器）。

    BREAKDOWN → BLOCKED（物料阻塞）→ QUALIFIED_UNSELECTED（趋势达标未入选）→
    RECOMMENDED → NORMAL（核心/卫星产品在决策池）→ DYNAMIC_WATCH（动态关键词匹配观察）。
    """
    if row.get("position_level") == "BREAKDOWN":
        return "breakdown"
    flags = [str(f) for f in (row.get("blocking_flags") or [])]
    if any(f in _ETF_MATERIAL_BLOCKS for f in flags):
        return "blocked"
    if str(row.get("trend_status", "") or "") in _ETF_TREND_GATES and not row.get("recommended"):
        return "qualified_unselected"
    if row.get("recommended"):
        return "recommended"
    if str(row.get("monitoring_source", "") or "") == "recommendation":
        return "normal"
    return "dynamic_watch"


def _audit_category(row: dict[str, Any]) -> str:
    """审计分类（单标签互斥，求和 = 总行数）。

    表示「当前最值得审计的状态」，不是资产来源身份：
      BREAKDOWN → BLOCKED（有意义阻塞）→ QUALIFIED_UNSELECTED → RECOMMENDED → NORMAL → MONITOR_ONLY
    monitor_only 是 provenance，不覆盖风险状态（monitor-only 且破位 → 归 breakdown）。
    """
    if row.get("position_level") == "BREAKDOWN":
        return "breakdown"
    if _meaningful_blocks(row):
        return "blocked"
    if row.get("state") == "QUALIFIED":
        return "qualified_unselected"
    if row.get("recommended") or row.get("state") == "RECOMMENDED":
        return "recommended"
    if str(row.get("participation", "") or "") != "monitor_only":
        return "normal"
    return "monitor_only"


def _flatten_themes(recommendation: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for b in recommendation.get("buckets", []):
        for t in b.get("themes", []):
            out.append((str(b.get("bucket_label", "")), t))
    return out


def _code_theme_map(recommendation: dict[str, Any]) -> dict[str, str]:
    """code → theme_label（供 recommended_actions 补充主题归属）。"""
    m: dict[str, str] = {}
    for _, t in _flatten_themes(recommendation):
        label = t.get("theme_label", "")
        for a in t.get("recommendation", {}).get("etf", []) + t.get("recommendation", {}).get("stocks", []):
            m.setdefault(str(a.get("code", "")), label)
        for a in (t.get("etf_monitoring") or []):
            m.setdefault(str(a.get("code", "")), label)
        for tier in ("leaders", "high_beta", "equipment"):
            for a in t.get("monitoring", {}).get(tier, []):
                m.setdefault(str(a.get("code", "")), label)
    return m


def classify_theme(theme: dict[str, Any], *, changed: bool = False) -> ReportClassification:
    confirmed = bool(theme.get("confirmed", False))
    status = str(theme.get("expression_status", "") or "")
    structural = str(theme.get("structural_expression", "") or "")
    execution = str(theme.get("execution_expression", "") or "")
    degraded = status == "DEGRADED" or bool(structural and execution and structural != execution)
    rec = theme.get("recommendation", {})
    assets = rec.get("etf", []) + rec.get("stocks", [])
    actionable = confirmed and any(a.get("recommended") for a in assets)
    return ReportClassification(
        actionable=actionable, changed=changed, degraded=degraded, watch=not confirmed)


def _primary_asset(theme: dict[str, Any]) -> dict[str, Any]:
    rec = theme.get("recommendation", {})
    for a in rec.get("etf", []):
        if a.get("recommended"):
            return a
    for a in rec.get("stocks", []):
        if a.get("recommended"):
            return a
    return {}


def _primary_name(theme: dict[str, Any]) -> str:
    a = _primary_asset(theme)
    return str(a.get("name", "")) if a else "—"


def _theme_change_brief(theme: dict[str, Any], classification: ReportClassification) -> str:
    if classification.degraded:
        return "表达降级"
    if classification.watch:
        return "观察"
    if classification.actionable:
        return "可交易"
    return "—"


def _one_liner(themes: list[ThemeState], action: dict[str, Any]) -> str:
    parts: list[str] = []
    for t in themes:
        if t.classification.degraded:
            parts.append(f"{t.theme_label} 出现表达降级（结构 {t.theme_obj.get('structural_expression','')} → 实际 {t.theme_obj.get('execution_expression','')}）")
        elif t.classification.watch:
            d = t.theme_obj.get("distance_to_industry_confirm")
            parts.append(f"{t.theme_label} 暂不加仓（观察）" if d is None else f"{t.theme_label} 暂不加仓（观察，差 {d}）")
        elif t.confirmed:
            parts.append(f"{t.theme_label} 继续可交易")
    judgment = f"今日判断：{ACTION_LABELS.get(str(action.get('level', 'WAIT')), str(action.get('level', '')))}"
    if parts:
        return f"{judgment} · " + "；".join(parts)
    return judgment


def _execution_card(theme: dict[str, Any], tstate: ThemeState, labels: dict[str, str]) -> ExecutionCard:
    rec = theme.get("recommendation", {})
    primary = [a for a in rec.get("etf", []) + rec.get("stocks", []) if a.get("recommended")]
    alternatives: list[dict[str, Any]] = []
    not_consider: list[dict[str, Any]] = []
    watch_etf = theme.get("watchlist", {}).get("etf", [])
    watch_stocks = theme.get("watchlist", {}).get("stocks", [])
    rec_codes = {a.get("code") for a in primary}
    for a in watch_etf:
        if a.get("code") not in rec_codes:
            not_consider.append(a)
    for a in watch_stocks:
        if a.get("code") not in rec_codes:
            not_consider.append(a)
    expanded = bool(tstate.classification.degraded or tstate.classification.changed)
    note = ""
    if tstate.classification.degraded:
        structural = str(theme.get("structural_expression", "") or "")
        execution = str(theme.get("execution_expression", "") or "")
        e0 = int(theme.get("eligible_etf_count", 0) or 0)
        tot = int(theme.get("etf_pool_total", 0) or 0)
        note = f"结构 {structural} → 实际 {execution}"
        if tot > 0 and e0 == 0:
            note += f"（ETF {e0}/{tot} 无一通过交易门）"
    return ExecutionCard(
        theme_label=tstate.theme_label, display_state=tstate.display_state,
        mode="expanded" if expanded else "compact",
        primary=primary, alternatives=alternatives, not_consider=not_consider, note=note)


def build_view_model(
    recommendation: dict[str, Any],
    meta: dict[str, Any],
    selection_date: str,
    *,
    display_priority: list[str],
    execution_labels: dict[str, str],
    prev: dict[str, Any] | None = None,
    narratives_spec: dict[str, Any] | None = None,
    audit_summary_spec: list[tuple[str, str]] | None = None,
    audit_sort: str = "",
    audit_etf_summary_spec: list[tuple[str, str]] | None = None,
    audit_etf_sort: str = "",
) -> ReportViewModel:
    """从 recommendation JSON + meta (+ 可选 previous) 构建 ReportViewModel。"""
    changes, changed_themes, comp_status = build_changes(recommendation, prev)

    theme_states: list[ThemeState] = []
    narratives: list[Narrative] = []
    execution: list[ExecutionCard] = []
    audit_stock: list[dict[str, Any]] = []
    audit_etf: list[dict[str, Any]] = []

    for bucket_label, t in _flatten_themes(recommendation):
        theme = str(t.get("theme", ""))
        label = t.get("theme_label", theme)
        cls = classify_theme(t, changed=theme in changed_themes)
        ds = cls.display_state(display_priority)
        ts = ThemeState(
            theme=theme, theme_label=label, bucket_label=bucket_label,
            confirmed=bool(t.get("confirmed", False)),
            classification=cls, display_state=ds,
            action=ACTION_LABELS.get(str(t.get("today", {}).get("action", "")), "—"),
            primary=_primary_name(t), change=_theme_change_brief(t, cls),
            theme_obj=t,
        )
        theme_states.append(ts)
        execution.append(_execution_card(t, ts, execution_labels))

        for tier in ("leaders", "high_beta", "equipment"):
            for a in t.get("monitoring", {}).get(tier, []):
                audit_stock.append({**a, "_theme_label": label, "_asset_key": str(a.get("code", a.get("symbol", "")))})
        for a in (t.get("etf_monitoring") or []):
            audit_etf.append({**a, "_theme_label": label, "_asset_key": str(a.get("code", a.get("symbol", "")))})

    # 01 可执行标的：recommended_actions 补充主题归属
    code_theme = _code_theme_map(recommendation)
    actionable: list[dict[str, Any]] = []
    for a in recommendation.get("recommended_actions", []):
        a = dict(a)
        a.setdefault("theme_label", code_theme.get(str(a.get("code", "")), ""))
        actionable.append(a)

    # 03 叙事（证据驱动，只展开异常）
    if narratives_spec:
        from .report_narrative import build_narratives
        narratives = build_narratives(theme_states, narratives_spec)

    one_liner = _one_liner(theme_states, recommendation.get("action", {}) or {})

    # 06 审计：分类 + 摘要计数 + （Phase B）异常优先排序
    _AUDIT_RANK = {"breakdown": 0, "blocked": 1, "qualified_unselected": 2,
                   "recommended": 3, "normal": 4, "monitor_only": 5, "dynamic_watch": 6}

    def _audit_sort_key(r: dict[str, Any]):
        score = r.get("score_trend")
        return (_AUDIT_RANK.get(r.get("_audit_category", ""), 9),
                -(score if score is not None else -1e9),
                str(r.get("_asset_key", "")))

    for row in audit_stock:
        row["_audit_category"] = _audit_category(row)
    if audit_sort == "anomaly_first":
        audit_stock.sort(key=_audit_sort_key)
    counts: dict[str, int] = {}
    for row in audit_stock:
        cat = row["_audit_category"]
        counts[cat] = counts.get(cat, 0) + 1
    audit_summary: list[tuple[str, str, int]] = []
    for cid, clabel in (audit_summary_spec or []):
        audit_summary.append((cid, clabel, counts.get(cid, 0)))

    # 06 ETF 审计：ETF 特有分类 + 摘要 + （组内按 RPS15）排序 + 主题产品可用性
    def _etf_sort_key(r: dict[str, Any]):
        rps = r.get("rps15")
        return (_AUDIT_RANK.get(r.get("_audit_category", ""), 9),
                -(rps if rps is not None else -1e9),
                str(r.get("_asset_key", "")))

    for row in audit_etf:
        row["_audit_category"] = _etf_audit_category(row)
    if audit_etf_sort == "anomaly_first":
        audit_etf.sort(key=_etf_sort_key)
    etf_counts: dict[str, int] = {}
    for row in audit_etf:
        cat = row["_audit_category"]
        etf_counts[cat] = etf_counts.get(cat, 0) + 1
    audit_etf_summary: list[tuple[str, str, int]] = []
    for cid, clabel in (audit_etf_summary_spec or []):
        audit_etf_summary.append((cid, clabel, etf_counts.get(cid, 0)))

    etf_product_availability: list[tuple[str, int, int]] = []
    for ts in theme_states:
        e0 = int(ts.theme_obj.get("eligible_etf_count", 0) or 0)
        tot = int(ts.theme_obj.get("etf_pool_total", 0) or 0)
        etf_product_availability.append((ts.theme_label, e0, tot))

    return ReportViewModel(
        selection_date=selection_date,
        action=recommendation.get("action", {}) or {},
        one_liner=one_liner,
        actionable=actionable,
        themes=theme_states,
        narratives=narratives,
        execution=execution,
        changes=changes,
        comparison_status=comp_status,
        audit_stock=audit_stock,
        audit_etf=audit_etf,
        audit_summary=audit_summary,
        audit_etf_summary=audit_etf_summary,
        etf_product_availability=etf_product_availability,
        audit_calibre=CALIBRE_TEXT,
        meta=meta or {},
    )
