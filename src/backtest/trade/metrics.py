"""
交易级指标与报告（v0.5.2 第一轮）。

独立等名义本金：每笔 1 个单位。聚合指标针对「每笔收益」，不做共享账户净值曲线。
"""

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
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin:12px 0}
.metric-card{background:var(--light);border-radius:8px;padding:12px 14px;text-align:center}
.metric-value{font-size:20px;font-weight:700;color:var(--blue)}
.metric-label{font-size:12px;color:var(--muted);margin-top:2px}
.insight{background:#F8FAFC;border-left:4px solid var(--cyan);border-radius:6px;padding:12px 16px;
         margin:10px 0;font-size:13px}
.empty{color:var(--muted);font-size:13px;padding:8px 0}
"""


def compute_metrics(trades: pd.DataFrame) -> dict[str, Any]:
    """按退出策略计算交易级指标（仅 filled 且 closed 的完整交易）。"""
    out: dict[str, Any] = {"policies": {}}
    if trades.empty:
        return out
    for policy, g in trades.groupby("exit_policy"):
        filled = g[g["entry_status"] == "filled"]
        closed = filled[filled["exit_status"] == "closed"]
        ret = pd.to_numeric(closed["return_pct"], errors="coerce").dropna()
        rec: dict[str, Any] = {
            "n_trades": int(len(g)),
            "n_unfilled": int(len(g[g["entry_status"] == "unfilled"])),
            "n_open_at_end": int(len(filled[filled["exit_status"] == "open_at_end"])),
            "n_unfilled_exit": int(len(filled[filled["exit_status"] == "unfilled_exit"])),
            "n_closed": int(len(closed)),
            "unfilled_reasons": g[g["entry_status"] == "unfilled"]
                .groupby("entry_unfilled_reason")["trade_id"].count().to_dict(),
        }
        if not ret.empty:
            wins = ret[ret > 0]
            losses = ret[ret < 0]
            rec.update({
                "win_rate": round(float((ret > 0).mean()), 4),
                "mean_return_pct": round(float(ret.mean()), 4),
                "median_return_pct": round(float(ret.median()), 4),
                "p25_return_pct": round(float(ret.quantile(0.25)), 4),
                "p75_return_pct": round(float(ret.quantile(0.75)), 4),
                "max_loss_pct": round(float(ret.min()), 4),
                "max_gain_pct": round(float(ret.max()), 4),
                "avg_win_pct": round(float(wins.mean()), 4) if not wins.empty else None,
                "avg_loss_pct": round(float(losses.mean()), 4) if not losses.empty else None,
                "profit_factor": round(float(wins.sum() / abs(losses.sum())), 4)
                    if not losses.empty and losses.sum() != 0 else None,
                "total_return_units": round(float(ret.sum()), 4),
                "avg_holding_days": round(float(pd.to_numeric(
                    closed["holding_days"], errors="coerce").mean()), 1),
            })
        out["policies"][policy] = rec
    return out


def render_report_html(
    trades: pd.DataFrame,
    metrics: dict[str, Any],
    output_dir: Path,
    label: str,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    html_path = output_dir / f"backtest_{label}.html"
    policies = metrics.get("policies", {})

    parts = [
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='UTF-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1.0'>",
        f"<title>交易层回测 · {label}</title><style>{CSS}</style></head><body><div class='container'>",
        "<h1>⑥ 交易层回测：同一入场规则下的退出策略比较</h1>",
        f"<div class='subtitle'>{label} · 生成于 {now_str} · 独立等名义本金（每笔 1 单位，无共享现金账户）"
        f" · T 日信号 → T+1 开盘 · 手续费/滑点见参数</div>",
    ]

    if not policies:
        parts.append("<div class='section'><div class='empty'>无交易（检查信号、主题与入场条件）</div></div>")
    else:
        parts.append('<div class="section"><h2>退出策略对比</h2>')
        parts.append("<table><tr><th>退出策略</th><th class='num'>成交数</th><th class='num'>未成交</th>"
                     "<th class='num'>持有到期</th><th class='num'>胜率</th><th class='num'>均值收益</th>"
                     "<th class='num'>中位收益</th><th class='num'>P25</th><th class='num'>P75</th>"
                     "<th class='num'>盈亏比</th><th class='num'>最大亏损</th>"
                     "<th class='num'>累计单位收益</th><th class='num'>均持有天数</th></tr>")
        for policy, m in policies.items():
            r = m.get("mean_return_pct")
            hl = "style='color:#2E7D32'" if r is not None and r > 0 else ("style='color:#C62828'" if r is not None and r < 0 else "")
            parts.append(
                f"<tr><td>{policy}</td><td class='num'>{m.get('n_closed', 0)}</td>"
                f"<td class='num'>{m.get('n_unfilled', 0)}</td><td class='num'>{m.get('n_open_at_end', 0)}</td>"
                f"<td class='num'>{_fmt(m.get('win_rate'))}</td>"
                f"<td class='num' {hl}>{_fmt(m.get('mean_return_pct'))}%</td>"
                f"<td class='num'>{_fmt(m.get('median_return_pct'))}%</td>"
                f"<td class='num'>{_fmt(m.get('p25_return_pct'))}%</td>"
                f"<td class='num'>{_fmt(m.get('p75_return_pct'))}%</td>"
                f"<td class='num'>{_fmt(m.get('profit_factor'))}</td>"
                f"<td class='num'>{_fmt(m.get('max_loss_pct'))}%</td>"
                f"<td class='num'>{_fmt(m.get('total_return_units'))}</td>"
                f"<td class='num'>{_fmt(m.get('avg_holding_days'))}</td></tr>")
        parts.append("</table></div>")

        # 逐笔明细（每策略前 50 笔）
        if not trades.empty:
            parts.append('<div class="section"><h2>逐笔交易（前 50 笔/策略）</h2>')
            parts.append("<table><tr><th>策略</th><th>代码</th><th>名称</th>"
                         "<th class='num'>入场信号</th><th class='num'>入场日</th><th class='num'>入场价</th>"
                         "<th class='num'>出场日</th><th class='num'>出场价</th><th>状态</th>"
                         "<th class='num'>收益%</th><th class='num'>持有</th></tr>")
            for policy, g in trades.groupby("exit_policy"):
                for _, t in g.head(50).iterrows():
                    parts.append(
                        f"<tr><td>{t['exit_policy']}</td><td>{t['entity_code']}</td><td>{t['entity_name']}</td>"
                        f"<td class='num'>{t['entry_signal_date']}</td><td class='num'>{t['entry_fill_date']}</td>"
                        f"<td class='num'>{t['entry_fill_price']}</td><td class='num'>{t['exit_fill_date']}</td>"
                        f"<td class='num'>{t['exit_fill_price']}</td><td>{t['exit_status']}</td>"
                        f"<td class='num'>{_fmt(t.get('return_pct'))}%</td><td class='num'>{t.get('holding_days')}</td></tr>")
            parts.append("</table></div>")

    parts.append(
        "<hr><div style='text-align:center;font-size:12px;color:var(--muted);padding:16px 0'>"
        "AKsignal · v0.5.2 Trade Simulation · 独立等名义本金，不含组合风险优化</div>")
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
    return f"{v * 100:.1f}%"


def render_sensitivity_html(
    result: dict[str, Any],
    output_dir: Path,
    label: str,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    html_path = output_dir / f"sensitivity_{label}.html"

    parts = [
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='UTF-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1.0'>",
        f"<title>退出规则稳健性 · {label}</title><style>{CSS}</style></head><body><div class='container'>",
        "<h1>⑥ 退出规则稳健性验证（v0.5.2 第二轮）</h1>",
        f"<div class='subtitle'>{label} · 生成于 {now_str} · 目的不是找最优参数，"
        f"而是验证是否存在稳定的参数平台区间 · 独立等名义本金</div>",
    ]
    parts.append(
        f"<div class='insight'><b>Universe：</b>{result.get('universe_mode', '—')}"
        f"（{result.get('universe_size', '—')} 只 ETF）· config_hash "
        f"{result.get('universe_config_hash', '—')}</div>")

    # 1. fixed scan
    parts.append('<div class="section"><h2>① 固定持有期扫描</h2>')
    parts.append("<table><tr><th>持有期</th><th class='num'>成交</th><th class='num'>胜率</th>"
                 "<th class='num'>均值%</th><th class='num'>中位%</th><th class='num'>盈亏比</th>"
                 "<th class='num'>累计单位</th><th class='num'>均持有</th><th class='num'>笔/年</th></tr>")
    for r in result["fixed_scan"]:
        parts.append(f"<tr><td>{r['label']}</td><td class='num'>{r['n']}</td>"
                     f"<td class='num'>{_pct(r.get('win_rate'))}</td><td class='num'>{_fmt(r.get('mean_ret'))}</td>"
                     f"<td class='num'>{_fmt(r.get('median_ret'))}</td><td class='num'>{_fmt(r.get('profit_factor'))}</td>"
                     f"<td class='num'>{_fmt(r.get('total_units'))}</td><td class='num'>{_fmt(r.get('avg_holding_days'))}</td>"
                     f"<td class='num'>{_fmt(r.get('trades_per_year'))}</td></tr>")
    parts.append("</table></div>")

    # 2. ma scan
    parts.append('<div class="section"><h2>② MA 参数扫描</h2>')
    parts.append("<table><tr><th>窗口</th><th class='num'>成交</th><th class='num'>胜率</th>"
                 "<th class='num'>均值%</th><th class='num'>中位%</th><th class='num'>盈亏比</th>"
                 "<th class='num'>累计单位</th><th class='num'>大盈利贡献</th>"
                 "<th class='num'>均持有</th><th class='num'>笔/年</th></tr>")
    for r in result["ma_scan"]:
        parts.append(f"<tr><td>{r['label']}</td><td class='num'>{r['n']}</td>"
                     f"<td class='num'>{_pct(r.get('win_rate'))}</td><td class='num'>{_fmt(r.get('mean_ret'))}</td>"
                     f"<td class='num'>{_fmt(r.get('median_ret'))}</td><td class='num'>{_fmt(r.get('profit_factor'))}</td>"
                     f"<td class='num'>{_fmt(r.get('total_units'))}</td><td class='num'>{_pct(r.get('big_win_share'))}</td>"
                     f"<td class='num'>{_fmt(r.get('avg_holding_days'))}</td>"
                     f"<td class='num'>{_fmt(r.get('trades_per_year'))}</td></tr>")
    parts.append("</table></div>")

    # 3. by year
    by_year = result["by_year"]
    parts.append('<div class="section"><h2>③ 分年份验证</h2>')
    parts.append("<table><tr><th>策略</th><th class='num'>年份</th><th class='num'>成交</th>"
                 "<th class='num'>胜率</th><th class='num'>均值%</th><th class='num'>累计单位</th><th>为正</th></tr>")
    for r in by_year["rows"]:
        pos = "✅" if r.get("positive") else "❌"
        parts.append(f"<tr><td>{r['policy']}</td><td class='num'>{r['year']}</td><td class='num'>{r['n']}</td>"
                     f"<td class='num'>{_pct(r.get('win_rate'))}</td><td class='num'>{_fmt(r.get('mean_ret'))}</td>"
                     f"<td class='num'>{_fmt(r.get('total_units'))}</td><td>{pos}</td></tr>")
    parts.append("</table>")
    if by_year["exclude_best"]:
        parts.append("<div class='insight'><b>排除最强年份后：</b><br>")
        for policy, eb in by_year["exclude_best"].items():
            parts.append(f"<small>{policy}：最强年 {eb['best_year']}（均值 {_fmt(eb['best_mean'])}%）"
                         f"；排除后均值 {_fmt(eb['mean_excluding_best'])}%（{eb['n_years_excluding_best']} 年）</small><br>")
        parts.append("</div>")
    parts.append('</div>')

    # 4. by etf
    parts.append('<div class="section"><h2>④ 分 ETF 验证（Top 5 贡献占比）</h2>')
    for e in result["by_etf"]:
        parts.append(f"<div class='insight'><b>{e['policy']}</b>：{e['n_entities']} 只 ETF，"
                     f"Top5 贡献占比 <b>{_pct(e.get('top5_share'))}</b></div>")
        parts.append("<table><tr><th>ETF</th><th class='num'>成交</th><th class='num'>均值%</th>"
                     "<th class='num'>累计单位</th><th class='num'>贡献占比</th></tr>")
        for t in e["top"]:
            parts.append(f"<tr><td>{t['entity_code']}</td><td class='num'>{t['n']}</td>"
                         f"<td class='num'>{_fmt(t.get('mean_ret'))}</td><td class='num'>{_fmt(t.get('total_units'))}</td>"
                         f"<td class='num'>{_pct(t.get('share'))}</td></tr>")
        parts.append("</table>")
    parts.append('</div>')

    # 5. cost scan
    parts.append('<div class="section"><h2>⑤ 成本敏感性（bp = 双边合计）</h2>')
    parts.append("<table><tr><th>策略</th><th class='num'>成本bp</th><th class='num'>成交</th>"
                 "<th class='num'>胜率</th><th class='num'>均值%</th><th class='num'>累计单位</th></tr>")
    for r in result["cost_scan"]:
        parts.append(f"<tr><td>{r['base']}</td><td class='num'>{r['cost_bp']}</td><td class='num'>{r['n']}</td>"
                     f"<td class='num'>{_pct(r.get('win_rate'))}</td><td class='num'>{_fmt(r.get('mean_ret'))}</td>"
                     f"<td class='num'>{_fmt(r.get('total_units'))}</td></tr>")
    parts.append("</table></div>")

    parts.append(
        "<hr><div style='text-align:center;font-size:12px;color:var(--muted);padding:16px 0'>"
        "AKsignal · v0.5.2 退出规则稳健性验证 · 不构成交易建议</div>")
    parts.append("</div></body></html>")
    html_path.write_text("\n".join(parts), encoding="utf-8")
    return html_path
