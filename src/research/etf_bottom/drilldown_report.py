"""Study 2A Current Bottom ETF Drilldown HTML 报告（zihao raccoon 视觉体系）。

结构：
  一句话结论 + 三分类汇总
  每只 ETF 的历史赔率表（20D/60D/120D 均值/中位/胜率 + 先例数）
  按支持级别分组明细
  口径与限定
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

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
table{border-collapse:collapse;width:100%;font-size:.86em;margin:10px 0}
th{background:var(--zh-light-blue);color:var(--zh-deep-blue);text-align:left;padding:7px 9px;border:1px solid #cfe0ec}
td{padding:6px 9px;border:1px solid #e3edf5}
tr:nth-child(even) td{background:#f7fbfd}
.pos{color:#b32424;font-weight:600}
.neg{color:#1a7a5a}
.kpi{display:inline-block;background:var(--zh-card);border:1px solid #d5e6f2;border-radius:8px;padding:10px 16px;margin:6px 8px 6px 0;min-width:150px}
.kpi b{display:block;font-size:1.35em;color:var(--zh-blue)}
.kpi span{color:var(--zh-muted);font-size:.8em}
footer{margin-top:40px;padding-top:14px;border-top:1px solid #d5e6f2;color:var(--zh-muted);font-size:.8em}
.summary{background:var(--zh-deep-blue);color:#fff;border-radius:10px;padding:18px 22px;margin:16px 0}
.summary h3{color:var(--zh-cyan);margin-top:0}
.flag-ok{color:#1a7a5a}.flag-bad{color:#b32424}
.tag{display:inline-block;padding:1px 8px;border-radius:8px;font-size:.75em}
.tag-ok{background:#d8f0e0;color:#1a7a5a}.tag-bad{background:#f7dddd;color:#b32424}.tag-warn{background:#fff3d6;color:#7a5a00}
"""


def _fmt_ret(v, nd=1):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    cls = "pos" if v > 0 else ("neg" if v < 0 else "")
    return f"<span class='{cls}'>{v*100:+.{nd}f}%</span>"


def _ret_cell(st: dict, show_med: bool = False) -> str:
    if not st or st.get("mean") is None:
        return "—"
    small = ""
    if show_med and st.get("median") is not None:
        small = f"<br><small>中位 {_fmt_ret(st['median'])} · 胜率 {st.get('win_rate', 0)*100:.0f}% · n={st.get('n', 0)}</small>"
    return _fmt_ret(st["mean"]) + small


def _etf_table(etfs: list[dict], states: tuple) -> str:
    sub = [r for r in etfs if r["support_level"] in states]
    if not sub:
        return "<p>无该组 ETF</p>"
    rows = ""
    for r in sub:
        tag_cls = {"历史支持": "tag-ok", "历史不支持": "tag-bad", "证据不足": "tag-warn"}.get(r["support_level"], "tag-warn")
        cells = ""
        for h in ("20", "60", "120"):
            st = r["hist"].get(h, {})
            cells += f"<td>{_ret_cell(st, show_med=True)}</td>"
        cur = r.get("current", {})
        rows += (
            f"<tr><td>{r['fund_code']}</td><td>{r['fund_name']}</td><td>{r['etf_type']}</td>"
            f"<td>{r['n_hist_entries']}</td>"
            f"{cells}"
            f"<td>{cur.get('days_in_current_bottom', 0)}</td>"
            f"<td>{cur.get('current_price_pos_120', '—')}</td>"
            f"<td>{cur.get('current_price_pos_360', '—')}</td>"
            f"<td><span class='tag {tag_cls}'>{r['support_level']}</span></td></tr>"
        )
    return f"""<table>
<thead><tr><th>代码</th><th>名称</th><th>类型</th><th>历史先例</th>
<th colspan='2'>20D 历史</th><th colspan='2'>60D 历史</th><th colspan='2'>120D 历史</th>
<th>当前低位天数</th><th>当前120D位置</th><th>当前360D位置</th><th>支持级别</th></tr>
<tr><th></th><th></th><th></th><th></th><th>均值</th><th>中位/胜率</th><th>均值</th><th>中位/胜率</th><th>均值</th><th>中位/胜率</th><th></th><th></th><th></th><th></th></tr></thead>
<tbody>{rows}</tbody></table>"""


def render_drilldown(payload: dict, out_path: Path | None = None) -> Path:
    out_path = out_path or (STUDY_DIR / "state_odds_drilldown.html")
    etfs = payload["etfs"]
    sm = payload["support_summary"]
    n_ok = sm.get("历史支持", 0)
    n_bad = sm.get("历史不支持", 0)
    n_warn = sm.get("证据不足", 0)

    ok_names = [r["fund_name"] for r in etfs if r["support_level"] == "历史支持"]
    headline = (f"当前 {payload['n_etfs']} 只长期底部 ETF 中，{n_ok} 只有历史支持"
                f"（自身多次低位先例后 120D 中位收益为正且胜率≥50%），"
                f"{n_bad} 只历史不支持（低位先例后中位收益≤0），{n_warn} 只证据不足（先例过近或不足）。")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Study 2A · Current Bottom ETF Drilldown</title>
<style>{_CSS}</style></head><body>
<div class="wrap">
<header>
  <h1>Study 2A · Current Bottom ETF Drilldown</h1>
  <div class="meta">对 {payload['n_etfs']} 只当前长期底部 ETF 逐只回看历史低位先例的前向收益 · as_of={payload.get('as_of', '2026-08-28')} · {payload.get('generated_at', '')}</div>
</header>

<div class="summary">
  <h3>一句话结论</h3>
  <p>{headline}</p>
</div>

<div class="kpis">
  <div class="kpi"><span>历史支持</span><b>{n_ok}</b></div>
  <div class="kpi"><span>历史不支持</span><b>{n_bad}</b></div>
  <div class="kpi"><span>证据不足</span><b>{n_warn}</b></div>
</div>

<h2>一、有历史支持的低位（先例后 120D 中位为正、胜率≥50%）</h2>
<div class="card">{_etf_table(etfs, ("历史支持",))}
<p class="meta">历史支持 = 该 ETF 自身 ≥2 次进入长期底部的先例，且 120D 前向收益中位 >0、胜率 ≥50%。这是「有历史支持的低位」。</p></div>

<h2>二、历史不支持（只是看起来便宜）</h2>
<div class="card">{_etf_table(etfs, ("历史不支持",))}
<p class="meta">历史不支持 = 先例 ≥2，但 120D 前向收益中位 ≤0 或胜率 <50%——历史上低位后并不反弹。</p></div>

<h2>三、证据不足（先例过近/不足）</h2>
<div class="card">{_etf_table(etfs, ("证据不足",))}
<p class="meta">证据不足 = 先例 <2，或先例全在近期（120D 前向窗口未到期，n=0）。无法用自身历史判断。</p></div>

<h2>四、口径与限定</h2>
<div class="card">
<ul>
<li>事件 = 该 ETF 全历史上 long_term_bottom（120D 且 360D 位置≤20%）的 off→on 转换；当前仍在状态内的段不计入历史先例</li>
<li>前向收益 close→close，20/60/120 交易日，窗口严格 ≤ 事件日，无 look-ahead</li>
<li>支持级别基于 120D 中位收益与胜率；不同 ETF 先例数量差异大（n_hist_entries 列）</li>
<li>只回答「这一只自身历史上低位之后如何」，不构成当前买入建议；跨市场环境（2024/2025 普涨年）的影响见 Study 2 时间代表性警示</li>
</ul>
</div>

<footer>AKsignal · Lane 2 Research · Study 2A Current Bottom ETF Drilldown · {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</footer>
</div></body></html>"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path
