"""
四组对比矩阵（v0.5.2）— configured vs theme-matched × AI / 高现金流。

从各组 sensitivity JSON 聚合，产出统一对比报告：
  交易数 / 胜率 / 均值中位 / PF / 排除最强年 / Top5 贡献 / fixed_20 vs ma20。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from src.backtest.trade.metrics import CSS

GROUPS = [
    ("AI 基础设施 · 全市场关键词", "ai_infrastructure", "theme-matched"),
    ("AI 基础设施 · 固定资产池", "ai_infrastructure", "configured"),
    ("高现金流 · 全市场关键词", "high_cashflow", "theme-matched"),
    ("高现金流 · 固定资产池", "high_cashflow", "configured"),
]


def _load_group(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _pick(d: dict[str, Any], key: str, rows_key: str, label: str) -> dict[str, Any]:
    for r in d.get(rows_key, []):
        if r.get("label") == label:
            return r
    return {}


def build_matrix(sens_dir: Path, label: str) -> dict[str, Any]:
    """读取各组 sensitivity JSON，聚合对比。"""
    groups: list[dict[str, Any]] = []
    for name, theme, mode in GROUPS:
        path = sens_dir / f"sensitivity_{theme}_etf_{label}_{mode}.json"
        d = _load_group(path)
        if d is None:
            groups.append({"name": name, "theme": theme, "mode": mode,
                           "missing": True, "size": None})
            continue
        f20 = _pick(d, "fixed_20", "fixed_scan", "fixed_20")
        m20 = _pick(d, "ma20_exit", "ma_scan", "ma20_exit")
        eb = d.get("by_year", {}).get("exclude_best", {}).get("fixed_20", {})
        by_year = {r["year"]: r for r in d.get("by_year", {}).get("rows", [])
                   if r["policy"] == "fixed_20"}
        top5 = next((e.get("top5_share") for e in d.get("by_etf", [])
                     if e.get("policy") == "fixed_20"), None)
        groups.append({
            "name": name, "theme": theme, "mode": mode,
            "size": d.get("universe_size"),
            "config_hash": d.get("universe_config_hash"),
            "fixed_20": f20, "ma20": m20,
            "exclude_best": eb,
            "by_year": by_year,
            "top5_share": top5,
            "missing": False,
        })
    return {"label": label, "groups": groups}


def render_matrix_html(matrix: dict[str, Any], output_dir: Path, label: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    html_path = output_dir / f"matrix_{label}.html"

    parts = [
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='UTF-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1.0'>",
        f"<title>四组对比矩阵 · {label}</title><style>{CSS}</style></head><body><div class='container'>",
        "<h1>⑥ 交易层回测 · 四组对比矩阵（configured vs theme-matched）</h1>",
        f"<div class='subtitle'>{label} · 生成于 {now_str} · fixed_20 退出 · 独立等名义本金 · "
        "configured=固定资产池（AI 8 / HC 6），theme-matched=全市场关键词（AI 74 / HC 19）</div>",
    ]

    parts.append('<div class="section"><h2>fixed_20 四组对比</h2>')
    parts.append("<table><tr><th>组</th><th class='num'>Universe</th><th class='num'>成交</th>"
                 "<th class='num'>胜率</th><th class='num'>均值%</th><th class='num'>中位%</th>"
                 "<th class='num'>PF</th><th class='num'>排除最强年</th>"
                 "<th class='num'>Top5贡献</th></tr>")
    for g in matrix["groups"]:
        if g.get("missing"):
            parts.append(f"<tr><td>{g['name']}</td><td colspan='7' class='empty'>未生成 sensitivity 报告</td></tr>")
            continue
        f = g["fixed_20"]
        eb = g.get("exclude_best", {})
        parts.append(
            f"<tr><td>{g['name']}</td><td class='num'>{g.get('size')}</td><td class='num'>{f.get('n', 0)}</td>"
            f"<td class='num'>{_pct(f.get('win_rate'))}</td><td class='num'>{_fmt(f.get('mean_ret'))}</td>"
            f"<td class='num'>{_fmt(f.get('median_ret'))}</td><td class='num'>{_fmt(f.get('profit_factor'))}</td>"
            f"<td class='num'>{_fmt(eb.get('mean_excluding_best'))}%</td>"
            f"<td class='num'>{_pct(g.get('top5_share'))}</td></tr>")
    parts.append("</table></div>")

    parts.append('<div class="section"><h2>fixed_20 vs ma20</h2>')
    parts.append("<table><tr><th>组</th><th class='num'>fixed n</th><th class='num'>fixed 均值%</th>"
                 "<th class='num'>fixed PF</th><th class='num'>ma20 n</th><th class='num'>ma20 均值%</th>"
                 "<th class='num'>ma20 PF</th><th>优劣</th></tr>")
    for g in matrix["groups"]:
        if g.get("missing"):
            continue
        f, m = g["fixed_20"], g["ma20"]
        diff = (f.get("mean_ret") or 0) - (m.get("mean_ret") or 0)
        win = "fixed_20" if diff > 0.1 else ("ma20" if diff < -0.1 else "接近")
        parts.append(
            f"<tr><td>{g['name']}</td><td class='num'>{f.get('n', 0)}</td>"
            f"<td class='num'>{_fmt(f.get('mean_ret'))}</td><td class='num'>{_fmt(f.get('profit_factor'))}</td>"
            f"<td class='num'>{m.get('n', 0)}</td><td class='num'>{_fmt(m.get('mean_ret'))}</td>"
            f"<td class='num'>{_fmt(m.get('profit_factor'))}</td><td>{win}</td></tr>")
    parts.append("</table></div>")

    parts.append('<div class="section"><h2>分年份（fixed_20 每年均值）</h2>')
    for g in matrix["groups"]:
        if g.get("missing"):
            continue
        cells = "".join(
            f"<td class='num'>{_fmt(r.get('mean_ret'))}%"
            f"{'<span style=\"color:#2E7D32\">✓</span>' if r.get('positive') else '<span style=\"color:#C62828\">✗</span>'}</td>"
            for y, r in sorted(g["by_year"].items()))
        parts.append(f"<table><tr><th>{g['name']}</th>{''.join(
            f'<th class=\"num\">{y}</th>' for y in sorted(g['by_year']))}</tr>"
                     f"<tr><td>均值</td>{cells}</tr></table>")
    parts.append('</div>')

    parts.append(
        "<hr><div style='text-align:center;font-size:12px;color:var(--muted);padding:16px 0'>"
        "AKsignal · v0.5.2 交易层 · 固定资产池=可执行策略，theme-matched=广义主题研究基线</div>")
    parts.append("</div></body></html>")
    html_path.write_text("\n".join(parts), encoding="utf-8")
    return html_path


def _fmt(v: Any) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def _pct(v: Any) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{v * 100:.0f}%"
