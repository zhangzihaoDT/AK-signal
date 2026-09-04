"""Layer③ 报告声明式 Spec —— Schema 校验 + typed model + loader（v2）。

报告结构真源 = src/selection/report_spec.yaml。
生产路径不允许依赖隐藏默认值：Spec 缺失/非法/引用了未注册的 formatter 或条件 → 运行开始即抛错。
Spec 只描述「呈现什么」（结构/顺序/列/标签/条件/优先级），不描述「怎么算」。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


class ReportSpecError(ValueError):
    pass


REPORT_SPEC_PATH = Path(__file__).parent / "report_spec.yaml"
SUPPORTED_SPEC_VERSIONS = {2}

PRIORITIES = {"primary", "secondary", "appendix"}
# 多标签分类 / 展示状态标签（Spec display_priority / expand.only / include 的取值集合）
DISPLAY_LABELS = {"degraded", "changed", "actionable", "watch", "normal", "confirmed"}
# 已知 renderer（report_engine 注册表；启动期校验）—— v0.12.1 8 段 IA
KNOWN_RENDERERS = {
    "decision_summary", "theme_confirmation", "eligibility",
    "vehicle", "timing", "why_now", "next_trigger", "audit",
}
# 已知列条件谓词（决定某列是否显示）
KNOWN_COLUMN_CONDITIONS = {"has_evidence"}
# 已知审计摘要分类 id（audit.*.summary）—— 个股矩阵沿用决策分类；ETF 矩阵用 V2 资格/载体/时机分类
KNOWN_AUDIT_CATEGORIES = {
    "breakdown", "blocked", "qualified_unselected", "recommended",
    "normal", "monitor_only", "dynamic_watch",
    "eligible", "vehicle", "timing_ready", "unreliably",
    "below_account", "low_liquidity", "below_trend", "dedup_lost",
}
# 已知审计排序模式
KNOWN_AUDIT_SORTS = {"", "anomaly_first"}
# formatter 名称（report_formatters 注册表；Spec 引用必须在其中）
KNOWN_FORMATTERS = {
    "num", "pct", "money", "liquidity", "change_1d", "trajectory",
    "technical", "blocking", "data_quality", "evidence",
    "monitor_conclusion", "etf_conclusion", "trend_heat", "position_level",
    "position_pct", "position_header", "etf_trend_status", "trade_state",
    "display_state_tag", "theme_role", "participation", "audit_reason",
    "etf_theme_role", "etf_audit_reason", "etf_strength", "etf_position",
    "audit_category", "leadership",
    "lane3_state", "lane2_reliable", "signal_chain",
    "confirmed_tag", "timing_exec",
}


@dataclass(frozen=True)
class ColumnSpec:
    key: str
    header: str
    fmt: str = ""
    header_fmt: str = ""
    when: str = ""


@dataclass(frozen=True)
class SummarySpec:
    id: str
    label: str


@dataclass(frozen=True)
class TableSpec:
    title: str
    source: str
    columns: list[ColumnSpec]
    summary: list[SummarySpec] = field(default_factory=list)
    detail_columns: list[ColumnSpec] = field(default_factory=list)
    sort: str = ""


@dataclass(frozen=True)
class SectionSpec:
    id: str
    priority: str
    renderer: str
    title: str
    subtitle: str = ""
    collapsed: bool = False
    expand_only: list[str] = field(default_factory=list)   # narratives.expand.only
    include: list[str] = field(default_factory=list)       # execution.include
    normal_mode: str = ""                                   # compact / expanded
    exception_mode: str = ""


@dataclass(frozen=True)
class ReportSpec:
    spec_version: int
    id: str
    title: str
    subtitle: str
    display_priority: list[str]
    sections: list[SectionSpec]
    theme_confirmation_columns: list[ColumnSpec]
    eligibility_columns: list[ColumnSpec]
    vehicle_columns: list[ColumnSpec]
    timing_columns: list[ColumnSpec]
    why_labels: dict[str, str]
    next_trigger_labels: dict[str, str]
    audit: dict[str, TableSpec]

    def section(self, section_id: str) -> SectionSpec:
        for s in self.sections:
            if s.id == section_id:
                return s
        raise ReportSpecError(f"report spec: unknown section {section_id!r}")


def _require(raw: dict[str, Any], path: str) -> Any:
    node: Any = raw
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            raise ReportSpecError(f"report spec: {path}: missing")
        node = node[key]
    return node


def _validate_columns(cols: Any, path: str) -> list[ColumnSpec]:
    if not isinstance(cols, list) or not cols:
        raise ReportSpecError(f"report spec: {path}: non-empty columns list required")
    out: list[ColumnSpec] = []
    for i, c in enumerate(cols):
        if not isinstance(c, dict) or not c.get("key") or not c.get("header"):
            raise ReportSpecError(f"report spec: {path}[{i}]: key & header required")
        fmt = str(c.get("fmt", "") or "")
        if fmt and fmt not in KNOWN_FORMATTERS:
            raise ReportSpecError(f"report spec: {path}[{i}].fmt {fmt!r} not registered")
        hfmt = str(c.get("header_fmt", "") or "")
        if hfmt and hfmt not in KNOWN_FORMATTERS:
            raise ReportSpecError(f"report spec: {path}[{i}].header_fmt {hfmt!r} not registered")
        when = str(c.get("when", "") or "")
        if when and when not in KNOWN_COLUMN_CONDITIONS:
            raise ReportSpecError(f"report spec: {path}[{i}].when {when!r} unknown")
        out.append(ColumnSpec(key=c["key"], header=c["header"], fmt=fmt, header_fmt=hfmt, when=when))
    return out


def _validate_labels(raw: Any, path: str) -> dict[str, str]:
    if not isinstance(raw, dict) or not raw:
        raise ReportSpecError(f"report spec: {path}: labels dict required")
    return {str(k): str(v) for k, v in raw.items()}


def _validate_sections(raw: Any, path: str) -> list[SectionSpec]:
    if not isinstance(raw, list) or not raw:
        raise ReportSpecError(f"report spec: {path}: sections list required")
    ids: set[str] = set()
    out: list[SectionSpec] = []
    for i, s in enumerate(raw):
        sid = s.get("id", "")
        if not sid or sid in ids:
            raise ReportSpecError(f"report spec: {path}[{i}]: unique id required")
        ids.add(sid)
        if s.get("priority", "") not in PRIORITIES:
            raise ReportSpecError(f"report spec: section {sid}.priority invalid: {s.get('priority')!r}")
        renderer = s.get("renderer", "")
        if renderer not in KNOWN_RENDERERS:
            raise ReportSpecError(f"report spec: section {sid}.renderer {renderer!r} not registered")
        expand_only = [str(x) for x in (s.get("expand", {}).get("only", []) if isinstance(s.get("expand"), dict) else [])]
        for x in expand_only:
            if x not in DISPLAY_LABELS:
                raise ReportSpecError(f"report spec: section {sid}.expand.only {x!r} invalid")
        include = [str(x) for x in (s.get("include", []) or [])]
        for x in include:
            if x not in DISPLAY_LABELS:
                raise ReportSpecError(f"report spec: section {sid}.include {x!r} invalid")
        out.append(SectionSpec(
            id=sid, priority=s["priority"], renderer=renderer,
            title=str(s.get("title", "")), subtitle=str(s.get("subtitle", "")),
            collapsed=bool(s.get("collapsed", False)),
            expand_only=expand_only, include=include,
            normal_mode=str(s.get("normal_mode", "")), exception_mode=str(s.get("exception_mode", "")),
        ))
    return out


def _validate_display_priority(raw: Any, path: str) -> list[str]:
    if not isinstance(raw, list) or not raw:
        raise ReportSpecError(f"report spec: {path}: display_priority required")
    out: list[str] = []
    for x in raw:
        x = str(x)
        if x not in DISPLAY_LABELS:
            raise ReportSpecError(f"report spec: {path}: {x!r} invalid")
        out.append(x)
    return out


def validate_report_spec(raw: dict[str, Any]) -> None:
    """整体校验：版本支持 / 结构完整 / 引用合法。失败抛 ReportSpecError。"""
    r = _require(raw, "report")
    if int(r.get("spec_version", 0)) not in SUPPORTED_SPEC_VERSIONS:
        raise ReportSpecError(f"report spec: unsupported spec_version {r.get('spec_version')!r}")
    _validate_display_priority(r.get("display_priority"), "report.display_priority")
    _validate_sections(r.get("sections"), "report.sections")
    for key in ("theme_confirmation_columns", "eligibility_columns",
                "vehicle_columns", "timing_columns"):
        _validate_columns(r.get(key), f"report.{key}")
    _validate_labels(r.get("why_labels"), "report.why_labels")
    _validate_labels(r.get("next_trigger_labels"), "report.next_trigger_labels")
    audit = r.get("audit")
    if not isinstance(audit, dict):
        raise ReportSpecError("report spec: report.audit required")


def _validate_summary(raw: Any, path: str) -> list[SummarySpec]:
    if not isinstance(raw, list) or not raw:
        return []
    seen: set[str] = set()
    out: list[SummarySpec] = []
    for i, s in enumerate(raw):
        sid = s.get("id", "") if isinstance(s, dict) else ""
        label = s.get("label", "") if isinstance(s, dict) else ""
        if sid not in KNOWN_AUDIT_CATEGORIES:
            raise ReportSpecError(f"report spec: {path}[{i}].id {sid!r} unknown")
        if sid in seen:
            raise ReportSpecError(f"report spec: {path}: duplicate id {sid!r}")
        seen.add(sid)
        out.append(SummarySpec(id=sid, label=str(label or sid)))
    return out


def _validate_audit_table(tab: dict[str, Any], key: str) -> TableSpec:
    summary = _validate_summary(tab.get("summary"), f"report.audit.{key}.summary")
    sort = str(tab.get("sort", "") or "")
    if sort not in KNOWN_AUDIT_SORTS:
        raise ReportSpecError(f"report spec: report.audit.{key}.sort {sort!r} unknown")
    return TableSpec(
        title=str(tab.get("title", key)),
        source=str(tab.get("source", "")),
        columns=_validate_columns(tab.get("columns"), f"report.audit.{key}.columns"),
        summary=summary,
        detail_columns=_validate_columns(tab.get("detail_columns"), f"report.audit.{key}.detail_columns")
        if tab.get("detail_columns") else [],
        sort=sort,
    )


def _parse(raw: dict[str, Any]) -> ReportSpec:
    r = _require(raw, "report")
    sections = _validate_sections(r.get("sections"), "report.sections")
    audit_raw = _require(r, "audit")
    audit: dict[str, TableSpec] = {}
    for key in ("stock_matrix", "etf_matrix"):
        tab = audit_raw.get(key)
        if not isinstance(tab, dict):
            raise ReportSpecError(f"report spec: report.audit.{key} required")
        audit[key] = _validate_audit_table(tab, key)
    return ReportSpec(
        spec_version=int(r["spec_version"]),
        id=str(r.get("id", "selection")),
        title=str(r.get("title", "")),
        subtitle=str(r.get("subtitle", "")),
        display_priority=_validate_display_priority(r.get("display_priority"), "report.display_priority"),
        sections=sections,
        theme_confirmation_columns=_validate_columns(
            r.get("theme_confirmation_columns"), "report.theme_confirmation_columns"),
        eligibility_columns=_validate_columns(r.get("eligibility_columns"), "report.eligibility_columns"),
        vehicle_columns=_validate_columns(r.get("vehicle_columns"), "report.vehicle_columns"),
        timing_columns=_validate_columns(r.get("timing_columns"), "report.timing_columns"),
        why_labels=_validate_labels(r.get("why_labels"), "report.why_labels"),
        next_trigger_labels=_validate_labels(r.get("next_trigger_labels"), "report.next_trigger_labels"),
        audit=audit,
    )


def _load_raw(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ReportSpecError(f"report spec: {path}: invalid yaml root")
    return data


@lru_cache(maxsize=4)
def load_report_spec(path: Path | None = None) -> ReportSpec:
    p = path or REPORT_SPEC_PATH
    raw = _load_raw(p)
    validate_report_spec(raw)
    return _parse(raw)

