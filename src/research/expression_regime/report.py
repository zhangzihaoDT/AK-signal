"""Expression Regime 研究报告 — JSON + HTML 可视化（v0.10）。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

CSS = """
:root{--blue:#174A7C;--deep:#06213D;--cyan:#7ECDEB;--light:#DDEFF8;--cream:#FFF9EF;
      --gold:#D79A36;--text:#1F2D3D;--muted:#6B7C8F;--card:#FFFFFF;--border:#E8EDF2}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Noto Sans SC",sans-serif;
     background:var(--cream);color:var(--text);line-height:1.6;padding:36px 24px}
.container{max-width:1100px;margin:0 auto}
h1{font-size:26px;font-weight:600;color:var(--deep);margin-bottom:4px}
.subtitle{font-size:13px;color:var(--muted);margin-bottom:28px}
.section{background:var(--card);border-radius:12px;box-shadow:0 1px 4px rgba(0,0,0,.06);
         padding:24px 28px;margin-bottom:22px}
.section h2{font-size:17px;font-weight:600;color:var(--blue);margin-bottom:16px;
            padding-bottom:8px;border-bottom:2px solid var(--light)}
table{width:100%;border-collapse:collapse;font-size:12.5px;margin:10px 0}
th,td{padding:6px 8px;text-align:left;border-bottom:1px solid var(--border)}
th{color:var(--muted);font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:.03em}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
tr:hover td{background:#F8FAFC}
.tag{display:inline-block;padding:1px 8px;border-radius:4px;font-size:11px;font-weight:500}
.tag-etf{background:#E3F2FD;color:#1565C0}
.tag-leader{background:#FFF3E0;color:#E65100}
.tag-combo{background:#E8F5E9;color:#2E7D32}
.insight{background:#F8FAFC;border-left:4px solid var(--cyan);border-radius:6px;padding:12px 16px;
         margin:10px 0;font-size:13px;color:var(--text)}
.empty{color:var(--muted);font-size:13px;padding:8px 0}
.good{color:#2E7D32}.bad{color:#C62828}
"""

EXPR_TAG = {
    "ETF_PRIORITY": ("ETF 优先", "tag-etf"),
    "LEADER_PRIORITY": ("龙头优先", "tag-leader"),
    "ETF_CORE_PLUS_LEADER": ("核心+卫星", "tag-combo"),
}


def _fmt(v: Any, pct: bool = False) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    if pct:
        return f"{v * 100:.1f}%"
    return f"{v:.3f}"


def _pct_or_dash(v: Any) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{v * 100:.1f}%"


def _color_delta(v: Any) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    cls = "good" if v >= 0 else "bad"
    return f"<span class='{cls}'>{v * 100:+.1f}%</span>"


def render_expression_regime_html(result: dict[str, Any], output_dir: Path, label: str) -> Path:
    by_expr = result.get("summary_by_expression", pd.DataFrame())
    overall = result.get("summary_overall", pd.DataFrame())
    events = result.get("events", pd.DataFrame())
    output_dir.mkdir(parents=True, exist_ok=True)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    src = result.get("structure_source", "tier")
    src_label = "Tier 篮子结构近似" if src == "tier" else "行业内部结构（Enrichment）"
    html_path = output_dir / f"expression_regime_{label}.html"

    parts = [
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='UTF-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1.0'>",
        f"<title>表达方式制度研究 · {label}</title><style>{CSS}</style></head><body><div class='container'>",
        "<h1>⑥ 表达方式制度研究（Expression Regime）</h1>",
        f"<div class='subtitle'>区间 {label} · 结构输入源 = {src_label} · 生成于 {now_str} · "
        "验证「市场结构判断（广泛/龙头/扩散）能否预测下一阶段更优表达」</div>",
    ]
    n_events = len(events)
    parts.append(
        f"<div class='insight'><b>事件总数 {n_events}</b> · 事件 = 主题确认日 + 结构判定日 · "
        f"水平 {', '.join(str(h) + '日' for h in result.get('horizons', []))} · "
        "命中 = 判定表达事后 ≥ 事后最优表达（任一）。</div>")

    if by_expr.empty:
        parts.append("<div class='section'><div class='empty'>无事件（检查区间 / 结构输入源 / 主题）</div></div>")
    else:
        parts.append("<div class='section'><h2>按判定表达分组</h2>")
        parts.append("<table><tr><th>表达</th><th class='num'>水平</th><th class='num'>事件数</th>"
                     "<th class='num'>非重叠</th><th class='num'>命中率</th>"
                     "<th class='num'>判定收益均值</th><th class='num'>事后最优均值</th>"
                     "<th class='num'>判错代价(均值)</th><th class='num'>相对基准超额</th>"
                     "<th class='num'>ETF−龙头(均值)</th><th class='num'>组合−单一最优</th></tr>")
        for _, r in by_expr.sort_values(["horizon", "expression"]).iterrows():
            tag = EXPR_TAG.get(r["expression"], (str(r["expression"]), "tag-combo"))
            parts.append(
                f"<tr><td><span class='tag {tag[1]}'>{tag[0]}</span></td>"
                f"<td class='num'>{int(r['horizon'])}日</td>"
                f"<td class='num'>{int(r['n_events'])}</td><td class='num'>{int(r['n_non_overlap'])}</td>"
                f"<td class='num'>{_pct_or_dash(r['hit_rate'])}</td>"
                f"<td class='num'>{_pct_or_dash(r['chosen']['mean'])}</td>"
                f"<td class='num'>{_pct_or_dash(r['best']['mean'])}</td>"
                f"<td class='num'>{_color_delta(r['delta_best']['mean'])}</td>"
                f"<td class='num'>{_pct_or_dash(r['chosen_excess']['mean'])}</td>"
                f"<td class='num'>{_color_delta(r['etf_vs_stock']['mean'])}</td>"
                f"<td class='num'>{_color_delta(r['combo_vs_single']['mean'])}</td></tr>")
        parts.append("</table></div>")

        if not overall.empty:
            parts.append("<div class='section'><h2>总体判定质量</h2>")
            parts.append("<table><tr><th class='num'>水平</th><th class='num'>事件数</th>"
                         "<th class='num'>非重叠</th><th class='num'>命中率</th>"
                         "<th class='num'>判错代价(均值)</th><th class='num'>判定收益均值</th>"
                         "<th class='num'>事后最优均值</th></tr>")
            for _, r in overall.sort_values("horizon").iterrows():
                parts.append(
                    f"<tr><td class='num'>{int(r['horizon'])}日</td>"
                    f"<td class='num'>{int(r['n_events'])}</td><td class='num'>{int(r['n_non_overlap'])}</td>"
                    f"<td class='num'>{_pct_or_dash(r['hit_rate'])}</td>"
                    f"<td class='num'>{_color_delta(r['delta_best']['mean'])}</td>"
                    f"<td class='num'>{_pct_or_dash(r['chosen']['mean'])}</td>"
                    f"<td class='num'>{_pct_or_dash(r['best']['mean'])}</td></tr>")
            parts.append("</table></div>")

        if not events.empty:
            parts.append("<div class='section'><h2>最近事件明细（前 15）</h2>")
            cols = ["trade_date", "theme_label", "expression", "etf_name", "etf_rps15",
                    "leader_name", "leader_score", "median_advance_ratio", "median_leader_contribution"]
            parts.append("<table><tr>" + "".join(f"<th>{c}</th>" for c in cols) + "</tr>")
            for _, r in events.sort_values("trade_date").tail(15).iterrows():
                tag = EXPR_TAG.get(r["expression"], (str(r["expression"]), "tag-combo"))
                cells = [
                    str(r["trade_date"]), str(r["theme_label"]),
                    f"<span class='tag {tag[1]}'>{tag[0]}</span>",
                    str(r["etf_name"]), _fmt(r["etf_rps15"], pct=False),
                    str(r["leader_name"]), _fmt(r["leader_score"], pct=False),
                    _fmt(r["median_advance_ratio"]), _fmt(r["median_leader_contribution"]),
                ]
                parts.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
            parts.append("</table></div>")

    parts.append(
        "<hr><div style='text-align:center;font-size:12px;color:var(--muted);padding:16px 0'>"
        "AKsignal · v0.10 Expression Regime · 结构输入源可插拔（tier / industry）· 不构成交易建议</div>")
    parts.append("</div></body></html>")
    html_path.write_text("\n".join(parts), encoding="utf-8")
    return html_path


def save_expression_regime_json(result: dict[str, Any], output_dir: Path, label: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"expression_regime_{label}.json"
    payload = {
        "label": label,
        "horizons": result.get("horizons", []),
        "structure_source": result.get("structure_source", "tier"),
        "n_events": int(len(result.get("events", pd.DataFrame()))),
        "summary_by_expression": result.get("summary_by_expression", pd.DataFrame()).to_dict(orient="records")
        if not result.get("summary_by_expression", pd.DataFrame()).empty else [],
        "summary_overall": result.get("summary_overall", pd.DataFrame()).to_dict(orient="records")
        if not result.get("summary_overall", pd.DataFrame()).empty else [],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path
