"""Price Bottom Map HTML 报告（zihao raccoon 视觉体系）。

结构：
  一句话结论 + KPI（长期底部树形：仍在底部 / 已开始修复）
  bottom_state × etf_type 交叉表
  底部明细表（DEEP / RECOVERING / RECENT，按 price_pos_360 排序）
  折算污染审计清单
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
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
.flag-bad{color:#b32424}.flag-ok{color:#1a7a5a}
.tag{display:inline-block;padding:1px 7px;border-radius:8px;font-size:.75em;margin-right:4px}
.tag-deep{background:#7A4A24;color:#fff}.tag-recov{background:#D79A36;color:#06213D}
.tag-recent{background:#7ECDEB;color:#06213D}.tag-unrel{background:#ccc;color:#333}.tag-normal{background:#eef;color:#666}
.tree{font-family:ui-monospace,Menlo,monospace;background:#f7fbfd;padding:12px 16px;border-radius:8px}
"""

_STATE_LABEL = {
    "DEEP_BOTTOM": "深底（60/120/360 全低）",
    "RECOVERING_FROM_BOTTOM": "长期底 · 开始修复",
    "RECENT_BOTTOM": "近期回调（非长期低）",
    "NORMAL": "正常",
    "UNRELIABLE": "数据不可靠",
}
_STATE_TAG = {
    "DEEP_BOTTOM": "tag-deep", "RECOVERING_FROM_BOTTOM": "tag-recov",
    "RECENT_BOTTOM": "tag-recent", "UNRELIABLE": "tag-unrel", "NORMAL": "tag-normal",
}


def _pct(v, nd=0):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{v:.{nd}f}%"


def _dist_pct(v, nd=1):
    """距窗口低点涨幅（小数 0.0359 → +3.6%）。"""
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{v*100:+.{nd}f}%"


def _num(v):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{v:,.3f}"


def _cell(v, nd=1):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{v:.{nd}f}"


def _cross_table(df: pd.DataFrame) -> str:
    formal = df[df["bottom_state"].isin(["DEEP_BOTTOM", "RECOVERING_FROM_BOTTOM", "RECENT_BOTTOM"])]
    ct = formal.pivot_table(index="bottom_state", columns="etf_type", values="fund_code", aggfunc="count", fill_value=0)
    order = ["DEEP_BOTTOM", "RECOVERING_FROM_BOTTOM", "RECENT_BOTTOM"]
    ct = ct.reindex([o for o in order if o in ct.index])
    if ct.empty:
        return "<p>无底部状态 ETF</p>"
    rows = ""
    for state, row in ct.iterrows():
        cells = "".join(f"<td>{int(v)}</td>" for v in row)
        rows += f"<tr><td><b>{_STATE_LABEL.get(state, state)}</b></td>{cells}</tr>"
    headers = "".join(f"<th>{c}</th>" for c in ct.columns)
    totals = "".join(f"<td><b>{int(v)}</b></td>" for v in ct.sum())
    return f"<table><thead><tr><th>状态</th>{headers}</tr></thead><tbody>{rows}<tr><td><b>合计</b></td>{totals}</tr></tbody></table>"


def _detail_table(df: pd.DataFrame, states: tuple[str, ...], as_of: str) -> str:
    sub = df[df["bottom_state"].isin(states)].sort_values("price_pos_360", na_position="last")
    if sub.empty:
        return "<p>无该状态 ETF</p>"
    rows = ""
    for r in sub.itertuples():
        tag = f"<span class='tag {_STATE_TAG.get(r.bottom_state, '')}'>{r.bottom_state[:4]}</span>"
        rows += (
            f"<tr><td>{r.fund_code}</td><td>{r.fund_name}</td><td>{r.etf_type}</td>"
            f"<td>{_num(getattr(r, 'close_as_of'))}</td>"
            f"<td>{_cell(getattr(r, 'price_pos_60'))}</td><td>{_cell(getattr(r, 'price_pos_120'))}</td><td>{_cell(getattr(r, 'price_pos_360'))}</td>"
            f"<td>{_dist_pct(getattr(r, 'distance_to_low_60'))}</td><td>{_dist_pct(getattr(r, 'distance_to_low_120'))}</td><td>{tag}</td></tr>"
        )
    return f"""<table>
<thead><tr><th>代码</th><th>名称</th><th>类型</th><th>现价</th>
<th>60D位置</th><th>120D位置</th><th>360D位置</th>
<th>距60D低</th><th>距120D低</th><th>状态</th></tr></thead>
<tbody>{rows}</tbody></table>"""


def _audit_table(df: pd.DataFrame) -> str:
    unrel = df[df["bottom_state"] == "UNRELIABLE"]
    if unrel.empty:
        return "<p>无折算污染样本</p>"
    rows = ""
    for r in unrel.itertuples():
        flags = f"60:{int(getattr(r, 'unreliable_60'))} 120:{int(getattr(r, 'unreliable_120'))} 360:{int(getattr(r, 'unreliable_360'))}"
        rows += f"<tr><td>{r.fund_code}</td><td>{r.fund_name}</td><td>{flags}</td><td>{r.history_days}</td></tr>"
    return f"""<table>
<thead><tr><th>代码</th><th>名称</th><th>折算污染窗口</th><th>历史天数</th></tr></thead>
<tbody>{rows}</tbody></table>"""


def render_price_map(df: pd.DataFrame, as_of: str, out_path: Path | None = None) -> Path:
    out_path = out_path or (STUDY_DIR / f"price_map_{as_of.replace('-', '')}.html")
    counts = df["bottom_state"].value_counts().to_dict()
    long_term = df[df["long_term_bottom"] == True]
    n_lt = int(len(long_term))
    n_deep = int((long_term["bottom_state"] == "DEEP_BOTTOM").sum())
    n_recov = int((long_term["bottom_state"] == "RECOVERING_FROM_BOTTOM").sum())
    n_full = int(df["full_360_sample"].sum())
    n_unrel = int(counts.get("UNRELIABLE", 0))

    headline = f"截至 {as_of}，{n_lt} 只 ETF 处于长期价格底部（120D 且 360D 位置≤20%），其中 {n_deep} 只仍在底部、{n_recov} 只已开始修复。"

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Price Bottom Map · {as_of}</title>
<style>{_CSS}</style></head><body>
<div class="wrap">
<header>
  <h1>Price Bottom Map · ETF 价格底部地图</h1>
  <div class="meta">横截面市场状态快照 · 锚点 as_of={as_of} · 窗口严格截断 ≤ as_of · 价格位置 0=窗口最低 · 疑似折算标 UNRELIABLE · {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</div>
</header>

<div class="summary">
  <h3>一句话结论</h3>
  <p>{headline}</p>
</div>

<div class="kpis">
  <div class="kpi"><span>全市场（≥60D）</span><b>{len(df)}</b></div>
  <div class="kpi"><span>360D 完整样本</span><b>{n_full}</b></div>
  <div class="kpi"><span>长期底部</span><b>{n_lt}</b></div>
  <div class="kpi"><span>数据不可靠</span><b>{n_unrel}</b></div>
</div>

<div class="card">
<h3>长期底部构成</h3>
<div class="tree">长期底部 {n_lt}
├─ 仍在底部（DEEP_BOTTOM） {n_deep}
└─ 已开始修复（RECOVERING） {n_recov}</div>
<p class="meta">long_term_bottom = price_pos_120 ≤ 20% 且 price_pos_360 ≤ 20%（独立 bool，非状态）。</p>
</div>

<h2>一、底部状态 × ETF 类型</h2>
<div class="card">{_cross_table(df)}</div>

<h2>二、仍在底部（DEEP_BOTTOM）</h2>
<div class="card">{_detail_table(df, ("DEEP_BOTTOM",), as_of)}</div>

<h2>三、已开始修复（RECOVERING_FROM_BOTTOM）</h2>
<div class="card">{_detail_table(df, ("RECOVERING_FROM_BOTTOM",), as_of)}</div>

<h2>四、近期回调（RECENT_BOTTOM，非长期便宜）</h2>
<div class="card">{_detail_table(df, ("RECENT_BOTTOM",), as_of)}</div>

<h2>五、折算污染审计（UNRELIABLE）</h2>
<div class="card">{_audit_table(df)}
<p class="meta">possible_corporate_action = 单日 |ret| ≥ 20%（疑似份额折算/公司行为，非事实断言）。窗口内折算 → 该窗口位置置空；360D 污染或历史不足 → 整体 UNRELIABLE。</p>
</div>

<footer>AKsignal · Lane 2 Research · Price Bottom Map · {as_of} · 状态互斥分类（DEEP / RECOVERING / RECENT / NORMAL / UNRELIABLE）</footer>
</div></body></html>"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path
