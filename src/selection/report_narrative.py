"""Layer③ 报告 03 叙事（report_narrative）。

「为什么」段落：论点式（标题 + 论证段 + 证据表），严格 exception-based——
NORMAL 主题不展开；只对 degraded / changed / actionable /（接近确认的）watch 展开。

证据驱动：Spec 定义叙事结构（title / clauses / evidence_labels）与 clause→触发谓词映射；
本模块解析「哪些事实支持哪个 clause」，再动态组合论证句——不在 Spec 硬编码整段结论。

只读展示派生，绝不写回 recommendation 对象。
"""

from __future__ import annotations

from typing import Any

from .report_spec import ClauseSpec, NarrativeSpec
from .report_viewmodel import Narrative, ThemeState


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "—"


def _num(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):g}"
    except (TypeError, ValueError):
        return str(v)


def _theme_fields(t: dict[str, Any]) -> dict[str, Any]:
    """theme 字段 + why metrics 展平，供 clause 占位符填充。"""
    fields: dict[str, Any] = {}
    for k, v in t.items():
        if not isinstance(v, (dict, list)):
            fields[k] = v
    why = t.get("why", {}) or {}
    for k, v in why.items():
        if not isinstance(v, (dict, list)) and k not in fields:
            fields[k] = v
    return fields


# ── clause 触发谓词（代码管「哪些事实支持哪个 clause」，Spec 管映射） ──

def _predicates(t: dict[str, Any]) -> dict[str, bool]:
    why = t.get("why", {}) or {}
    metrics = {k: why.get(k) for k in (
        "n_observe", "n_total", "n_strong", "median_participation",
        "median_hhi", "median_top3_share", "median_rps15",
        "strongest_industry_rps15", "etf_median_rps15")}
    confirmed = bool(t.get("confirmed", False))
    structural = str(t.get("structural_expression", "") or "")
    execution = str(t.get("execution_expression", "") or "")
    hhi = metrics.get("median_hhi")
    top3 = metrics.get("median_top3_share")
    part = metrics.get("median_participation")
    etf_med = metrics.get("etf_median_rps15")
    e0 = int(t.get("eligible_etf_count", 0) or 0)
    tot = int(t.get("etf_pool_total", 0) or 0)
    s0 = int(t.get("eligible_stock_count", 0) or 0)
    n_observe = int(metrics.get("n_observe", 0) or 0)
    n_total = int(metrics.get("n_total", 0) or 0)
    d_ind = t.get("distance_to_industry_confirm")
    return {
        "concentration_high": (hhi is not None and hhi >= 0.15) or (top3 is not None and top3 >= 0.60),
        "concentration_low": (hhi is None or hhi < 0.15) and (top3 is None or top3 < 0.60),
        "breadth_weak": part is not None and part < 0.60,
        "breadth_high": part is not None and part >= 0.60,
        "etf_ineligible": tot > 0 and e0 == 0,
        "etf_strong": etf_med is not None and etf_med >= 60,
        "stock_available": s0 > 0,
        "narrow_confirmed": confirmed and n_total > 0 and n_observe / n_total < 0.5,
        "consistent": bool(structural and execution and structural == execution),
        "watch_near": (not confirmed) and d_ind is not None and float(d_ind) <= 20,
        "watch_far": (not confirmed) and (d_ind is None or float(d_ind) > 20),
    }


def _fill(text: str, fields: dict[str, Any]) -> str:
    return text.format_map(_SafeDict({k: _num(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
                                      for k, v in fields.items()}))


def _compose_argument(t: dict[str, Any], spec: NarrativeSpec, predicates: dict[str, bool]) -> str:
    fields = _theme_fields(t)
    parts: list[str] = []
    for clause in spec.clauses:
        if predicates.get(clause.when, False):
            parts.append(_fill(clause.text, fields))
    return "；".join(parts)


_EVIDENCE_VALUE = {
    "结构判断": lambda t: str(t.get("structural_expression", "") or "—"),
    "实际执行": lambda t: str(t.get("execution_expression", "") or "—"),
    "合格ETF": lambda t: f"{t.get('eligible_etf_count', 0)}/{t.get('etf_pool_total', 0)}",
    "合格个股": lambda t: _num(t.get("eligible_stock_count")),
    "确认广度": lambda t: f"{t.get('why', {}).get('n_observe', '—')}/{t.get('why', {}).get('n_total', '—')}",
    "中位参与率": lambda t: _num(t.get("why", {}).get("median_participation")),
    "中位HHI": lambda t: _num(t.get("why", {}).get("median_hhi")),
    "中位Top3": lambda t: _num(t.get("why", {}).get("median_top3_share")),
    "行业中位RPS15": lambda t: _num(t.get("why", {}).get("median_rps15")),
    "ETF中位RPS15": lambda t: _num(t.get("why", {}).get("etf_median_rps15")),
    "最强行业RPS15": lambda t: _num(t.get("why", {}).get("strongest_industry_rps15")),
    "距离确认": lambda t: _num(t.get("distance_to_industry_confirm")),
    "代表ETF": lambda t: (t.get("strongest_etf") or {}).get("name", "—"),
    "ETF RPS15": lambda t: _num((t.get("strongest_etf") or {}).get("rps15")),
}


def _compose_evidence(t: dict[str, Any], labels: list[str]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for label in labels:
        fn = _EVIDENCE_VALUE.get(label)
        if fn:
            out.append((label, fn(t)))
    return out


def build_narratives(
    themes: list[ThemeState],
    narratives_spec: dict[str, NarrativeSpec],
) -> list[Narrative]:
    out: list[Narrative] = []
    for ts in themes:
        t = ts.theme_obj
        if ts.classification.degraded:
            ntype = "degraded_execution"
        elif ts.classification.watch:
            preds = _predicates(t)
            if preds.get("watch_near"):
                ntype = "approaching"
            else:
                continue  # 只解释接近确认的观察主题，远的压缩
        elif ts.classification.changed or ts.classification.actionable:
            ntype = "trend_diffusing"
        else:
            continue  # NORMAL 压缩，不展开
        spec = narratives_spec.get(ntype)
        if spec is None:
            continue
        preds = _predicates(t)
        argument = _compose_argument(t, spec, preds)
        if not argument:
            continue  # 无任何 clause 命中（证据不足）→ 不展开
        title = spec.title.format(theme_label=ts.theme_label)
        out.append(Narrative(
            theme_label=ts.theme_label, display_state=ts.display_state,
            title=title, argument=argument,
            evidence=_compose_evidence(t, spec.evidence_labels)))
    return out
