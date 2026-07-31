"""
Layer ③ — 交易候选 HTML 可视化

候选资产对象（JSON）的只读视图，不承担任何筛选逻辑。
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
"""


def _num(v: Any) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{v}"


def render_selection_html(candidates: dict[str, Any], output_dir: Path, date_str: str) -> Path:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    html_path = output_dir / f"tradable_candidates_{date_str}.html"
    output_dir.mkdir(parents=True, exist_ok=True)

    parts = [
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='UTF-8'><meta name='viewport' content='width=device-width, initial-scale=1.0'>",
        f"<title>③ 交易标的筛选 · {date_str}</title>",
        f"<style>{CSS}</style></head><body><div class='container'>",
        "<h1>③ 交易标的筛选与表达方式选择</h1>",
        f"<div class='subtitle'>报告日期 {date_str[:4]}-{date_str[4:6]}-{date_str[6:8]} · 生成于 {now_str} · Layer ③ 只回答「买什么」，买多少/何时买卖由 Layer 4 决定</div>",
    ]

    # 方向门控
    parts.append('<div class="section"><h2>方向门控</h2>')
    gate = candidates.get("direction_gate", "SKIP")
    gate_tag = "tag-confirm" if gate == "PROCEED" else "tag-unconfirm"
    parts.append(f"<div class='insight'><strong>门控：</strong><span class='tag {gate_tag}'>{gate}</span> · {candidates.get('direction_reason', '')}</div>")
    parts.append('</div>')

    # 子主题候选
    parts.append('<div class="section"><h2>交易候选</h2>')
    for sub in candidates.get("subthemes", []):
        sub_label = sub.get("subtheme_label", sub.get("subtheme", ""))
        confirmed = sub.get("confirmed", False)
        conf_tag = "tag-confirm" if confirmed else "tag-unconfirm"
        expr = sub.get("expression_label", "")

        conf_txt = "已确认" if confirmed else "未确认"
        parts.append(f"<h3>{sub_label} · {sub.get('subtheme', '')} <span class='tag {conf_tag}'>{conf_txt}</span></h3>")
        parts.append(f"<div class='verdict'><b>表达方式：</b>{expr}<br><small>{sub.get('expression_reason', '')}</small></div>")

        # 子主题指标
        m = sub.get("metrics", {})
        parts.append('<div class="metrics">')
        for k, v in m.items():
            if v is None:
                continue
            label = {"median_rps15": "中位 RPS15", "median_participation": "中位参与率",
                     "median_hhi": "中位 HHI", "median_top3_share": "中位 Top3",
                     "n_strong": "强势行业", "n_observe": "观察行业"}.get(k, k)
            val = f"{float(v) * 100:.0f}%" if k.startswith("median_participation") else _num(v)
            parts.append(f'<div class="metric-card"><div class="metric-value">{val}</div><div class="metric-label">{label}</div></div>')
        parts.append('</div>')

        for section_key, section_label in [
            ("core_etf", "核心 ETF"), ("sub_industry_etf", "细分行业 ETF"),
            ("leaders", "行业龙头"), ("high_beta", "高弹性观察"), ("equipment", "设备/上游"),
        ]:
            items = sub.get(section_key, [])
            parts.append(f"<h4>{section_label}（{len(items)}）</h4>")
            if not items:
                parts.append("<div class='empty'>—</div>")
                continue
            parts.append("<table><tr><th>代码</th><th>名称</th><th class='num'>RPS15</th><th class='num'>RPS20</th><th class='num'>RPS60</th><th class='num'>5日收益</th><th>趋势</th><th>说明</th></tr>")
            for it in items:
                parts.append(
                    f"<tr><td>{it.get('code', '')}</td><td>{it.get('name', '')}</td>"
                    f"<td class='num'>{_num(it.get('rps15'))}</td>"
                    f"<td class='num'>{_num(it.get('rps20'))}</td>"
                    f"<td class='num'>{_num(it.get('rps60'))}</td>"
                    f"<td class='num'>{_num(it.get('return_5d'))}</td>"
                    f"<td>{it.get('trend_status', '')}</td>"
                    f"<td>{it.get('reason', '')}</td></tr>")
            parts.append("</table>")

    parts.append('</div>')

    parts.append(f'<hr><div style="text-align:center;font-size:12px;color:var(--zh-muted);padding:20px 0">AKsignal · Layer ③ 交易标的筛选 · 报告自动生成于 {now_str}</div>')
    parts.append("</div></body></html>")

    html_path.write_text("\n".join(parts), encoding="utf-8")
    logger.info("selection html: %s", html_path)
    return html_path
