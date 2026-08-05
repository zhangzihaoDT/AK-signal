"""
Layer ② 主题确认（Theme Confirmation）— HTML 报告（v0.4.3 两方向）

生成 sw_industry_confirmation_{date}.html，回答 ARCHITECTURE.md ② 的核心问题：
「每个主题是否被底层行业证据支持？」

报告结构：
  ① 主题确认判断  全局 + 按主题（Theme）共振表 + 按组合意图（Bucket）聚合
  ② 证据明细     SW 行业（确认因子之一）：全部焦点行业 RPS5/10/15、ΔRPS15、加速、强势层级
  ③ 龙头 vs 广泛  强势行业的驱动分类（贡献集中度 × 广度）+ 前三大贡献者
  ④ ETF—行业背离 每主题行业群相对全市场强度

注：SW 行业 / ETF / 参与率 / HHI 都是 Theme 的确认因子，不是确认目标本身。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger("sw_industry_rps.confirmation_report")

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
.tag-core{background:#E3F2FD;color:#1565C0}
.tag-related{background:#F5F5F5;color:#6B7C8F}
.tag-strong{background:#E8F5E9;color:#2E7D32}
.tag-observe{background:#FFF3E0;color:#E65100}
.tag-neutral{background:#FFF8E1;color:#F57F17}
.tag-weak{background:#FFEBEE;color:#C62828}
.tag-none{background:#F5F5F5;color:#9E9E9E}
.judgment{background:#F4F9FC;border:1px solid var(--zh-light-blue);border-radius:10px;padding:20px 24px;margin:16px 0;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px;line-height:1.9;color:var(--zh-text)}
.judgment .k{color:var(--zh-muted)}
.judgment .v{font-weight:600;color:var(--zh-deep-blue)}
.verdict{background:var(--zh-cream);border:1px dashed var(--zh-raccoon-gold);border-radius:8px;padding:16px 20px;margin:12px 0;font-size:14px;color:var(--zh-brown)}
.verdict b{color:var(--zh-deep-blue)}
.insight{background:#F8FAFC;border-left:4px solid var(--zh-cyan);border-radius:6px;padding:14px 18px;margin:12px 0;font-size:13px;color:var(--zh-text)}
.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:20px}
@media(max-width:640px){.grid-2{grid-template-columns:1fr}}
hr{border:none;border-top:1px solid var(--zh-border);margin:24px 0}
p{font-size:14px;color:var(--zh-muted);margin-bottom:12px}
.up{color:#2E7D32;font-weight:600}
.down{color:#C62828;font-weight:600}
"""

STRENGTH_TAG = {
    "强势": "tag-strong", "观察": "tag-observe", "中性": "tag-neutral",
    "弱势": "tag-weak", "无数据": "tag-none",
}
RELEVANCE_TAG = {"核心": "tag-core", "相关": "tag-related"}


def _num(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return f"{float(v):.1f}"


def _sign(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    v = float(v)
    if v > 0:
        return f'<span class="up">+{v:.1f}</span>'
    if v < 0:
        return f'<span class="down">{v:.1f}</span>'
    return f"{v:.1f}"


def _pct(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return f"{float(v) * 100:.0f}%"


def render_confirmation_report(
    focus_df: pd.DataFrame,
    resonance: dict[str, Any],
    theme_resonance: list[dict[str, Any]],
    bucket_resonance: list[dict[str, Any]],
    divergence_map: dict[str, dict[str, Any]],
    market_context: dict[str, Any],
    drilldown_results: dict[str, Any],
    output_dir: Path,
    date_str: str,
) -> Path:
    """生成 sw_industry_confirmation_{date}.html。"""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    html_path = output_dir / f"sw_industry_confirmation_{date_str}.html"
    output_dir.mkdir(parents=True, exist_ok=True)

    n_core = int((focus_df["relevance"] == "core").sum()) if not focus_df.empty else 0
    n_rel = len(focus_df) - n_core
    parts = [
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='UTF-8'><meta name='viewport' content='width=device-width, initial-scale=1.0'>",
        f"<title>② 主题确认 · {date_str}</title>",
        f"<style>{CSS}</style></head><body><div class='container'>",
        "<h1>② 主题确认（Theme Confirmation）</h1>",
        f"<div class='subtitle'>报告日期 {date_str[:4]}-{date_str[4:6]}-{date_str[6:8]} · 生成于 {now_str} · "
        f"主题确认因子：SW 行业焦点组（{len(focus_df)} 个，核心 {n_core} · 相关 {n_rel}），按 Bucket（Core/Quality）→ Theme 分层</div>",
    ]

    # ══════════════════ SECTION 1: 群共振 ══════════════════
    parts.append('<div class="section"><h2>① 主题确认判断</h2>')
    parts.append("<p>核心问题：每个主题的 ETF 强势是否得到了底层行业的支持？行业群是单一强势还是同步共振？</p>")

    parts.append('<div class="metrics">')
    metric_items = [
        (str(resonance.get("n_strong", 0)), "强势区(≥90)"),
        (str(resonance.get("n_observe", 0)), "观察区(≥80)"),
        (str(resonance.get("n_core_strong", 0)), "核心强势"),
        (_num(resonance.get("group_median_rps15")), "群中位 RPS15"),
        (_sign(resonance.get("group_median_delta_rps15")), "群 ΔRPS15"),
        (resonance.get("status", "—"), "共振状态"),
    ]
    for val, lbl in metric_items:
        parts.append(f'<div class="metric-card"><div class="metric-value">{val}</div><div class="metric-label">{lbl}</div></div>')
    parts.append('</div>')

    parts.append('<div class="judgment">')
    parts.append(f'焦点行业数：<span class="k">{len(focus_df)}</span>（核心 {n_core} · 相关 {n_rel}）<br>')
    parts.append(f'进入 RPS15 强势区(≥90)：<span class="v">{resonance.get("n_strong", 0)}</span><br>')
    parts.append(f'进入 RPS15 观察区(≥80)：<span class="v">{resonance.get("n_observe", 0)}</span><br>')
    parts.append(f'核心行业强势(≥90)：<span class="v">{resonance.get("n_core_strong", 0)}</span><br>')
    parts.append(f'群 RPS15 中位数：<span class="v">{_num(resonance.get("group_median_rps15"))}</span><br>')
    parts.append(f'群 5 日排名变化（ΔRPS15 中位）：<span class="v">{_sign(resonance.get("group_median_delta_rps15"))}</span><br>')
    parts.append('</div>')
    parts.append(f"<div class='verdict'><b>这一层判断：</b>{resonance.get('verdict', '—')}</div>")

    # Bucket 聚合表
    if bucket_resonance:
        parts.append("<h3>组合意图聚合（Core / Quality / Tactical）</h3>")
        parts.append("<table><tr>")
        parts.append("<th>Bucket</th><th>目标</th><th class='num'>行业数</th><th class='num'>强势(≥90)</th><th class='num'>观察(≥80)</th>")
        parts.append("<th class='num'>中位 RPS15</th><th>状态</th></tr>")
        for br in bucket_resonance:
            tag = {"群共振": "tag-strong", "局部走强": "tag-observe", "整体弱势": "tag-weak"}.get(br["status"], "tag-none")
            parts.append(
                f"<tr><td><b>{br['bucket_label']}</b></td><td>{br['objective']}</td>"
                f"<td class='num'>{br['n']}</td>"
                f"<td class='num'>{br['n_strong']}</td>"
                f"<td class='num'>{br['n_observe']}</td>"
                f"<td class='num'>{_num(br['median_rps15'])}</td>"
                f"<td><span class='tag {tag}'>{br['status']}</span> · {br['summary']}</td></tr>")
        parts.append("</table>")
        parts.append("<p style='font-size:12px'>Bucket 是「为什么持有」的组合意图：Core 长期增长 / Quality 稳定现金流 / Tactical 周期机会。行业确认用于判断当前哪个意图被底层行业支撑。</p>")

    # 主题共振表
    if theme_resonance:
        parts.append("<h3>主题共振（每 Theme 独立确认）</h3>")
        parts.append("<table><tr>")
        parts.append("<th>Bucket</th><th>主题</th><th class='num'>行业数</th><th class='num'>强势(≥90)</th><th class='num'>观察(≥80)</th>")
        parts.append("<th class='num'>中位 RPS15</th><th class='num'>中位 ΔRPS15</th><th>状态</th></tr>")
        theme_rank = {"群共振": "tag-strong", "局部走强": "tag-observe", "整体弱势": "tag-weak"}
        for tr in theme_resonance:
            tag = theme_rank.get(tr["status"], "tag-none")
            parts.append(
                f"<tr><td>{tr.get('bucket_label', '—')}</td><td>{tr['theme_label']}</td>"
                f"<td class='num'>{tr['n']}</td>"
                f"<td class='num'>{tr['n_strong']}</td>"
                f"<td class='num'>{tr['n_observe']}</td>"
                f"<td class='num'>{_num(tr['median_rps15'])}</td>"
                f"<td class='num'>{_sign(tr['median_delta_rps15'])}</td>"
                f"<td><span class='tag {tag}'>{tr['status']}</span> · {tr['summary']}"
                f"{' · <b>' + tr.get('confirmation_breadth', '') + '</b>' if tr.get('confirmation_breadth') else ''}</td></tr>")
        parts.append("</table>")
        parts.append("<p style='font-size:12px'>同一主题内产业周期可能不同步，主题级共振用于识别「是主题内群共振，还是单一行业行情」。</p>")

    parts.append('</div>')

    # ══════════════════ SECTION 2: 证据明细（SW 行业） ══════════════════
    parts.append('<div class="section"><h2>② 证据明细 · SW 行业</h2>')
    if not focus_df.empty:
        parts.append("<table><tr>")
        parts.append("<th>行业</th><th>Bucket</th><th>主题</th><th>关联</th><th class='num'>RPS5</th><th class='num'>RPS10</th><th class='num'>RPS15</th>")
        parts.append("<th class='num'>ΔRPS15</th><th class='num'>短期动能</th><th>强势层级</th><th>驱动分类</th><th class='num'>参与率</th><th>重构质量</th>")
        parts.append("</tr>")
        for _, r in focus_df.iterrows():
            rt = RELEVANCE_TAG.get(r["relevance_label"], "")
            rl = r["relevance_label"]
            st = STRENGTH_TAG.get(r["strength_level"], "")
            sl = r["strength_level"]
            drive = r.get("drive_pattern", "") or "—"
            acc = _sign(r.get("short_term_acceleration"))
            ql = r.get("reconstruction_quality", "") or ""
            parts.append(
                f"<tr><td>{r['industry_name']}</td>"
                f"<td>{r.get('bucket_label', '')}</td>"
                f"<td>{r.get('theme_label', '')}</td>"
                f"<td><span class='tag {rt}'>{rl}</span></td>"
                f"<td class='num'>{_num(r['RPS5'])}</td>"
                f"<td class='num'>{_num(r['RPS10'])}</td>"
                f"<td class='num'>{_num(r['RPS15'])}</td>"
                f"<td class='num'>{_sign(r['delta_rps15'])}</td>"
                f"<td class='num'>{acc}</td>"
                f"<td><span class='tag {st}'>{sl}</span></td>"
                f"<td>{drive}</td>"
                f"<td class='num'>{_pct(r.get('participation_rate'))}</td>"
                f"<td>{ql}</td></tr>")
        parts.append("</table>")
    else:
        parts.append("<p>无重点行业数据。</p>")
    parts.append('</div>')

    # ══════════════════ SECTION 3: 龙头 vs 广泛上涨 ══════════════════
    parts.append('<div class="section"><h2>③ 龙头 vs 广泛上涨</h2>')
    parts.append("<p>强势行业的驱动结构：少数龙头贡献（高 HHI）还是广泛参与（高参与率）。以下展示已完成穿透的重点行业。</p>")
    drilled = {code: dd for code, dd in drilldown_results.items() if dd is not None}
    if drilled:
        for code, dd in drilled.items():
            parts.append(f"<h3>{dd.industry_name}（{code}）— {dd.industry_return_pct:+.1f}% / {dd.window}日</h3>")
            parts.append('<div class="grid-2">')
            parts.append("<table><tr><th>维度</th><th>值</th></tr>")
            parts.append(f"<tr><td>驱动分类</td><td>{_format_full(dd)}</td></tr>")
            parts.append(f"<tr><td>参与率</td><td>{_pct(dd.participation_rate)}（{dd.num_positive}/{dd.num_constituents} 上涨）</td></tr>")
            parts.append(f"<tr><td>HHI</td><td>{_num(dd.hhi)}</td></tr>")
            parts.append(f"<tr><td>Top1 贡献占比</td><td>{_pct(dd.top1_share)}</td></tr>")
            parts.append(f"<tr><td>Top3 贡献占比</td><td>{_pct(dd.top3_share)}</td></tr>")
            parts.append(f"<tr><td>重构质量</td><td>{dd.reconstruction_quality}</td></tr>")
            parts.append(f"<tr><td>覆盖</td><td>权重 {_pct(dd.weight_coverage)} / 数量 {_pct(dd.count_coverage)}</td></tr>")
            parts.append("</table>")
            parts.append("<table><tr><th>前三大贡献</th><th class='num'>贡献(pp)</th></tr>")
            for cr in dd.top_contributors[:3]:
                parts.append(f"<tr><td>{cr.stock_name}</td><td class='num'>{cr.contribution_pct:+.2f}</td></tr>")
            parts.append("</table>")
            parts.append("</div>")
    else:
        parts.append("<p>当前无重点行业进入强势区/观察区，未进行成分股穿透。</p>")
    parts.append('</div>')

    # ══════════════════ SECTION 4: ETF—行业背离（每主题） ══════════════════
    parts.append('<div class="section"><h2>④ ETF—行业背离（按主题）</h2>')
    parts.append("<p>每个主题行业群相对全市场的中观强度（行业侧近似）。完整「ETF vs 行业」双覆盖需接入 ETF 侧数据（后续）。</p>")
    if divergence_map:
        parts.append("<table><tr><th>主题</th><th class='num'>行业群中位 RPS15</th><th class='num'>全市场中位 RPS15</th><th class='num'>差值</th><th>状态</th><th>判断</th></tr>")
        for theme_key, dv in divergence_map.items():
            tag = {"行业支持": "tag-strong", "行业背离": "tag-weak", "中性": "tag-neutral", "无数据": "tag-none"}.get(dv.get("status", ""), "tag-none")
            parts.append(
                f"<tr><td>{theme_key}</td>"
                f"<td class='num'>{_num(dv.get('group_median_rps15'))}</td>"
                f"<td class='num'>{_num(dv.get('market_median_rps15'))}</td>"
                f"<td class='num'>{_sign(dv.get('gap'))}</td>"
                f"<td><span class='tag {tag}'>{dv.get('status', '—')}</span></td>"
                f"<td>{dv.get('note', '—')}</td></tr>")
        parts.append("</table>")
    else:
        parts.append("<p>无主题背离数据。</p>")
    parts.append('</div>')

    # ── Footer ──
    parts.append(f'<hr><div style="text-align:center;font-size:12px;color:var(--zh-muted);padding:20px 0">AKsignal · Layer ② 主题确认（Theme Confirmation） · 报告自动生成于 {now_str}</div>')
    parts.append("</div></body></html>")

    html_path.write_text("\n".join(parts), encoding="utf-8")
    logger.info("  confirmation html: %s", html_path)
    return html_path


def _format_full(dd: Any) -> str:
    from . import confirmation as conf
    c = conf.CONTRIBUTION_LABELS.get(dd.contribution_structure, dd.contribution_structure)
    b = conf.BREADTH_LABELS.get(dd.breadth_structure, dd.breadth_structure)
    if c and b:
        return f"{c} × {b}"
    return c or b or "—"
