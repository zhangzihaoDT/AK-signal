"""Study 2 Price Bottom State Odds HTML 报告（zihao raccoon 视觉体系）。

结构：
  一句话结论 + 三状态 × 三 horizon 核心表（含/剔除折算两列）
  按 ETF 类型分层
  按年份分层（跨年份稳健性）
  MAE / MFE / excess
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
table{border-collapse:collapse;width:100%;font-size:.88em;margin:10px 0}
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
"""

_STATE_LABEL = {
    "DEEP_BOTTOM": "深底（60/120/360 全低）",
    "RECOVERING_FROM_BOTTOM": "长期底 · 已开始修复",
    "RECENT_BOTTOM": "近期回调（非长期低）",
}
_TYPE_LABEL = {
    "broad": "宽基", "industry": "行业", "theme": "主题", "dividend": "红利",
    "cross_border": "跨境", "commodity": "商品",
}


def _pct(v, nd=1, sign=True):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{v*100:+.{nd}f}%" if sign else f"{v*100:.{nd}f}%"


def _cell(v, nd=1):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    cls = "pos" if v > 0 else ("neg" if v < 0 else "")
    return f"<span class='{cls}'>{_pct(v, nd)}</span>"


def _core_table(summary: dict) -> str:
    rows = ""
    for state in ("DEEP_BOTTOM", "RECOVERING_FROM_BOTTOM", "RECENT_BOTTOM"):
        rec = summary.get(state, {})
        m = rec.get("_meta", {})
        n = m.get("n_events", 0)
        n_etf = m.get("n_etfs", 0)
        n_ca = m.get("n_corp_action_forward", 0)
        nol = m.get("n_non_overlap", {})
        cells = ""
        for h in ("20", "60", "120"):
            r = rec.get(h, {})
            ret = r.get("ret", {})
            retc = r.get("ret_clean", {})
            cells += (
                f"<td>{_cell(ret.get('mean'))}<br>"
                f"<small>中位 {_pct(ret.get('median'))} · 胜率 {_pct(ret.get('win_rate'), 0, False)} · n={ret.get('n')}</small></td>"
            )
            cells += f"<td>{_cell(retc.get('mean'))}<br><small>n={retc.get('n')}</small></td>"
            cells += f"<td>{_cell(r.get('excess', {}).get('mean'))}</td>"
            cells += f"<td>{_cell(r.get('mae', {}).get('mean'))}</td><td>{_cell(r.get('mfe', {}).get('mean'))}</td>"
        rows += (
            f"<tr><td><b>{_STATE_LABEL[state]}</b><br>"
            f"<small>n={n} · {n_etf} ETF · 折算{n_ca} · 非重叠 {nol}</small></td>{cells}</tr>"
        )
    return f"""<table>
<thead><tr><th>Entry state</th>
<th colspan='2'>20D 收益</th><th>超额</th><th>MAE</th><th>MFE</th>
<th colspan='2'>60D 收益</th><th>超额</th><th>MAE</th><th>MFE</th>
<th colspan='2'>120D 收益</th><th>超额</th><th>MAE</th><th>MFE</th></tr>
<tr><th></th><th>含折算</th><th>剔除折算</th><th></th><th></th><th></th>
<th>含折算</th><th>剔除折算</th><th></th><th></th><th></th>
<th>含折算</th><th>剔除折算</th><th></th><th></th><th></th></tr></thead>
<tbody>{rows}</tbody></table>"""


def _by_table(by: dict, state_order: tuple = ("DEEP_BOTTOM", "RECOVERING_FROM_BOTTOM", "RECENT_BOTTOM")) -> str:
    sections = ""
    for state in state_order:
        sub = by.get(state, {})
        if not sub:
            continue
        rows = ""
        for key, rec in sub.items():
            m = rec.get("_meta", {})
            r120 = rec.get("120", {}).get("ret", {})
            r60 = rec.get("60", {}).get("ret", {})
            rows += (
                f"<tr><td>{_TYPE_LABEL.get(key, key)}</td>"
                f"<td>{_cell(r60.get('mean'))}<br><small>胜率 {_pct(r60.get('win_rate'), 0, False)} · n={r60.get('n')}</small></td>"
                f"<td>{_cell(r120.get('mean'))}<br><small>胜率 {_pct(r120.get('win_rate'), 0, False)} · n={r120.get('n')}</small></td>"
                f"<td>{m.get('n_events', 0)}</td></tr>"
            )
        sections += f"<h3>{_STATE_LABEL[state]}</h3><table><thead><tr><th>类型</th><th colspan='2'>60D</th><th colspan='2'>120D</th><th>事件数</th></tr></thead><tbody>{rows}</tbody></table>"
    return sections or "<p>无分层数据</p>"


def _year_table(by_year: dict) -> str:
    sections = ""
    for state in ("DEEP_BOTTOM", "RECOVERING_FROM_BOTTOM", "RECENT_BOTTOM"):
        sub = by_year.get(state, {})
        if not sub:
            continue
        rows = ""
        for year in sorted(sub, key=int):
            rec = sub[year]
            r = rec.get("120", {}).get("ret", {})
            r20 = rec.get("20", {}).get("ret", {})
            rows += (
                f"<tr><td>{year}</td>"
                f"<td>{_cell(r20.get('mean'))}<br><small>胜率 {_pct(r20.get('win_rate'), 0, False)}</small></td>"
                f"<td>{_cell(r.get('mean'))}<br><small>胜率 {_pct(r.get('win_rate'), 0, False)} · n={r.get('n')}</small></td>"
                f"<td>{rec.get('_meta', {}).get('n_events', 0)}</td></tr>"
            )
        sections += f"<h3>{_STATE_LABEL[state]}</h3><table><thead><tr><th>年份</th><th colspan='2'>20D</th><th colspan='2'>120D</th><th>事件数</th></tr></thead><tbody>{rows}</tbody></table>"
    return sections or "<p>无年份数据</p>"


def render_state_odds(payload: dict, out_path: Path | None = None) -> Path:
    out_path = out_path or (STUDY_DIR / "state_odds_result.html")
    summary = payload["summary"]
    by_type = payload["by_type"]
    by_year = payload["by_year"]

    # 一句话结论：三种状态 120D 均值对比
    headline = _build_headline(summary)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Study 2 · Price Bottom State Odds</title>
<style>{_CSS}</style></head><body>
<div class="wrap">
<header>
  <h1>Study 2 · Price Bottom State Odds</h1>
  <div class="meta">{payload.get('universe', {}).get('n_full_etf', '—')} FULL ETF · 事件=进入底部状态的 off→on 转换 · 前向 20/60/120 交易日 · 横截面基准 · 全离线 · {payload.get('generated_at', '')}</div>
</header>

<div class="summary">
  <h3>一句话结论</h3>
  {headline}
</div>

<div class="kpis">
  <div class="kpi"><span>DEEP 事件</span><b>{summary.get('DEEP_BOTTOM', {}).get('_meta', {}).get('n_events', 0)}</b></div>
  <div class="kpi"><span>RECOVERING 事件</span><b>{summary.get('RECOVERING_FROM_BOTTOM', {}).get('_meta', {}).get('n_events', 0)}</b></div>
  <div class="kpi"><span>RECENT 事件</span><b>{summary.get('RECENT_BOTTOM', {}).get('_meta', {}).get('n_events', 0)}</b></div>
</div>

<h2>一、三状态 × 三 horizon 核心比较</h2>
<div class="card">{_core_table(summary)}</div>
<p class="meta">「含折算」= 全部事件；「剔除折算」= 前向窗口内无折算污染的事件。超额=相对同市场横截面中位前向收益；MAE/MFE 为窗口内最低/最高 close 相对事件日收益。</p>

<h2>二、按 ETF 类型分层</h2>
<div class="card">{_by_table(by_type)}</div>

<h2>三、按年份分层（跨年份稳健性）</h2>
<div class="card">{_year_table(by_year)}</div>

<h2>三·b、时间代表性警示</h2>
<div class="card">
<p>⚠ <b>三种底部状态的 120D 正收益在 2023 均告负</b>（DEEP -9.4%、RECOVERING -11.8%、RECENT -6.4%），
而 2024/2025 全部显著为正——这是<b>市场普遍上涨年份效应</b>，非底部状态特有信号。
DEEP_BOTTOM 的 +4.0% 主要由 2024(+17.8%)/2025(+23.9%) 撑起；2021(-6.9%)/2023(-9.4%) 为负。
结论：底部状态的中期赔率<b>依赖市场环境</b>，不能外推为「低位即买入」的稳定 alpha。</p>
</div>

<h2>四、口径与限定</h2>
<div class="card">
<ul>
<li>Universe：{payload.get('universe', {}).get('n_full_etf', '—')} 只 FULL ETF（历史≥756 交易日）</li>
<li>事件语义：off→on 转换（前一日非该状态），连续在状态内不重复计数；货币/债券不产生事件</li>
<li>状态：复用 price_map 语义——price_pos_N 位置 ≤20% 为 LOW；折算窗口级污染；UNRELIABLE/数据不足不产生事件</li>
<li>基准：同市场横截面中位前向收益（全离线、确定性）</li>
<li>前向收益 close→close，窗口严格 ≤ 事件日，无 look-ahead；折算只标记不修正（与前序口径一致）</li>
</ul>
</div>

<footer>AKsignal · Lane 2 Research · Study 2 Price Bottom State Odds · {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</footer>
</div></body></html>"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path


def _build_headline(summary: dict) -> str:
    parts = []
    for state, label in (("DEEP_BOTTOM", "深底"), ("RECOVERING_FROM_BOTTOM", "长期底·已修复"), ("RECENT_BOTTOM", "近期回调")):
        rec = summary.get(state, {})
        r = rec.get("120", {}).get("ret", {})
        rc = rec.get("120", {}).get("ret_clean", {})
        if r.get("mean") is not None:
            parts.append(f"{label} 120D 均值 {_pct(r.get('mean'))}（剔除折算 {_pct(rc.get('mean'))}）、胜率 {_pct(r.get('win_rate'), 0, False)}、n={r.get('n')}")
    if not parts:
        return "<p>样本不足。</p>"
    return "<p>" + "；".join(parts) + "。</p>"
