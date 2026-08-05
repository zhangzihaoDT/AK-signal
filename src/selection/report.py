"""
Layer ③ — 多主题交易候选 HTML 可视化（v0.4.3）

候选资产对象（JSON）的只读视图，不承担任何筛选逻辑。
报告按 Bucket（Core/Quality/Tactical）→ Theme 分层展示。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("selection.report")

CSS = """
:root {
  --zh-blue: #174A7C; --zh-deep-blue: #06213D; --zh-cyan: #7ECDEB;
  --zh-light-blue: #DDEFF8; --zh-cream: #FFF9EF; --zh-raccoon-gold: #D79A36;
  --zh-brown: #7A4A24; --zh-text: #1F2D3D; --zh-muted: #6B7C8F;
  --zh-card: #FFFFFF; --zh-border: #E8EDF2;
}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Noto Sans SC",sans-serif;background:var(--zh-cream);color:var(--zh-text);line-height:1.6;padding:40px 24px}
.container{max-width:1000px;margin:0 auto}
h1{font-size:28px;font-weight:600;color:var(--zh-deep-blue);margin-bottom:4px}
.subtitle{font-size:14px;color:var(--zh-muted);margin-bottom:32px}
.section{background:var(--zh-card);border-radius:12px;box-shadow:0 1px 4px rgba(0,0,0,.06);padding:28px 32px;margin-bottom:24px}
.section h2{font-size:18px;font-weight:600;color:var(--zh-blue);margin-bottom:20px;padding-bottom:10px;border-bottom:2px solid var(--zh-light-blue)}
.section h3{font-size:15px;font-weight:600;color:var(--zh-text);margin:18px 0 10px}
.section h4{font-size:13px;font-weight:600;color:var(--zh-muted);margin:14px 0 8px}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin:16px 0}
.metric-card{background:var(--zh-light-blue);border-radius:8px;padding:14px 16px;text-align:center}
.metric-value{font-size:22px;font-weight:700;color:var(--zh-blue)}
.metric-label{font-size:12px;color:var(--zh-muted);margin-top:2px}
table{width:100%;border-collapse:collapse;font-size:13px;margin:12px 0}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--zh-border)}
th{color:var(--zh-muted);font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:.03em}
tr:hover td{background:#F8FAFC}
td.num,th.num{text-align:right}
.tag{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:500}
.tag-confirm{background:#E8F5E9;color:#2E7D32}
.tag-unconfirm{background:#FFEBEE;color:#C62828}
.tag-role{background:#E3F2FD;color:#1565C0}
.verdict{background:var(--zh-cream);border:1px dashed var(--zh-raccoon-gold);border-radius:8px;padding:16px 20px;margin:12px 0;font-size:14px;color:var(--zh-brown)}
.verdict b{color:var(--zh-deep-blue)}
.insight{background:#F8FAFC;border-left:4px solid var(--zh-cyan);border-radius:6px;padding:14px 18px;margin:12px 0;font-size:13px;color:var(--zh-text)}
hr{border:none;border-top:1px solid var(--zh-border);margin:24px 0}
p{font-size:14px;color:var(--zh-muted);margin-bottom:12px}
.empty{color:var(--zh-muted);font-size:13px;padding:8px 0}
.align-line{font-size:12px;color:var(--zh-muted);margin:0 0 24px}
details{border:1px solid var(--zh-border);border-radius:8px;margin:12px 0;padding:0 16px 4px}
summary{cursor:pointer;font-weight:600;color:var(--zh-blue);padding:12px 0;list-style:none}
summary::before{content:"▸ ";color:var(--zh-muted)}
details[open] summary::before{content:"▾ "}
"""


def _num(v: Any) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{v}"


def _state_tag(state: str) -> str:
    if state == "RECOMMENDED":
        return "<span class='tag tag-confirm'>推荐</span>"
    if state == "QUALIFIED":
        return "<span class='tag tag-role'>合格</span>"
    return "观察"


def _final_state_label(state: str) -> str:
    return {"RECOMMENDED": "今日候选", "QUALIFIED": "合格观察", "WATCH": "核心监控"}.get(state, state)


def _monitor_state_label(a: dict[str, Any]) -> str:
    """固定观察池的最终状态展示；趋势数据缺失 → 数据缺失。"""
    if a.get("selection_status") == "unavailable":
        return "数据缺失"
    return _final_state_label(str(a.get("state", "")))


def _trend_qualified_label(score: Any, trend_status: Any) -> str:
    """趋势资格：趋势分≥70 且 watch_level∈{S,A} 视为达标。"""
    try:
        passed = score is not None and float(score) >= 70 and str(trend_status) in ("S", "A")
    except (TypeError, ValueError):
        passed = False
    return "已通过" if passed else "未通过"


def _asset_rows(parts: list[str], items: list[dict[str, Any]]) -> None:
    if not items:
        parts.append("<div class='empty'>—</div>")
        return
    parts.append("<table><tr><th>代码</th><th>名称</th><th>状态</th><th class='num'>RPS15</th><th class='num'>RPS20</th><th class='num'>RPS60</th><th class='num'>趋势分</th><th>趋势</th><th>说明</th></tr>")
    for it in items:
        parts.append(
            f"<tr><td>{it.get('code', '')}</td><td>{it.get('name', '')}</td>"
            f"<td>{_state_tag(it.get('state', ''))}</td>"
            f"<td class='num'>{_num(it.get('rps15'))}</td>"
            f"<td class='num'>{_num(it.get('rps20'))}</td>"
            f"<td class='num'>{_num(it.get('rps60'))}</td>"
            f"<td class='num'>{_num(it.get('score_trend'))}</td>"
            f"<td>{it.get('trend_status', '')}</td>"
            f"<td>{it.get('reason', '')}</td></tr>")
    parts.append("</table>")


def render_selection_html(
    candidates: dict[str, Any],
    output_dir: Path,
    date_str: str,
    meta: dict[str, Any] | None = None,
) -> Path:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    html_path = output_dir / f"tradable_candidates_{date_str}.html"
    output_dir.mkdir(parents=True, exist_ok=True)

    parts = [
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='UTF-8'><meta name='viewport' content='width=device-width, initial-scale=1.0'>",
        f"<title>③ 交易标的筛选 · {date_str}</title>",
        f"<style>{CSS}</style></head><body><div class='container'>",
        "<h1>③ 多主题交易标的筛选与表达方式选择</h1>",
        f"<div class='subtitle'>报告日期 {date_str[:4]}-{date_str[4:6]}-{date_str[6:8]} · 生成于 {now_str} · "
        f"按 Bucket（Core/Quality/Tactical）→ Theme 分层 · Layer ③ 只回答「买什么」，买多少/何时买卖由 Layer 4 决定</div>",
    ]

    # 数据对齐（单行，运行质量信息，不占决策层）
    alignment = (meta or {}).get("alignment", {})
    layers = (meta or {}).get("layers", {})
    if alignment:
        align_txt = {
            "aligned": "对齐", "stale_industry": "行业滞后", "stale_etf": "ETF 滞后",
            "no_industry": "无行业确认", "no_etf": "无 ETF 数据", "no_data": "无数据",
        }.get(alignment.get("alignment_status", ""), alignment.get("alignment_status", ""))
        lag = alignment.get("industry_lag_days")
        lag_txt = "" if lag in (None, 0) else f"，行业滞后 {lag} 个交易日"
        le = layers.get("etf", {}); lc = layers.get("account_candidates", {}); ls = layers.get("sw_industry", {})
        parts.append(
            f"<div class='align-line'>信号对齐：<b>{align_txt}</b>{lag_txt} · selection_date={alignment.get('selection_date', '')}"
            f" · Layer① ETF {le.get('trade_date', '—')}/{le.get('data_status', '—')}"
            f" · Layer② 行业 {ls.get('trade_date', '—')}/{ls.get('data_status', '—')}</div>")

    # Selection 输入覆盖：ETF 复用 Layer① / 个股趋势产物 / 缺失降级
    coverage = (meta or {}).get("coverage", {})
    if coverage:
        degraded = coverage.get("degraded_assets") or []
        cov_txt = (f"Selection coverage：<b>{coverage.get('selection_coverage', '—')}</b>"
                   f"（{coverage.get('selection_coverage_pct', '—')}% · ETF 复用 {coverage.get('etf_reused', '—')}"
                   f" · 个股输入 {coverage.get('stock_inputs_loaded', '—')} · Online fetch {coverage.get('online_fetches', 0)}）")
        if degraded:
            cov_txt += f"<br><small style='color:#C62828'>缺失/不可用 {len(degraded)}：{'、'.join(degraded)}</small>"
        parts.append(f"<div class='align-line'>{cov_txt}</div>")

    # 口径说明：ETF RPS 与行业 RPS 是不同横截面，不可直接对比
    parts.append(
        "<div class='insight' style='border-left-color:#D79A36'><b>口径说明：</b>"
        "ETF 的 RPS15/20/60 是<b>相对全市场 ETF 横截面</b>的百分位（Layer① rotation）；"
        "行业的 RPS15 是<b>相对 124 个申万二级行业横截面</b>的百分位（Layer②）。"
        "两者标尺不同，<b>不可直接对比</b>。主题确认 = 任一焦点行业 RPS15≥80（存在性判定）；"
        "ETF 候选按 ETF 自身动量（趋势门）+ 主题确认选出，<b>不要求 ETF 对应行业也确认</b>。"
        "因此可能出现「行业弱势但对应 ETF 相对强势」的组合——这是两层信号解耦的固有特性，不代表行业层面已转强。</div>")

    # 配置降级提示：未注册 theme / 跨主题资产（不参与确认门控，仅作状态标记）
    config_issues = (meta or {}).get("config_issues") or {}
    if config_issues:
        issue_parts: list[str] = []
        unregistered = config_issues.get("unregistered_themes") or []
        if unregistered:
            issue_parts.append(f"asset pool 存在未注册 theme（不进入候选，需加入 config/themes_two_directions.yaml）：{'、'.join(unregistered)}")
        cross = config_issues.get("cross_theme_assets") or {}
        if cross:
            shown = "、".join(f"{code}({'/'.join(ths)})" for code, ths in list(cross.items())[:8])
            issue_parts.append(f"跨主题资产（primary 归属 = 首个 bucket）：{shown}")
        if issue_parts:
            parts.append(f"<div class='insight' style='border-left-color:#D79A36'><strong>配置降级：</strong>{' ｜ '.join(issue_parts)}</div>")

    # ── 第一层：今天做什么 ─────────────────────────────────────────
    action = candidates.get("action") or {}
    lvl = action.get("level", "WAIT")
    action_tag = "tag-confirm" if lvl == "BUY" else ("tag-unconfirm" if lvl == "WAIT" else "")
    lvl_txt = {"BUY": "买入", "OBSERVE": "观察", "WAIT": "等待"}.get(lvl, lvl)
    summary = candidates.get("summary") or {}
    direction = candidates.get("direction") or {}
    dir_txt = (f"{action.get('direction_label', '')} · {action.get('theme_label', '')}"
               if action.get("theme_label") else "—")
    parts.append('<div class="section"><h2>今天做什么</h2>')
    parts.append(f"<div class='verdict'><b>今日方向：</b><span class='tag {action_tag}'>{lvl_txt}</span> · {dir_txt}</div>")
    if action.get("expression_label"):
        parts.append(f"<div class='insight'><b>表达方式：</b>{action.get('expression_label', '')}</div>")
    parts.append(f"<p><b>原因：</b>{direction.get('reason', '')}</p>")
    closest = candidates.get("closest_theme")
    if closest:
        se = closest.get("strongest_etf") or {}
        d_ind = closest.get("distance_to_industry_confirm")
        d_etf = closest.get("distance_to_etf_strength")
        parts.append(
            f"<div class='insight'><b>最接近出现行业确认：</b>{closest.get('theme_label', '')}"
            f"（{closest.get('bucket_label', '')}） · 代表 ETF {se.get('name', '—')} · 当前 RPS15 {_num(se.get('rps15'))}"
            f" · 行业确认尚差 {_num(d_ind)} · ETF 强势门槛尚差 {_num(d_etf)}</div>")
    parts.append('<div class="metrics">')
    parts.append(f"<div class='metric-card'><div class='metric-value'>{summary.get('recommended_actions', 0)}</div><div class='metric-label'>推荐行动</div></div>")
    parts.append(f"<div class='metric-card'><div class='metric-value'>{summary.get('qualified_candidates', 0)}</div><div class='metric-label'>合格候选</div></div>")
    parts.append(f"<div class='metric-card'><div class='metric-value'>{summary.get('confirmed_themes', '0/0')}</div><div class='metric-label'>确认主题</div></div>")
    parts.append(f"<div class='metric-card'><div class='metric-value'>{summary.get('strongest_etf_theme', '—')}</div><div class='metric-label'>ETF 表现最强</div></div>")
    parts.append('</div>')
    parts.append('</div>')

    # ── 第二层：Bucket 概览 ───────────────────────────────────────
    buckets = candidates.get("buckets", [])
    closest_key = closest.get("theme") if closest else None
    parts.append('<div class="section"><h2>多主题概览 · 相对状态</h2>')
    parts.append("<table><tr><th>Bucket</th><th>目标</th><th>主题</th><th>当前阶段</th><th class='num'>中位RPS15</th><th>最强ETF</th><th class='num'>ETF RPS15</th><th class='num'>距行业确认</th><th>结论</th></tr>")
    for b in buckets:
        for sub in b.get("themes", []):
            m = sub.get("metrics", {})
            se = sub.get("strongest_etf") or {}
            d_ind = sub.get("distance_to_industry_confirm")
            d_txt = "已确认" if sub.get("confirmed") else (_num(d_ind) if d_ind is not None else "—")
            if sub.get("confirmed"):
                concl = "已确认"
            elif closest and sub.get("theme") == closest.get("theme"):
                concl = "最接近行业确认"
            else:
                concl = "暂不关注"
            bucket_cell = f"<b>{b.get('bucket_label', '')}</b>" if sub.get("theme") == (b.get("themes") or [{}])[0].get("theme", "") else ""
            parts.append(
                f"<tr><td>{bucket_cell}</td><td>{b.get('objective', '')}</td><td>{sub.get('theme_label', '')}</td><td>{sub.get('stage', '')}</td>"
                f"<td class='num'>{_num(m.get('median_rps15'))}</td><td>{se.get('name', '—')}</td>"
                f"<td class='num'>{_num(se.get('rps15'))}</td><td class='num'>{d_txt}</td><td>{concl}</td></tr>")
    parts.append("</table>")

    # 只展开「最接近转强」主题，其余折叠；展开区压缩为「为什么未确认 / 代表ETF / 仍缺条件」
    for b in buckets:
        parts.append(f"<h3>{b.get('bucket_label', '')} · {b.get('objective', '')}</h3>")
        for sub in b.get("themes", []):
            is_closest = sub.get("theme") == closest_key
            theme_label = sub.get("theme_label", sub.get("theme", ""))
            conf_tag = "tag-confirm" if sub.get("confirmed") else "tag-unconfirm"
            conf_txt = "已确认" if sub.get("confirmed") else "未确认"
            open_attr = " open" if is_closest else ""
            parts.append(f"<details{open_attr}><summary>{theme_label} · {sub.get('stage', '')} · <span class='tag {conf_tag}'>{conf_txt}</span></summary>")

            if sub.get("confirmed"):
                # 确认机制显式化：依据 + 行业广度（窄幅/广泛），避免「20% 行业转强为何主题确认」的误读
                ev = sub.get("confirm_evidence", {}) or {}
                m = sub.get("metrics", {})
                breadth = sub.get("confirmation_breadth", "")
                ev_txt = f"{ev.get('industry', '—')} RPS15 = {_num(ev.get('rps15'))}，达到确认门槛"
                parts.append(
                    f"<div class='insight'><b>确认依据：</b>{ev_txt}"
                    f" ｜ 行业广度：{_num(m.get('n_observe'))} 个焦点行业进入观察区，"
                    f"属于 <b>{breadth}</b></div>")
                parts.append(f"<div class='verdict'><b>表达方式：</b>{sub.get('expression_label', '')}<br><small>{sub.get('expression_reason', '')}</small></div>")
                m = sub.get("metrics", {})
                parts.append('<div class="metrics">')
                for k, v in m.items():
                    if v is None or k == "strongest_industry_rps15":
                        continue
                    label = {"median_rps15": "中位 RPS15", "median_participation": "中位参与率",
                             "median_hhi": "中位 HHI", "median_top3_share": "中位 Top3",
                             "n_strong": "强势行业", "n_observe": "观察行业", "etf_median_rps15": "主题ETF中位RPS15"}.get(k, k)
                    val = f"{float(v) * 100:.0f}%" if k.startswith("median_participation") else _num(v)
                    parts.append(f'<div class="metric-card"><div class="metric-value">{val}</div><div class="metric-label">{label}</div></div>')
                parts.append('</div>')
            else:
                m = sub.get("metrics", {})
                se = sub.get("strongest_etf") or {}
                d_ind = sub.get("distance_to_industry_confirm")
                parts.append(
                    f"<div class='insight'><b>为什么未确认：</b>行业观察数 {m.get('n_observe', 0)}"
                    f" ｜ 中位行业 RPS15 {_num(m.get('median_rps15'))}"
                    f" ｜ 距行业确认 {_num(d_ind)}</div>")
                parts.append(
                    f"<div class='insight'><b>代表 ETF：</b>{se.get('name', '—')}"
                    f" ｜ RPS15 {_num(se.get('rps15'))}"
                    f" ｜ 5日 {_num(se.get('return_5d'))}% ｜ 20日 {_num(se.get('return_20d'))}%</div>")
                parts.append("<div class='empty'>仍缺条件：行业确认尚未通过（RPS15≥80）</div>")

            sc = sub.get("stock_candidates", [])
            if sc:
                parts.append("<h4>今日股票行动候选</h4>")
                _asset_rows(parts, sc)

            if sub.get("confirmed"):
                for section_key, section_label in [("core_etf", "核心 ETF（动态候选）"), ("sub_industry_etf", "细分行业 ETF（动态候选）")]:
                    parts.append(f"<h4>{section_label}</h4>")
                    _asset_rows(parts, sub.get(section_key, []))
            parts.append('</details>')
    parts.append('</div>')

    # ── 第三层：核心资产监控（固定池全量，独立章节） ───────────────
    tier_label = {"LEADER": "龙头", "HIGH_BETA": "高弹性", "UPSTREAM": "上游"}
    all_wl: list[dict[str, Any]] = []
    for b in buckets:
        for sub in b.get("themes", []):
            wl = sub.get("stock_watchlist", {})
            for tier in ("leaders", "high_beta", "equipment"):
                for a in wl.get(tier, []):
                    all_wl.append({**a, "_tier": tier_label.get(a.get("role", ""), tier)})
    parts.append('<div class="section"><h2>核心资产监控（固定观察池全量）</h2>')
    parts.append("<div class='empty' style='padding:0 0 12px'>变化比绝对状态更重要：一日分数变动、状态变化、持续天数、最近趋势达标日期。</div>")
    if all_wl:
        parts.append("<table><tr><th>标的</th><th>分类</th><th>所属主题</th><th class='num'>趋势分</th><th>趋势</th><th class='num'>一日变化</th><th>趋势资格</th><th>风险门控</th><th>最终状态</th><th>状态变化</th><th class='num'>持续</th><th>最近达标</th></tr>")
        for a in all_wl:
            sc = a.get("score_change_1d")
            sc_txt = "" if sc is None else f"{sc:+d}"
            reason = a.get("reason", "")
            if reason in ("风险警戒", "剔除观察"):
                risk_txt = f"未通过：{reason}"
            else:
                risk_txt = "通过" if a.get("risk_gate_passed", True) else "未通过"
            risk_flags = a.get("risk_flags") or []
            if risk_flags:
                risk_txt += f"（{'，'.join(risk_flags)}）"
            parts.append(
                f"<tr><td>{a.get('name', '')}</td><td>{a.get('_tier', '')}</td><td>{a.get('theme', '')}</td>"
                f"<td class='num'>{_num(a.get('score_trend'))}</td><td>{a.get('trend_status', '')}</td>"
                f"<td class='num'>{sc_txt}</td><td>{_trend_qualified_label(a.get('score_trend'), a.get('trend_status'))}</td>"
                f"<td>{risk_txt}</td><td>{_monitor_state_label(a)}</td>"
                f"<td>{a.get('state_change', '')}</td><td class='num'>{a.get('days_in_state', '')}d</td>"
                f"<td>{a.get('last_trend_qualified_date', '')}</td></tr>")
        parts.append("</table>")
    else:
        parts.append("<div class='empty'>— 无固定观察池标的</div>")
    parts.append('</div>')

    parts.append(f'<hr><div style="text-align:center;font-size:12px;color:var(--zh-muted);padding:20px 0">AKsignal · Layer ③ 多主题交易标的筛选 · 报告自动生成于 {now_str}</div>')
    parts.append("</div></body></html>")

    html_path.write_text("\n".join(parts), encoding="utf-8")
    logger.info("selection html: %s", html_path)
    return html_path
