"""v0.6 组合账户报告 — NAV 曲线（SVG）+ 指标表。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from src.backtest.metrics import CSS


def _svg_curves(curves: dict[str, pd.DataFrame], width: int = 1000, height: int = 320) -> str:
    """多条净值曲线归一化到初始值 1.0 的 SVG 折线图。"""
    palette = ["#174A7C", "#D79A36", "#2E7D32", "#C62828", "#6A1B9A", "#00838F"]
    all_dates: set[str] = set()
    series: dict[str, list[tuple[str, float]]] = {}
    for label, nav in curves.items():
        if nav.empty:
            continue
        nav = nav.copy()
        base = float(nav["equity"].iloc[0]) or 1.0
        nav["norm"] = nav["equity"] / base
        series[label] = list(zip(nav["date"].astype(str), nav["norm"]))
        all_dates.update(d for d, _ in series[label])
    if not series:
        return "<div class='empty'>无净值数据</div>"

    xs = sorted(all_dates)
    x_ord = {d: i for i, d in enumerate(xs)}
    n = max(len(xs), 1)
    pad_l, pad_r, pad_t, pad_b = 60, 20, 16, 28
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b

    y_vals = [v for s in series.values() for _, v in s]
    ymin, ymax = min(y_vals), max(y_vals)
    if ymax - ymin < 1e-6:
        ymax, ymin = ymax + 0.5, ymin - 0.5
    span = ymax - ymin

    def px(d: str) -> float:
        return pad_l + x_ord[d] / (n - 1) * plot_w if n > 1 else pad_l + plot_w / 2

    def py(v: float) -> float:
        return pad_t + (ymax - v) / span * plot_h

    parts = [f"<svg width='{width}' height='{height}' viewBox='0 0 {width} {height}' "
             "xmlns='http://www.w3.org/2000/svg' style='background:#FDFBF7'>"]
    # 网格 + y 轴
    for i in range(6):
        v = ymin + span * i / 5
        y = py(v)
        parts.append(f"<line x1='{pad_l}' y1='{y:.1f}' x2='{width - pad_r}' y2='{y:.1f}' "
                     f"stroke='#E8EDF2' stroke-width='1'/>")
        parts.append(f"<text x='{pad_l - 6}' y='{y + 4:.1f}' text-anchor='end' "
                     f"font-size='10' fill='#6B7C8F'>{v:.2f}</text>")
    parts.append(f"<text x='{pad_l}' y='{pad_t - 6}' font-size='11' fill='#6B7C8F'>归一化净值（起始=1.0）</text>")

    for (label, pts), color in zip(series.items(), palette):
        if len(pts) == 1:
            px0, py0 = px(pts[0][0]), py(pts[0][1])
            parts.append(f"<circle cx='{px0:.1f}' cy='{py0:.1f}' r='2.5' fill='{color}'/>")
            continue
        path = " ".join(
            f"{'M' if i == 0 else 'L'}{px(d):.1f},{py(v):.1f}" for i, (d, v) in enumerate(pts))
        parts.append(f"<path d='{path}' fill='none' stroke='{color}' stroke-width='1.6'/>")
    # 图例
    lx = pad_l + 10
    for (label, _), color in zip(series.items(), palette):
        parts.append(f"<rect x='{lx}' y='{height - 16}' width='12' height='3' fill='{color}'/>")
        parts.append(f"<text x='{lx + 15}' y='{height - 11}' font-size='10' fill='#1F2D3D'>{label}</text>")
        lx += 18 + len(label) * 6
    parts.append("</svg>")
    return "".join(parts)


def render_portfolio_html(
    result: dict[str, Any],
    output_dir: Path,
    label: str,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    html_path = output_dir / f"portfolio_{label}.html"

    curves: dict[str, pd.DataFrame] = {}
    rows: list[dict[str, Any]] = []
    items: list[tuple[str, str]] = []
    for key, v in result["single"].items():
        items.append((key, v["label"]))
    for mode, v in result["combined"].items():
        items.append((f"combined_{mode}", v["label"]))

    for key, lbl in items:
        acc = result["single"].get(key, {}).get("account") or result["combined"].get(key, {}).get("account")
        if acc is None:
            continue
        nav = acc.nav_frame()
        curves[lbl] = nav
        m = nav_metrics(nav)
        rows.append({"label": lbl, "n_filled": result["single"].get(key, result["combined"].get(key, {}))
                     .get("n_filled", 0), **m})

    params = result["params"]
    parts = [
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='UTF-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1.0'>",
        f"<title>组合账户回测 · {label}</title><style>{CSS}</style></head><body><div class='container'>",
        "<h1>⑦ 组合账户回测（v0.6 共享现金账户）</h1>",
        f"<div class='subtitle'>{label} · 生成于 {now_str} · 初始资金 {params['initial_capital']:,.0f}"
        f" · 最大持仓 {params['max_positions']} · 单资产上限 {params['max_weight_per_asset']:.0%}"
        f" · 费+滑点 {params['fee_pct']:.2f}% + {params['slippage_pct']:.2f}%（单边）</div>",
    ]

    parts.append('<div class="section"><h2>净值曲线（归一化，起始=1.0）</h2>')
    parts.append(_svg_curves(curves))
    parts.append('</div>')

    parts.append('<div class="section"><h2>账户指标</h2>')
    parts.append("<table><tr><th>组合</th><th class='num'>成交</th><th class='num'>总收益</th>"
                 "<th class='num'>年化</th><th class='num'>最大回撤</th><th class='num'>Sharpe</th>"
                 "<th class='num'>年化波动</th><th class='num'>净值日数</th></tr>")
    for r in rows:
        parts.append(
            f"<tr><td>{r['label']}</td><td class='num'>{r['n_filled']}</td>"
            f"<td class='num'>{_fmt(r.get('total_return_pct'))}%</td>"
            f"<td class='num'>{_fmt(r.get('annualized_pct'))}%</td>"
            f"<td class='num'>{_fmt(r.get('max_drawdown_pct'))}%</td>"
            f"<td class='num'>{_fmt(r.get('sharpe'))}</td>"
            f"<td class='num'>{_fmt(r.get('volatility_pct'))}%</td>"
            f"<td class='num'>{r.get('n_days')}</td></tr>")
    parts.append("</table></div>")

    parts.append('<div class="section"><h2>资金规则</h2>')
    parts.append("<table><tr><th>规则</th><th>值</th></tr>")
    for k, v in params.items():
        parts.append(f"<tr><td>{k}</td><td>{v}</td></tr>")
    parts.append("</table></div>")

    parts.append(
        "<hr><div style='text-align:center;font-size:12px;color:var(--muted);padding:16px 0'>"
        "AKsignal · v0.6 组合账户 · 先看自然结果，不做权重优化 · 不构成交易建议</div>")
    parts.append("</div></body></html>")
    html_path.write_text("\n".join(parts), encoding="utf-8")
    return html_path


def save_portfolio_json(
    result: dict[str, Any],
    output_dir: Path,
    label: str,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"portfolio_{label}.json"
    payload: dict[str, Any] = {"label": label, "params": result["params"], "accounts": {}}
    for key, v in result["single"].items():
        acc = v["account"]
        payload["accounts"][v["label"]] = {
            "theme": v["theme"], "n_filled": v["n_filled"], "n_trades": v["n_trades"],
            "nav": acc.nav_frame().to_dict(orient="records"),
            "metrics": nav_metrics(acc.nav_frame()),
            "orders": acc.orders,
        }
    for mode, v in result["combined"].items():
        acc = v["account"]
        payload["accounts"][v["label"]] = {
            "n_filled": v["n_filled"], "n_trades": v["n_trades"],
            "nav": acc.nav_frame().to_dict(orient="records"),
            "metrics": nav_metrics(acc.nav_frame()),
            "orders": acc.orders,
        }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def _fmt(v: Any) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{v:.2f}"


# 复用 simulate.nav_metrics（避免循环导入）
from .simulate import nav_metrics  # noqa: E402
