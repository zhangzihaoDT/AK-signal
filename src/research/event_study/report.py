"""事件研究报告 — JSON + HTML 可视化（v0.5.1）。"""

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
.tag-entry{background:#E8F5E9;color:#2E7D32}
.tag-exit{background:#FFEBEE;color:#C62828}
.insight{background:#F8FAFC;border-left:4px solid var(--cyan);border-radius:6px;padding:12px 16px;
         margin:10px 0;font-size:13px;color:var(--text)}
.empty{color:var(--muted);font-size:13px;padding:8px 0}
"""


def _fmt(v: Any, pct: bool = False) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    if pct:
        return f"{v * 100:.1f}%"
    return f"{v:.3f}"


def _state_tag(evt: str) -> str:
    return f"<span class='tag tag-entry'>入场</span>" if evt == "entry" else "<span class='tag tag-exit'>退出</span>"


def render_event_study_html(
    result: dict[str, Any],
    output_dir: Path,
    label: str,
) -> Path:
    summary = result.get("summary", pd.DataFrame())
    output_dir.mkdir(parents=True, exist_ok=True)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    html_path = output_dir / f"event_study_{label}.html"

    parts = [
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='UTF-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1.0'>",
        f"<title>事件研究 · {label}</title><style>{CSS}</style></head><body><div class='container'>",
        "<h1>⑤ 事件研究：信号状态转换的前向收益</h1>",
        f"<div class='subtitle'>区间 {label} · 生成于 {now_str} · 事件 = 状态转换（entry/exit），"
        f"非每日快照 · 基准 = 同实体宇宙横截面中位 · 绝对收益与超额收益并列</div>",
    ]

    n_events = len(result.get("events", pd.DataFrame()))
    parts.append(
        f"<div class='insight'><b>事件总数 {n_events}</b> · 层位 {result.get('layers', '')} · "
        f"水平 {', '.join(str(h) + '日' for h in result.get('horizons', []))}</div>")

    if summary.empty:
        parts.append("<div class='section'><div class='empty'>无事件（检查信号区间与层位）</div></div>")
    else:
        for (ety, evt), g in summary.groupby(["entity_type", "event_type"]):
            title = {"etf": "ETF", "industry": "行业", "stock": "个股"}.get(ety, ety)
            parts.append(f"<div class='section'><h2>{title} · {_state_tag(evt)}</h2>")
            parts.append("<table><tr><th>主题</th><th class='num'>水平</th>"
                         "<th class='num'>事件数</th><th class='num'>非重叠</th>"
                         "<th class='num'>均值收益</th><th class='num'>中位收益</th>"
                         "<th class='num'>胜率</th><th class='num'>基准均值</th>"
                         "<th class='num'>超额均值</th><th class='num'>超额中位</th>"
                         "<th class='num'>MFE均值</th><th class='num'>MAE均值</th></tr>")
            for _, r in g.iterrows():
                parts.append(
                    f"<tr><td>{r['theme']}</td><td class='num'>{int(r['horizon'])}日</td>"
                    f"<td class='num'>{int(r['n_events'])}</td><td class='num'>{int(r['n_non_overlap'])}</td>"
                    f"<td class='num'>{_fmt(r['ret']['mean'], pct=True)}</td>"
                    f"<td class='num'>{_fmt(r['ret']['median'], pct=True)}</td>"
                    f"<td class='num'>{_fmt(r['ret']['win_rate'], pct=True)}</td>"
                    f"<td class='num'>{_fmt(r['bench']['mean'], pct=True)}</td>"
                    f"<td class='num'>{_fmt(r['excess']['mean'], pct=True)}</td>"
                    f"<td class='num'>{_fmt(r['excess']['median'], pct=True)}</td>"
                    f"<td class='num'>{_fmt(r['mfe']['mean'], pct=True)}</td>"
                    f"<td class='num'>{_fmt(r['mae']['mean'], pct=True)}</td></tr>")
            parts.append("</table></div>")

    parts.append(
        "<hr><div style='text-align:center;font-size:12px;color:var(--muted);padding:16px 0'>"
        "AKsignal · v0.5.1 Event Study · 只回答「信号出现后资产是否倾向上涨」，不构成交易建议</div>")
    parts.append("</div></body></html>")
    html_path.write_text("\n".join(parts), encoding="utf-8")
    return html_path


def save_event_study_json(
    result: dict[str, Any],
    output_dir: Path,
    label: str,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"event_study_{label}.json"
    summary = result.get("summary", pd.DataFrame())
    payload = {
        "label": label,
        "horizons": result.get("horizons", []),
        "layers": result.get("layers", ""),
        "n_events": int(len(result.get("events", pd.DataFrame()))),
        "summary": summary.to_dict(orient="records") if not summary.empty else [],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path
