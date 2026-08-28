"""Layer③ 报告解释器（Report Engine, v2）。

流程：Report Spec（report_spec.yaml）+ recommendation JSON + ReportViewModel → HTML。
Spec 管结构（sections/columns/expand/include），本模块管「怎么渲染」：
  - 通用渲染器：table / verdict / chip / section / details
  - 6 段 renderer：decision_summary / theme_matrix / theme_narrative /
    execution_cards / change_log / audit
HTML 是 Spec 的生成结果；本模块不制造决策事实。
"""

from __future__ import annotations

import html as _html
from datetime import datetime
from typing import Any

from .report_formatters import FORMATTERS, HEADER_FORMATTERS
from .report_spec import ColumnSpec, ReportSpec, load_report_spec
from .report_viewmodel import ReportViewModel, build_view_model

ACTION_TAG = {"BUY": "tag-strong", "OBSERVE": "tag-observe", "WAIT": "tag-none"}
SEVERITY_TAG = {"up": "tag-strong", "down": "tag-weak", "info": "tag-observe"}

CSS = """
:root {
  --zh-blue:#174A7C; --zh-deep-blue:#06213D; --zh-cyan:#7ECDEB;
  --zh-light-blue:#DDEFF8; --zh-cream:#FFF9EF; --zh-raccoon-gold:#D79A36;
  --zh-brown:#7A4A24; --zh-text:#1F2D3D; --zh-muted:#6B7C8F;
  --zh-card:#FFFFFF; --zh-border:#E8EDF2;
}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Noto Sans SC",sans-serif;background:var(--zh-cream);color:var(--zh-text);line-height:1.65;padding:40px 24px}
.container{max-width:1320px;margin:0 auto}
h1{font-size:26px;font-weight:600;color:var(--zh-deep-blue);margin-bottom:4px}
.subtitle{font-size:13px;color:var(--zh-muted);margin-bottom:28px}
.section{background:var(--zh-card);border-radius:12px;box-shadow:0 1px 4px rgba(0,0,0,.06);padding:24px 28px;margin-bottom:22px}
.section h2{font-size:17px;font-weight:600;color:var(--zh-blue);margin-bottom:14px;padding-bottom:8px;border-bottom:2px solid var(--zh-light-blue)}
.verdict{background:var(--zh-light-blue);border-radius:8px;padding:14px 16px;margin:10px 0;font-size:15px}
.verdict .big{font-size:22px;font-weight:700;color:var(--zh-blue);margin-right:8px}
.insight{background:#FBF6EA;border-left:3px solid var(--zh-raccoon-gold);padding:10px 14px;border-radius:6px;margin:10px 0;font-size:13.5px}
.insight b{color:var(--zh-brown)}
table{width:100%;border-collapse:collapse;font-size:13px;margin:10px 0}
th{background:var(--zh-light-blue);color:var(--zh-blue);text-align:left;padding:7px 10px;font-weight:600;border-bottom:2px solid #C7E2F0}
td{padding:7px 10px;border-bottom:1px solid var(--zh-border);vertical-align:top}
tr:hover td{background:#FAFCFF}
td.num,th.num{text-align:right}
.tag{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;font-weight:600}
.tag-strong{background:#E4F2E6;color:#1B6B34}
.tag-observe{background:#FDF0DB;color:#9A6B16}
.tag-weak{background:#FBE9E7;color:#B3402A}
.tag-none{background:#EEF1F4;color:var(--zh-muted)}
.chip{display:inline-flex;align-items:center;gap:8px;background:var(--zh-light-blue);border-radius:8px;padding:8px 12px;margin:4px 6px 4px 0;font-size:13px}
.chip .meta{color:var(--zh-muted);font-size:12px}
.card{border:1px solid var(--zh-border);border-radius:10px;padding:14px 16px;margin:12px 0}
.card h3{font-size:14px;font-weight:600;color:var(--zh-blue);margin-bottom:8px}
.card .line{font-size:13px;margin:4px 0}
.reject{color:#B3402A;font-size:12px}
details{border:1px solid var(--zh-border);border-radius:10px;padding:12px 16px;margin:12px 0}
details summary{cursor:pointer;font-weight:600;color:var(--zh-blue);font-size:14px}
.calibre{font-size:12.5px;color:var(--zh-muted);line-height:1.8}
.empty{color:var(--zh-muted);font-size:13px;padding:8px 0}
.change-list{list-style:none}
.change-list li{padding:6px 0;border-bottom:1px dashed var(--zh-border);font-size:13.5px}
.change-list li .who{color:var(--zh-muted);font-size:12px}
/* 技术详情（全字段）区域：加宽 + 横向滚动，避免多列换行 */
.detail-scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;padding-bottom:4px}
.detail-scroll table{white-space:nowrap}
.detail-scroll th,.detail-scroll td{white-space:nowrap}
"""


def _esc(v: Any) -> str:
    return _html.escape("" if v is None else str(v))


# ── 通用渲染器 ───────────────────────────────────────────────────────

def _render_table(spec_cols: list[ColumnSpec], rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "<div class='empty'>— 无数据</div>"
    has_evidence = any(row.get("evidence_stage") for row in rows)
    cols = [c for c in spec_cols if not c.when or (c.when == "has_evidence" and has_evidence)]
    parts = ["<table><thead><tr>"]
    for c in cols:
        header = HEADER_FORMATTERS[c.header_fmt](None) if c.header_fmt in HEADER_FORMATTERS else c.header
        cls = " class='num'" if c.key in ("score_change_1d", "position_pct", "rps15", "liquidity", "theme_rank") else ""
        parts.append(f"<th{cls}>{_esc(header)}</th>")
    parts.append("</tr></thead><tbody>")
    for row in rows:
        cells = []
        for c in cols:
            if c.fmt in FORMATTERS:
                # 注入列值到 key（fmt_num/fmt_pct 读 row['key']）；其余字段原样保留
                cell_row = {**row, "key": row.get(c.key)}
                cells.append(f"<td>{FORMATTERS[c.fmt](cell_row)}</td>")
            else:
                val = row.get(c.key, "—")
                cells.append(f"<td>{_esc('—' if val is None else val)}</td>")
        parts.append("<tr>" + "".join(cells) + "</tr>")
    parts.append("</tbody></table>")
    return "\n".join(parts)


def _section(title: str, body: str, subtitle: str = "") -> str:
    sub = f"<div class='subtitle'>{_esc(subtitle)}</div>" if subtitle else ""
    return f"<div class='section'><h2>{_esc(title)}</h2>{sub}{body}</div>"


# ── 01 今日结论 ──────────────────────────────────────────────────────

def _render_executive(spec: ReportSpec, vm: ReportViewModel) -> str:
    lvl = str(vm.action.get("level", "WAIT"))
    body = [
        f"<div class='verdict'><span class='big'>{_esc(lvl)}</span>"
        f"<span>{_esc(vm.one_liner)}</span></div>",
    ]
    if vm.actionable:
        body.append("<h3 style='font-size:14px;color:var(--zh-blue);margin:14px 0 8px'>今日可执行标的</h3>")
        chips = []
        for a in vm.actionable:
            signal = str(a.get("signal", "") or "")
            lead = str(a.get("leadership_level", "") or "")
            pos = str(a.get("position_level", "") or "")
            theme = str(a.get("theme_label", "") or "")
            meta = " · ".join(x for x in (signal, lead, pos) if x)
            tpart = f"<span class='meta'>· {_esc(theme)}</span>" if theme else ""
            chips.append(
                f"<span class='chip'><b>{_esc(a.get('name', ''))}</b>"
                f"<span class='meta'>{_esc(a.get('code', ''))} · {_esc(meta)}</span>{tpart}</span>")
        body.append("<div>" + "".join(chips) + "</div>")
    else:
        body.append("<div class='empty'>今日无可执行标的</div>")
    changes = [c for c in vm.changes if c.severity in ("up", "down")]
    if changes:
        body.append("<h3 style='font-size:14px;color:var(--zh-blue);margin:16px 0 8px'>今日关键变化</h3>")
        body.append("<ul class='change-list'>")
        for c in changes[:5]:
            body.append(
                f"<li><span class='tag {SEVERITY_TAG.get(c.severity, 'tag-none')}'>{_esc(c.severity)}</span> "
                f"<b>{_esc(c.theme_label)}</b> · {_esc(c.text)}"
                f"<span class='who'> · {_esc(c.kind)}</span></li>")
        body.append("</ul>")
    return _section(vm.meta.get("title", "01 今日结论"), "\n".join(body))


# ── 02 主题状态 ──────────────────────────────────────────────────────

def _render_theme_matrix(spec: ReportSpec, vm: ReportViewModel) -> str:
    rows = []
    for t in vm.themes:
        rows.append({
            "theme_label": t.theme_label,
            "display_state": t.display_state,
            "action": t.action,
            "primary": t.primary,
            "change": t.change,
        })
    return _section(spec.section("theme_matrix").title, _render_table(spec.theme_matrix_columns, rows))


# ── 03 为什么（叙事） ────────────────────────────────────────────────

def _render_narratives(spec: ReportSpec, vm: ReportViewModel) -> str:
    if not vm.narratives:
        return _section(spec.section("narratives").title,
                        "<div class='empty'>今日无需要额外解释的异常（正常状态已压缩）。</div>")
    blocks = []
    for n in vm.narratives:
        body = [f"<h3 style='font-size:14px;color:var(--zh-blue)'>{_esc(n.title)}</h3>",
                f"<div class='insight'>{_esc(n.argument)}</div>"]
        if n.evidence:
            body.append("<table><tbody>" + "".join(
                f"<tr><th style='width:180px'>{_esc(k)}</th><td>{_esc(v)}</td></tr>"
                for k, v in n.evidence) + "</tbody></table>")
        blocks.append("<div class='card'>" + "\n".join(body) + "</div>")
    return _section(spec.section("narratives").title, "\n".join(blocks))


# ── 04 怎么表达 ──────────────────────────────────────────────────────

def _execution_line(a: dict[str, Any], label: str) -> str:
    signal = str(a.get("signal", "") or "")
    lead = str(a.get("leadership_level", "") or "")
    pos = str(a.get("position_level", "") or "")
    meta = " · ".join(x for x in (signal, lead, pos) if x)
    reason = str(a.get("reason", "") or a.get("reject_reason", "") or "")
    if a.get("recommended"):
        reason_txt = f"<span class='meta'> · {_esc(reason)}</span>" if reason else ""
    else:
        reason_txt = f"<span class='reject'> — {_esc(reason)}</span>" if reason else ""
    return (f"<div class='line'><b>{_esc(label)}：</b>"
            f"<b>{_esc(a.get('name', ''))}</b> <span class='meta'>{_esc(a.get('code', ''))} · {_esc(meta)}</span>"
            f"{reason_txt}</div>")


def _render_execution(spec: ReportSpec, vm: ReportViewModel) -> str:
    blocks = []
    for card in vm.execution:
        lines = []
        if card.note:
            lines.append(f"<div class='insight' style='margin:4px 0'>{_esc(card.note)}</div>")
        if card.primary:
            for a in card.primary:
                lines.append(_execution_line(a, spec.execution_labels.get("primary", "首选")))
        elif card.display_state != "watch":
            lines.append("<div class='empty'>— 无合格标的</div>")
        if card.mode == "expanded":
            if card.alternatives:
                lines.append(f"<div class='line' style='margin-top:6px'><b>{_esc(spec.execution_labels.get('alternative', '备选'))}：</b></div>")
                for a in card.alternatives[:4]:
                    lines.append(_execution_line(a, "·"))
            if card.not_consider:
                lines.append(f"<div class='line' style='margin-top:6px'><b>{_esc(spec.execution_labels.get('not_consider', '不做什么'))}：</b></div>")
                for a in card.not_consider[:6]:
                    reject = str(a.get("reject_reason", "") or "")
                    lines.append(
                        f"<div class='line'><span class='meta'>{_esc(a.get('name', ''))}</span>"
                        f"<span class='reject'>× {_esc(reject)}</span></div>")
        blocks.append(
            f"<div class='card'><h3>{_esc(card.theme_label)}"
            f" <span class='tag {_state_tag(card.display_state)}'>{_esc(_state_label(card.display_state))}</span></h3>"
            + "\n".join(lines) + "</div>")
    return _section(spec.section("execution").title, "\n".join(blocks) if blocks else "<div class='empty'>—</div>")


def _state_tag(ds: str) -> str:
    return {"degraded": "tag-weak", "changed": "tag-observe", "actionable": "tag-strong",
            "watch": "tag-none", "normal": "tag-strong"}.get(ds, "tag-none")


def _state_label(ds: str) -> str:
    return {"degraded": "已确认 · 降级", "changed": "已确认 · 有变化", "actionable": "已确认",
            "watch": "观察", "normal": "已确认"}.get(ds, ds)


# ── 05 风险与变化 ────────────────────────────────────────────────────

def _render_changes(spec: ReportSpec, vm: ReportViewModel) -> str:
    if not vm.changes:
        return _section(spec.section("changes").title, "<div class='empty'>今日无显著变化。</div>")
    status_txt = {"OK": "对比上一交易日", "NO_PREV": "无上一份报告（首次/缺历史），仅日内变化",
                  "UNAVAILABLE": "上一份报告不可用，跳过跨日对比",
                  "VERSION_MISMATCH": "上一份报告跨版本，跳过结构对比"}.get(vm.comparison_status, "")
    body = [f"<div class='subtitle' style='margin-bottom:6px'>{_esc(status_txt)}</div>",
            "<ul class='change-list'>"]
    for c in vm.changes:
        body.append(
            f"<li><span class='tag {SEVERITY_TAG.get(c.severity, 'tag-none')}'>{_esc(c.severity)}</span> "
            f"<b>{_esc(c.theme_label)}</b> · {_esc(c.text)}"
            f"<span class='who'> · {_esc(c.kind)}</span></li>")
    body.append("</ul>")
    return _section(spec.section("changes").title, "\n".join(body))


# ── 06 决策审计 ──────────────────────────────────────────────────────

_AUDIT_SUMMARY_CLS = {
    "breakdown": "tag-weak", "blocked": "tag-weak",
    "qualified_unselected": "tag-observe", "recommended": "tag-strong",
    "normal": "tag-none", "monitor_only": "tag-none", "dynamic_watch": "tag-none",
}


def _render_audit_summary(vm: ReportViewModel, *, etf: bool = False) -> str:
    """审计摘要（debug index）：先告诉有什么异常，再定位具体资产。"""
    summary = vm.audit_etf_summary if etf else vm.audit_summary
    rows = vm.audit_etf if etf else vm.audit_stock
    if not summary:
        return ""
    prefix = "ETF 审计" if etf else "个股审计"
    chips = []
    for cid, label, n in summary:
        cls = _AUDIT_SUMMARY_CLS.get(cid, "tag-none")
        chips.append(f"<span class='tag {cls}'>{_esc(label)} {n}</span>")
    return f"<div class='insight' style='margin:0 0 12px'><b>{prefix} · {len(rows)} 只</b>　" + " ".join(chips) + "</div>"


def _render_etf_product_availability(vm: ReportViewModel) -> str:
    """主题产品可用性：每个主题有没有可交易 ETF 产品（读 theme 既有 eligible/etf_pool 事实）。"""
    if not vm.etf_product_availability:
        return ""
    lines = []
    for label, e0, tot in vm.etf_product_availability:
        if tot <= 0:
            continue
        warn = " ⚠" if e0 == 0 else ""
        lines.append(f"<span>{_esc(label)} <b>{e0}/{tot}</b> 可交易{warn}</span>")
    if not lines:
        return ""
    return f"<div class='calibre' style='margin:0 0 12px'><b>主题产品可用性</b>　" + "　".join(lines) + "</div>"


def _render_audit(spec: ReportSpec, vm: ReportViewModel) -> str:
    sec = spec.section("audit")
    parts = [_section(sec.title, f"<div class='calibre'>{_esc(vm.audit_calibre)}</div>", sec.subtitle)]
    stock_tab = spec.audit["stock_matrix"]
    etf_tab = spec.audit["etf_matrix"]
    parts.append(f"<h3 style='color:var(--zh-blue);margin:18px 0 6px'>{_esc(stock_tab.title)}</h3>")
    parts.append(_render_audit_summary(vm))
    parts.append(_render_table(stock_tab.columns, vm.audit_stock))
    if stock_tab.detail_columns:
        parts.append("<details><summary>技术详情（全字段 · 底层证据）</summary>"
                     + "<div class='detail-scroll'>" + _render_table(stock_tab.detail_columns, vm.audit_stock)
                     + "</div></details>")
    parts.append(f"<h3 style='color:var(--zh-blue);margin:18px 0 6px'>{_esc(etf_tab.title)}</h3>")
    parts.append(_render_audit_summary(vm, etf=True))
    parts.append(_render_etf_product_availability(vm))
    parts.append(_render_table(etf_tab.columns, vm.audit_etf))
    if etf_tab.detail_columns:
        parts.append("<details><summary>ETF 技术详情（全字段 · 底层证据）</summary>"
                     + "<div class='detail-scroll'>" + _render_table(etf_tab.detail_columns, vm.audit_etf)
                     + "</div></details>")
    return "\n".join(parts)


RENDERERS = {
    "decision_summary": _render_executive,
    "theme_matrix": _render_theme_matrix,
    "theme_narrative": _render_narratives,
    "execution_cards": _render_execution,
    "change_log": _render_changes,
    "audit": _render_audit,
}


# ── 顶层 ─────────────────────────────────────────────────────────────

def render_report_html(
    spec: ReportSpec,
    vm: ReportViewModel,
    *,
    date_str: str,
    now_str: str,
) -> str:
    parts = [
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='UTF-8'><meta name='viewport' content='width=device-width, initial-scale=1.0'>",
        f"<title>③ 今日投资建议 · {date_str}</title>",
        f"<style>{CSS}</style></head><body><div class='container'>",
        f"<h1>{_esc(spec.title)}</h1>",
        f"<div class='subtitle'>{_esc(spec.subtitle)} · 报告日期 {date_str[:4]}-{date_str[4:6]}-{date_str[6:8]} · 生成于 {now_str} · "
        "Decision Layer：只消费已落盘事实，禁止联网/重算；本报告由 Report Engine 按 Spec 生成，不产生新事实</div>",
    ]
    for sec in spec.sections:
        renderer = RENDERERS[sec.renderer]
        body = renderer(spec, vm)
        if sec.priority == "appendix":
            parts.append(f"<details><summary>{_esc(sec.title)}</summary>{body}</details>")
        else:
            parts.append(body)
    parts.append(
        f'<hr><div style="text-align:center;font-size:12px;color:var(--zh-muted);padding:20px 0">AKsignal · Layer ③ 投资建议 · 报告结构由 report_spec.yaml 声明 · {now_str}</div>')
    parts.append("</div></body></html>")
    return "\n".join(parts)


def render_selection_html_v2(
    recommendation: dict[str, Any],
    output_dir: Any,
    date_str: str,
    meta: dict[str, Any] | None = None,
    *,
    now_str: str | None = None,
    prev: dict[str, Any] | None = None,
) -> str:
    """Report Engine 入口：Spec + recommendation(+prev) → HTML 字符串。"""
    spec = load_report_spec()
    ts = now_str or datetime.now().strftime("%Y-%m-%d %H:%M")
    vm = build_view_model(
        recommendation, meta or {}, date_str,
        display_priority=spec.display_priority,
        execution_labels=spec.execution_labels,
        prev=prev,
        narratives_spec=spec.narratives,
        audit_summary_spec=[(s.id, s.label) for s in spec.audit["stock_matrix"].summary],
        audit_sort=spec.audit["stock_matrix"].sort,
        audit_etf_summary_spec=[(s.id, s.label) for s in spec.audit["etf_matrix"].summary],
        audit_etf_sort=spec.audit["etf_matrix"].sort,
    )
    return render_report_html(spec, vm, date_str=date_str, now_str=ts)
