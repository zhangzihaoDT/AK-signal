"""
Opportunity Radar — HTML renderer（纯消费，不重算）

只消费 build_radar 的 payload + CLI 传入的 meta（trade_date / alignment 等），
不联网、不重新计算任何指标、不改变 JSON 事实。
"""

from __future__ import annotations

from html import escape
from typing import Any

from .radar import CLASSIFICATION_GAP, CLASSIFICATION_NEW

CSS = """
:root {
  --zh-blue: #174A7C; --zh-deep-blue: #06213D; --zh-cyan: #7ECDEB;
  --zh-light-blue: #DDEFF8; --zh-cream: #FFF9EF; --zh-raccoon-gold: #D79A36;
  --zh-brown: #7A4A24; --zh-text: #1F2D3D; --zh-muted: #6B7C8F;
  --zh-card: #FFFFFF; --zh-border: #E8EDF2;
}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Noto Sans SC",sans-serif;background:var(--zh-cream);color:var(--zh-text);line-height:1.6;padding:40px 24px}
.container{max-width:1100px;margin:0 auto}
h1{font-size:26px;font-weight:600;color:var(--zh-deep-blue);margin-bottom:4px}
.subtitle{font-size:14px;color:var(--zh-muted);margin-bottom:8px}
.meta{font-size:12px;color:var(--zh-muted);margin-bottom:24px}
.section{background:var(--zh-card);border-radius:12px;box-shadow:0 1px 4px rgba(0,0,0,.06);padding:24px 28px;margin-bottom:20px}
.section h2{font-size:17px;font-weight:600;color:var(--zh-blue);margin-bottom:16px;padding-bottom:8px;border-bottom:2px solid var(--zh-light-blue)}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:4px 0 8px}
.metric-card{background:var(--zh-light-blue);border-radius:8px;padding:12px 14px;text-align:center}
.metric-value{font-size:22px;font-weight:700;color:var(--zh-blue)}
.metric-label{font-size:11px;color:var(--zh-muted);margin-top:2px}
table{width:100%;border-collapse:collapse;font-size:12.5px;margin:8px 0}
th,td{padding:7px 9px;text-align:left;border-bottom:1px solid var(--zh-border);vertical-align:top}
th{color:var(--zh-muted);font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:.02em}
tr:hover td{background:#F8FAFC}
td.num,th.num{text-align:right}
.tag{display:inline-block;padding:2px 7px;border-radius:4px;font-size:11px;font-weight:500;white-space:nowrap}
.tag-new{background:#E8F5E9;color:#2E7D32}
.tag-gap{background:#FFF3E0;color:#E65100}
.tag-none{background:#F5F5F5;color:#9E9E9E}
.tag-weak{background:#FFEBEE;color:#C62828}
.verdict{background:var(--zh-cream);border:1px dashed var(--zh-raccoon-gold);border-radius:8px;padding:12px 16px;margin:4px 0 8px;font-size:12.5px;color:var(--zh-brown)}
.judgment{background:#F4F9FC;border:1px solid var(--zh-light-blue);border-radius:8px;padding:12px 16px;margin:4px 0 8px;font-size:12.5px;color:var(--zh-text)}
.detail-block{background:#F8FAFC;border:1px solid var(--zh-border);border-radius:8px;padding:8px 10px;margin:6px 0;font-size:12px;color:var(--zh-text)}
.detail-block summary{cursor:pointer;color:var(--zh-blue);font-weight:600}
.code{font-family:ui-monospace,Menlo,monospace;color:var(--zh-muted);font-size:11px}
.muted{color:var(--zh-muted)}
.note{background:#FFFDF5;border:1px solid #F0E3C0;border-radius:8px;padding:10px 14px;margin:6px 0;font-size:12px;color:var(--zh-brown)}
.footer{margin-top:20px;padding-top:14px;border-top:1px solid var(--zh-border);font-size:11px;color:var(--zh-muted);text-align:center}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:720px){.grid{grid-template-columns:1fr}}
"""


def _esc(v: Any) -> str:
    if v is None:
        return ""
    return escape(str(v))


def _num(v: Any, suffix: str = "") -> str:
    if v is None:
        return "—"
    return f"{float(v):,.0f}{suffix}"


def _f(v: Any, nd: int = 1) -> str:
    if v is None:
        return "—"
    return f"{float(v):.{nd}f}"


def _lane2_tag(row: dict[str, Any]) -> str:
    rel = row.get("lane2_reliable_360")
    if rel is False:
        return '<span class="tag tag-weak">unreliable</span>'
    if rel is True:
        return '<span class="tag tag-new">reliable</span>'
    return '<span class="tag tag-none">lane-less</span>'


def _position_text(row: dict[str, Any]) -> str:
    """Lane2 位置展示：bottom state + target stage（机器枚举原样，仅消费展示）。"""
    bs = row.get("lane2_bottom_state")
    if not bs:
        return "lane-less"
    parts = [bs]
    ts = row.get("lane2_target_stage")
    if ts and ts not in ("NON_TARGET", "UNRELIABLE", None):
        parts.append(ts)
    if row.get("lane2_pos120") is not None:
        parts.append(f"pos120={_f(row.get('lane2_pos120'), 0)}")
    return " · ".join(parts)


def _theme_coverage_tag(row: dict[str, Any]) -> str:
    cls = row.get("classification")
    if cls == CLASSIFICATION_GAP:
        return '<span class="tag tag-gap">疑似漏映射</span>'
    if cls == CLASSIFICATION_NEW:
        return '<span class="tag tag-none">未覆盖</span>'
    return ""


def _opp_table(rows: list[dict[str, Any]], show_class: bool) -> str:
    head = ["ETF", "分类", "RPS15", "Lane1", "Lane2 可靠性", "Lane2 位置", "Lane3", "Theme 覆盖", "reason"]
    if not show_class:
        head = ["ETF", "RPS15", "Lane1", "Lane2 可靠性", "Lane2 位置", "Lane3", "Theme 覆盖", "reason"]
    trs = ["<tr>" + "".join(f"<th>{h}</th>" for h in head) + "</tr>"]
    for r in rows:
        cls = r.get("classification")
        if cls == CLASSIFICATION_GAP:
            cls_tag = '<span class="tag tag-gap">Possible Gap</span>'
        elif cls == CLASSIFICATION_NEW:
            cls_tag = '<span class="tag tag-new">New Theme</span>'
        else:
            cls_tag = '<span class="tag tag-weak">Rejected</span>'
        reason = " ".join(f'<span class="code">{_esc(x)}</span>' for x in (r.get("reason_codes") or [])) or "—"
        l3 = r.get("lane3_transition_state") or "—"
        cells = [
            f'<td><b>{_esc(r.get("fund_name"))}</b><br><span class="code">{_esc(r.get("fund_code"))}</span></td>',
            f'<td>{cls_tag}</td>' if show_class else "",
            f'<td class="num">{_f(r.get("rps15"))}</td>',
            f'<td>{_esc(r.get("trend_state") or "—")}</td>',
            f"<td>{_lane2_tag(r)}</td>",
            f"<td class=\"muted\">{_esc(_position_text(r))}</td>",
            f"<td class=\"muted\">{_esc(l3)}</td>",
            f"<td>{_theme_coverage_tag(r)}</td>",
            f'<td class="muted">{reason}</td>',
        ]
        trs.append("<tr>" + "".join(c for c in cells if c) + "</tr>")
    return "<table>" + "".join(trs) + "</table>"


def _direction_table(
    themes: list[dict[str, Any]],
    member_rows: dict[str, list[dict[str, Any]]],
) -> str:
    """V1.1 候选方向级表：候选方向 | 市场 | ETF 数 | 中位 RPS15 | 代表 ETF(amount) | 建议 bucket；
    成员 ETF 明细在 <details> 折叠。"""
    head = ["候选方向", "市场", "ETF 数", "中位 RPS15", "代表 ETF(amount)", "建议 bucket"]
    trs = ["<tr>" + "".join(f"<th>{h}</th>" for h in head) + "</tr>"]
    for g in themes:
        rep = g.get("representative") or {}
        tk = g.get("candidate_theme_key")
        members = member_rows.get(tk, []) if tk else []
        detail = ""
        if members:
            rows = "".join(
                f"<tr><td><b>{_esc(m.get('fund_name'))}</b><br><span class='code'>{_esc(m.get('fund_code'))}</span></td>"
                f"<td class='num'>{_f(m.get('rps15'))}</td>"
                f"<td>{_esc(m.get('trend_state') or '—')}</td>"
                f"<td>{_lane2_tag(m)}</td>"
                f"<td>{_esc(m.get('lane3_transition_state') or '—')}</td></tr>"
                for m in members)
            detail = (f"<details class='detail-block'><summary>成员 ETF（{len(members)}）</summary>"
                      f"<table><tr><th>ETF</th><th>RPS15</th><th>Lane1</th><th>Lane2 可靠性</th><th>Lane3</th></tr>"
                      f"{rows}</table></details>")
        trs.append(
            "<tr>"
            f"<td><b>{_esc(g.get('label'))}</b><br><span class='code'>{_esc(g.get('candidate_theme_key'))}</span></td>"
            f"<td>{_esc(g.get('market_scope') or '—')}</td>"
            f"<td class='num'>{_num(g.get('n_etfs'))}</td>"
            f"<td class='num'>{_f(g.get('median_rps15'))}</td>"
            f"<td>{_esc(rep.get('fund_name') or '—')}<br><span class='code'>{_esc(rep.get('fund_code') or '')}</span>"
            f"<span class='muted'> · {_esc(_amt(rep.get('amount')))}</span></td>"
            f"<td><span class='tag tag-none'>{_esc(g.get('bucket') or '—')}</span></td>"
            "</tr>")
        if detail:
            trs.append("<tr><td colspan='6'>" + detail + "</td></tr>")
    return "<table>" + "".join(trs) + "</table>"


def _amt(v: Any) -> str:
    if v is None:
        return "—"
    return f"{float(v):,.0f}"


def _broad_beta_table(rows: list[dict[str, Any]]) -> str:
    head = ["ETF", "RPS15", "Lane1", "Lane2 可靠性", "Lane3"]
    trs = ["<tr>" + "".join(f"<th>{h}</th>" for h in head) + "</tr>"]
    for m in rows:
        trs.append(
            "<tr>"
            f"<td><b>{_esc(m.get('fund_name'))}</b><br><span class='code'>{_esc(m.get('fund_code'))}</span></td>"
            f"<td class='num'>{_f(m.get('rps15'))}</td>"
            f"<td>{_esc(m.get('trend_state') or '—')}</td>"
            f"<td>{_lane2_tag(m)}</td>"
            f"<td>{_esc(m.get('lane3_transition_state') or '—')}</td></tr>")
    return "<table>" + "".join(trs) + "</table>"


def render_radar_html(payload: dict[str, Any], meta: dict[str, Any] | None = None) -> str:
    """渲染 Opportunity Radar HTML（标题「Opportunity Radar · Theme 外机会」）。

    meta 仅展示层信息：trade_date / generated_at / alignment / disclaimer，不参与判定。
    """
    meta = meta or {}
    summary = payload.get("summary") or {}
    opps = payload.get("opportunities") or []
    rejected = payload.get("rejected") or []
    candidate_themes = payload.get("candidate_themes") or []
    broad_beta = payload.get("broad_beta") or []
    use_directions = bool(candidate_themes)

    gap_rows = [r for r in opps if r.get("classification") == CLASSIFICATION_GAP]
    new_rows = [r for r in opps if r.get("classification") == CLASSIFICATION_NEW]

    def _metric(v: Any, label: str) -> str:
        return (f'<div class="metric-card"><div class="metric-value">{_num(v)}</div>'
                f'<div class="metric-label">{_esc(label)}</div></div>')

    # V1.1：提供 taxonomy 时首屏计数用候选方向数；否则退回 ETF 级（V1）
    opp_metric = (_metric(summary.get("candidate_theme_count"), "Candidate Directions")
                  if use_directions else _metric(summary.get("opportunity_count"), "Opportunity 候选(ETF)"))
    kpis = "".join([
        _metric(summary.get("full_market_count"), "研究able 全市场 ETF"),
        _metric(summary.get("mapped_count"), "已注册 Theme 内"),
        _metric(summary.get("unmapped_count"), "Theme 外"),
        opp_metric,
        _metric(summary.get("broad_beta_count"), "Market Beta(非Theme)"),
        _metric(summary.get("mapping_gap_count"), "疑似 Mapping Gap"),
    ])

    # 今日一句话结论（Observation 只陈述，不推荐）
    if not opps:
        headline = "今日没有出现值得研究的 Theme 外强势方向。"
    elif use_directions:
        headline = (f"今日 {summary.get('candidate_theme_count')} 个候选方向值得研究"
                    f"（{len(new_rows)} 只 Theme 外 ETF；Market Beta {summary.get('broad_beta_count')} 单列）。"
                    + (f" 另 {len(gap_rows)} 只疑似已有 Theme 的 Mapping Gap。" if gap_rows else ""))
    elif gap_rows:
        headline = (f"今日 {len(gap_rows)} 只 ETF 疑似属于已注册 Theme 但未被关键词覆盖"
                    f"（Mapping Gap），另 {len(new_rows)} 只方向可能构成新 Theme 候选。")
    else:
        headline = f"今日 {len(new_rows)} 只 Theme 外强势 ETF 值得研究（可能的新方向候选）。"

    # 02 候选方向 / New Theme 候选
    if use_directions:
        member_rows: dict[str, list[dict[str, Any]]] = {}
        for o in opps:
            tk = o.get("candidate_theme_key")
            if tk:
                member_rows.setdefault(tk, []).append(o)
        new_html = _direction_table(candidate_themes, member_rows)
        broad_html = (_broad_beta_table(broad_beta)
                      if broad_beta else '<p class="muted">无（今日无宽基/区域 ETF 过 Gate）</p>')
    else:
        new_html = _opp_table(new_rows, show_class=True) if new_rows else '<p class="muted">无</p>'
        broad_html = ""
    gap_html = _opp_table(gap_rows, show_class=True) if gap_rows else '<p class="muted">无</p>'

    # 04 Audit / Rejected（折叠，前 40）
    rej_limit = rejected[:40]
    rej_tail = f'<p class="muted">（共 {len(rejected)} 条被拒绝记录，此处展示 RPS15 前 {len(rej_limit)}）</p>' \
        if len(rejected) > len(rej_limit) else ""
    rej_html = _opp_table(rej_limit, show_class=True) if rej_limit else '<p class="muted">无</p>'

    td = meta.get("trade_date", "")
    gen = meta.get("generated_at", "")
    tax = meta.get("taxonomy") or payload.get("taxonomy")
    tax_note = ""
    if tax:
        tax_note = (f" ｜ 方向 taxonomy：{_esc(tax.get('rule_id'))}"
                    f" v{_esc(tax.get('taxonomy_version'))}"
                    f"（{_esc(tax.get('rule_status'))} · {_esc(tax.get('rule_spec_source'))}）")
    disclaimers = meta.get("disclaimer") or (
        "Opportunity Radar 仅发现值得进一步研究的 Theme 外市场状态，不构成 BUY 推荐。"
        "只有完成 Theme 注册与 Layer② Confirmation 后，才能进入 Selection V2（③A→③B→③C）。")

    section02_broad = ""
    if use_directions:
        section02_broad = (
            '<div class="section"><h2>02b Market Beta（宽基/区域，非 Theme）</h2>'
            '<div class="judgment">上证指数 / 中证2000 等宽基或区域指数 ETF 走强反映市场宽度/Beta，'
            '不是产业 Theme 方向——单列观察，不计入候选方向。</div>'
            f'{broad_html}</div>')

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Opportunity Radar · Theme 外机会 {_esc(td)}</title>
<style>{CSS}</style></head>
<body><div class="container">
<h1>Opportunity Radar · Theme 外机会</h1>
<div class="subtitle">Discovery / Observation —— 当前 Theme Registry 未覆盖的强势 ETF 方向</div>
<div class="meta">trade_date：{_esc(td)} ｜ 生成：{_esc(gen)} ｜ rule_version：{_esc(payload.get('rule_version', 'v1'))}{tax_note}</div>

<div class="section"><h2>01 今日 Radar</h2>
<div class="metrics">{kpis}</div>
<div class="verdict">{_esc(headline)}</div>
</div>

<div class="section"><h2>02 Candidate Directions（NEW_THEME_CANDIDATE，方向聚合）</h2>
<div class="judgment">同一语义方向的多只 ETF 聚合为一个候选方向（跨市场按 market_scope 拆开；代表=方向内
amount 最大）。仅回答「是否出现值得建立新 Theme 的方向」，不构成 BUY。成员明细见行内折叠。</div>
{new_html}
</div>
{section02_broad}

<div class="section"><h2>03 Possible Mapping Gaps（POSSIBLE_MAPPING_GAP）</h2>
<div class="judgment">这些 ETF 未被当前关键词规则映射，但已有结构化证据（固定资产池注册 / master exposure 分类）
与某已注册 Theme 高度相关 —— 优先于普通新 Theme 提示处理。</div>
{gap_html}
</div>

<div class="section"><h2>04 Audit / Rejected</h2>
<div class="judgment">Theme 外但未通过 Opportunity Gate 的 ETF（trend 未活跃 / Lane2 不可靠 / 流动性不足）。</div>
{rej_html}
{rej_tail}
</div>

<div class="note">{_esc(disclaimers)}</div>
<div class="footer">Opportunity Radar V1.1 ｜ JSON 是事实源，HTML 仅为 renderer ｜ AKSignal · zihao raccoon</div>
</div></body></html>"""
