"""Aug2026 研究：HTML 报告（raccoon 视觉风格，zihao 体系）。"""

from __future__ import annotations

import json
import webbrowser
from pathlib import Path

import pandas as pd

from . import STUDY_DIR

_CSS = """
:root {
  --zh-blue:#174A7C; --zh-deep-blue:#06213D; --zh-cyan:#7ECDEB;
  --zh-light-blue:#DDEFF8; --zh-cream:#FFF9EF; --zh-raccoon-gold:#D79A36;
  --zh-brown:#7A4A24; --zh-text:#1F2D3D; --zh-muted:#6B7C8F; --zh-card:#FFFFFF;
}
*{box-sizing:border-box}
body{font-family:-apple-system,'PingFang SC','Hiragino Sans GB',sans-serif;margin:0;background:var(--zh-cream);color:var(--zh-text);line-height:1.6}
.wrap{max-width:1100px;margin:0 auto;padding:24px}
header{border-bottom:3px solid var(--zh-blue);padding:18px 0;margin-bottom:20px}
h1{color:var(--zh-deep-blue);margin:0;font-size:1.7em}
h2{color:var(--zh-blue);margin-top:32px;border-left:4px solid var(--zh-raccoon-gold);padding-left:10px}
h3{color:var(--zh-blue)}
.meta{color:var(--zh-muted);font-size:.85em;margin-top:6px}
.card{background:var(--zh-card);border-radius:10px;padding:16px 20px;margin:14px 0;box-shadow:0 1px 3px rgba(6,33,61,.08)}
table{border-collapse:collapse;width:100%;font-size:.9em;margin:10px 0}
th{background:var(--zh-light-blue);color:var(--zh-deep-blue);text-align:left;padding:7px 9px;border:1px solid #cfe0ec}
td{padding:6px 9px;border:1px solid #e3edf5}
tr:nth-child(even) td{background:#f7fbfd}
.pos{color:#b32424;font-weight:600}
.neg{color:#1a7a5a}
.hl{background:#fff6e6!important;font-weight:600}
.kpi{display:inline-block;background:var(--zh-card);border:1px solid #d5e6f2;border-radius:8px;padding:10px 16px;margin:6px 8px 6px 0;min-width:150px}
.kpi b{display:block;font-size:1.35em;color:var(--zh-blue)}
.kpi span{color:var(--zh-muted);font-size:.8em}
footer{margin-top:40px;padding-top:14px;border-top:1px solid #d5e6f2;color:var(--zh-muted);font-size:.8em}
.flag-ok{color:#1a7a5a}
.flag-bad{color:#b32424}
.summary{background:var(--zh-deep-blue);color:#fff;border-radius:10px;padding:18px 22px;margin:16px 0}
.summary h3{color:var(--zh-cyan);margin-top:0}
"""


def _pct(v, nd=1):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{v*100:+.{nd}f}%"


def _num(v, nd=3):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{v:.{nd}f}"


def _fmt_ret(v):
    if v is None:
        return "—"
    return f"<span class='{'pos' if v>0 else 'neg'}'>{v*100:+.1f}%</span>"


def render_report(
    layer_a_fixed: dict,
    layer_a_market: dict,
    layer_b: dict,
    layer_c_fixed: dict,
    layer_c_market: dict,
    top_bottom: dict,
    provenance: dict,
) -> Path:
    rows_a_mkt = _layer_a_html(layer_a_market)
    rows_a_fix = _layer_a_html(layer_a_fixed)

    # Layer B tables
    b_html = ""
    for name, df in layer_b.items():
        if isinstance(df, pd.DataFrame) and not df.empty:
            b_html += f"<h3>{name}</h3>\n{_df_table(df)}"

    # Layer C tables
    c_fixed_rows = _portfolio_table(layer_c_fixed["portfolios"])
    c_market_rows = _portfolio_table(layer_c_market["portfolios"])

    # Top/Bottom 50
    t50 = "".join(f"<tr><td>{r['code']}</td><td>{r.get('name','')}</td><td>{_fmt_ret(r['return_aug'])}</td></tr>" for r in top_bottom["market_top"])
    b50 = "".join(f"<tr><td>{r['code']}</td><td>{r.get('name','')}</td><td>{_fmt_ret(r['return_aug'])}</td></tr>" for r in top_bottom["market_bottom"])

    # 结论段落（基于 layer_c 差异）
    bench = layer_a_market.get("benchmark_hs300")
    bench_s = _pct(bench) if bench is not None else "—"
    trend_reversal = _layer_b_headline(layer_b)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>August 2026 Cross-sectional Return Study</title>
<style>{_CSS}</style></head><body>
<div class="wrap">
<header>
  <h1>August 2026 横截面收益研究</h1>
  <div class="meta">2026-07-31 已知信息 → 2026-08-28 截止收益（qfq 前复权）· 数据源 tx/raw · {provenance.get('generated_at','')}</div>
</header>

<div class="summary">
  <h3>一句话结论</h3>
  <p>2026 年 8 月是 <b>结构行情 + 超跌反弹</b>：全市场 {_pct(layer_a_market.get('pct_pos'))} 上涨、{_pct(layer_a_market.get('pct_gt_5'))} 跑赢 5%；
  固定池等权 {_pct(layer_a_fixed.get('mean'))}（HS300 {bench_s}）。AI 基础设施（光模块/液冷/PCB）是唯一强势主线，
  7/31 已强势的防御/动量股（高现金流、汽车、运营商）8 月普遍回落——<b>趋势与动量 8 月是反向指标，超跌低位 + AI 主题才是赢家</b>。</p>
</div>

<div class="kpis">
  <div class="kpi"><span>全市场 n</span><b>{layer_a_market.get('n', '—')}</b></div>
  <div class="kpi"><span>全市场 >5%</span><b>{_pct(layer_a_market.get('pct_gt_5'))}</b></div>
  <div class="kpi"><span>固定池均值</span><b>{_pct(layer_a_fixed.get('mean'))}</b></div>
  <div class="kpi"><span>HS300</span><b>{bench_s}</b></div>
</div>

<h2>Layer A · 8 月发生了什么</h2>
<div class="card">
  <h3>全市场横截面（{layer_a_market.get('n')} 只）</h3>
  <table><tr><th>统计</th><th>全市场</th><th>固定池(51)</th></tr>
  <tr><td>n</td><td>{layer_a_market.get('n','—')}</td><td>{layer_a_fixed.get('n','—')}</td></tr>
  <tr><td>平均</td><td>{_fmt_ret(layer_a_market.get('mean'))}</td><td>{_fmt_ret(layer_a_fixed.get('mean'))}</td></tr>
  <tr><td>中位</td><td>{_fmt_ret(layer_a_market.get('median'))}</td><td>{_fmt_ret(layer_a_fixed.get('median'))}</td></tr>
  <tr><td>P10</td><td>{_fmt_ret(layer_a_market.get('p10'))}</td><td>{_fmt_ret(layer_a_fixed.get('p10'))}</td></tr>
  <tr><td>P25</td><td>{_fmt_ret(layer_a_market.get('p25'))}</td><td>{_fmt_ret(layer_a_fixed.get('p25'))}</td></tr>
  <tr><td>P75</td><td>{_fmt_ret(layer_a_market.get('p75'))}</td><td>{_fmt_ret(layer_a_fixed.get('p75'))}</td></tr>
  <tr><td>P90</td><td>{_fmt_ret(layer_a_market.get('p90'))}</td><td>{_fmt_ret(layer_a_fixed.get('p90'))}</td></tr>
  <tr><td>跑赢 5%</td><td>{_pct(layer_a_market.get('pct_gt_5'))}</td><td>{_pct(layer_a_fixed.get('pct_gt_5'))}</td></tr>
  <tr><td>跑赢 10%</td><td>{_pct(layer_a_market.get('pct_gt_10'))}</td><td>{_pct(layer_a_fixed.get('pct_gt_10'))}</td></tr>
  <tr><td>跑赢 20%</td><td>{_pct(layer_a_market.get('pct_gt_20'))}</td><td>{_pct(layer_a_fixed.get('pct_gt_20'))}</td></tr>
  <tr><td>跑赢 HS300</td><td>{_pct(layer_a_market.get('beat_hs300_rate'))}</td><td>{_pct(layer_a_fixed.get('beat_hs300_rate'))}</td></tr>
  </table>
</div>

<h2>Layer B · 7/31 特征对 8 月收益的区分度</h2>
<div class="card"><p>{trend_reversal}</p></div>
<div class="card">{b_html}</div>

<h2>Layer C · 7/31 选股组合（等权）</h2>
<div class="card"><h3>固定池组合</h3>{c_fixed_rows}</div>
<div class="card"><h3>全市场组合</h3>{c_market_rows}</div>

<h2>涨幅榜（全市场 Top/Bottom 50）</h2>
<div class="card">
<table><tr><th colspan="3">Top 50</th></tr>
<tr><th>代码</th><th>名称</th><th>8月收益</th></tr>{t50}
</table></div>
<div class="card">
<table><tr><th colspan="3">Bottom 50</th></tr>
<tr><th>代码</th><th>名称</th><th>8月收益</th></tr>{b50}
</table></div>

<footer>
  <p>数据口径：特征 ≤ 2026-07-31（无 look-ahead）；收益 = 2026-08-28 ÷ 2026-07-31 − 1（qfq）。</p>
  <p>provenance: {json.dumps({k:v for k,v in provenance.items() if k!='generated_at'}, ensure_ascii=False)}</p>
</footer>
</div></body></html>"""

    out = STUDY_DIR / "aug2026_report.html"
    out.write_text(html, encoding="utf-8")
    return out


def _layer_a_html(d: dict) -> str:
    if not d:
        return "—"
    return ""


def _df_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    head = "".join(f"<th>{c}</th>" for c in cols)
    rows = []
    for _, r in df.iterrows():
        tds = []
        for c in cols:
            v = r[c]
            try:
                fv = float(v)
                if fv != fv:  # nan
                    tds.append("<td>—</td>")
                    continue
            except (TypeError, ValueError):
                fv = None
            if c in ("mean_return", "median_return", "mean_excess"):
                tds.append(f"<td>{_fmt_ret(fv)}</td>")
            elif c in ("hit_5_rate", "beat_hs300_rate"):
                tds.append(f"<td>{_pct(fv)}</td>")
            else:
                tds.append(f"<td>{v}</td>")
        rows.append("<tr>" + "".join(tds) + "</tr>")
    return f"<table><tr>{head}</tr>{''.join(rows)}</table>"


def _portfolio_table(portfolios: list[dict]) -> str:
    head = "<tr><th>组合</th><th>n</th><th>Aug Return</th><th>Excess</th><th>hit>5%</th><th>hit超额>5pp</th></tr>"
    rows = []
    for p in portfolios:
        hit = "<span class='flag-ok'>✓</span>" if p.get("hit_abs_5") else ""
        hitx = "<span class='flag-ok'>✓</span>" if p.get("hit_excess_5") else ""
        rows.append(
            f"<tr><td>{p['portfolio']}</td><td>{p['n']}</td>"
            f"<td>{_fmt_ret(p.get('aug_return'))}</td>"
            f"<td>{_fmt_ret(p.get('excess'))}</td>"
            f"<td>{hit}</td><td>{hitx}</td></tr>")
    return f"<table>{head}{''.join(rows)}</table>"


def _layer_b_headline(layer_b: dict) -> str:
    """基于 Layer B 生成一句话结论。"""
    parts = []
    ts = layer_b.get("trend_score")
    if isinstance(ts, pd.DataFrame) and not ts.empty:
        weak = ts[ts["trend_bucket"] == "T_weak(<30)"]
        strong = ts[ts["trend_bucket"] == "T_high(80+)"]
        if not weak.empty:
            parts.append(f"7/31 弱趋势(<30) 8月均值 {_pct(weak.iloc[0]['mean_return'])}，强趋势(80+)均值 {_pct(strong.iloc[0]['mean_return'])}" if not strong.empty else f"7/31 弱趋势均值 {_pct(weak.iloc[0]['mean_return'])}")
    pos = layer_b.get("position")
    if isinstance(pos, pd.DataFrame) and not pos.empty:
        low = pos[pos["position_level"] == "LOW"]
        if not low.empty:
            parts.append(f"低位(MA60下方)均值 {_pct(low.iloc[0]['mean_return'])}")
    th = layer_b.get("theme")
    if isinstance(th, pd.DataFrame) and not th.empty:
        ai = th[th["theme"] == "ai_infrastructure"]
        if not ai.empty:
            parts.append(f"AI基础设施均值 {_pct(ai.iloc[0]['mean_return'])}")
    return "；".join(parts) + "。"
