"""Study 3C · HTML 报告（zihao raccoon 视觉体系）。

纯 renderer：只消费 study3c_summary.json + study3c_market_history.parquet +
study3c_current_state.parquet，不重算状态/不训练。

首页（§37-§40）：
  - KPI：BOTTOM / FIRST_EXIT / ACTIVE / ESTABLISHED / RETEST / POST + ACTIVE TRANSITION BREADTH
  - 当前市场一句话结论（§38，自动生成）
  - State Table（§39：ETF | 类型 | Lane3 状态 | 状态年龄 | Lane2 | Lane1 | pos120 | 变化）
  - Validation C1-C5 + verdict

产物：outputs/research/trend_transition/study3c_report.html
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from . import STUDY_DIR
from .state_metrics import STATE_LABEL_CN

_CSS = """
:root {
  --zh-blue:#174A7C; --zh-deep-blue:#06213D; --zh-cyan:#7ECDEB;
  --zh-light-blue:#DDEFF8; --zh-cream:#FFF9EF; --zh-raccoon-gold:#D79A36;
  --zh-brown:#7A4A24; --zh-text:#1F2D3D; --zh-muted:#6B7C8F; --zh-card:#FFFFFF;
}
*{box-sizing:border-box}
body{font-family:-apple-system,'PingFang SC','Hiragino Sans GB',sans-serif;margin:0;background:var(--zh-cream);color:var(--zh-text);line-height:1.6}
.wrap{max-width:1120px;margin:0 auto;padding:24px}
header{border-bottom:3px solid var(--zh-blue);padding:18px 0;margin-bottom:20px}
h1{color:var(--zh-deep-blue);margin:0;font-size:1.6em}
h2{color:var(--zh-blue);margin-top:30px;border-left:4px solid var(--zh-raccoon-gold);padding-left:10px}
h3{color:var(--zh-blue)}
.meta{color:var(--zh-muted);font-size:.85em;margin-top:6px}
.card{background:var(--zh-card);border-radius:10px;padding:16px 20px;margin:14px 0;box-shadow:0 1px 3px rgba(6,33,61,.08)}
table{border-collapse:collapse;width:100%;font-size:.88em;margin:10px 0}
th{background:var(--zh-light-blue);color:var(--zh-deep-blue);text-align:left;padding:7px 9px;border:1px solid #cfe0ec}
td{padding:6px 9px;border:1px solid #e3edf5}
tr:nth-child(even) td{background:#f7fbfd}
.pos{color:#b32424;font-weight:600}
.neg{color:#1a7a5a}
.hl{background:#fff6e6!important;font-weight:600}
.kpi{display:inline-block;background:var(--zh-card);border:1px solid #d5e6f2;border-radius:8px;padding:10px 16px;margin:6px 8px 6px 0;min-width:150px}
.kpi b{display:block;font-size:1.35em;color:var(--zh-blue)}
.kpi span{color:var(--zh-muted);font-size:.78em}
.kpi.gold b{color:var(--zh-raccoon-gold)}
footer{margin-top:40px;padding-top:14px;border-top:1px solid #d5e6f2;color:var(--zh-muted);font-size:.8em}
.summary{background:var(--zh-deep-blue);color:#fff;border-radius:10px;padding:18px 22px;margin:16px 0}
.summary h3{color:var(--zh-cyan);margin-top:0}
.summary .s{color:#cfe6f5;font-size:.95em;margin-top:8px}
.verdict{font-size:1.1em;font-weight:700;margin-top:10px;padding:10px 14px;border-radius:8px;display:inline-block}
.verdict.pass{background:#1a7a5a;color:#fff}
.verdict.fail{background:#b32424;color:#fff}
.pill{display:inline-block;padding:2px 10px;border-radius:20px;font-size:.78em;margin-right:6px}
.pill.pass{background:#e2f4e9;color:#1a7a5a;border:1px solid #bfe6cd}
.pill.fail{background:#fde4e4;color:#b32424;border:1px solid #f2c3c3}
details{margin:8px 0;background:#fafcfe;border:1px solid #d5e6f2;border-radius:8px;padding:8px 12px}
summary{cursor:pointer;font-weight:600;color:var(--zh-blue)}
"""

_ETYPE_LABEL = {"broad": "宽基", "industry": "行业", "theme": "主题",
                "dividend": "红利", "cross_border": "跨境", "bond": "债券",
                "commodity": "商品", "money": "货币"}

# §40 中文标签
_STATE_CN = {
    "UNRELIABLE": "数据不足",
    "BOTTOM": "长期底部",
    "FIRST_EXIT": "刚离开底部",
    "TRANSITION_EARLY": "趋势切换·早期",
    "TRANSITION_ACTIVE": "趋势切换·进行中",
    "TRANSITION_ESTABLISHED": "趋势切换·已建立",
    "RETEST": "重新回到底部",
    "POST_TRANSITION": "已完成底部切换",
}

_KPI_ORDER = ["BOTTOM", "FIRST_EXIT", "TRANSITION_EARLY", "TRANSITION_ACTIVE",
              "TRANSITION_ESTABLISHED", "RETEST", "POST_TRANSITION"]


def _fmt(v: Any, nd: int = 3) -> str:
    if v is None or (isinstance(v, float) and not (v == v)):
        return "—"
    try:
        f = float(v)
        if not (f == f):
            return "—"
        return f"{f:.{nd}f}"
    except (TypeError, ValueError):
        return str(v)


def _pct(v: Any) -> str:
    if v is None or (isinstance(v, float) and not (v == v)):
        return "—"
    return f"{float(v) * 100:.1f}%"


def _load_summary() -> dict[str, Any]:
    with open(STUDY_DIR / "study3c_summary.json", encoding="utf-8") as f:
        return json.load(f)


def _load_market() -> pd.DataFrame:
    return pd.read_parquet(STUDY_DIR / "study3c_market_history.parquet")


def _load_current() -> pd.DataFrame:
    return pd.read_parquet(STUDY_DIR / "study3c_current_state.parquet")


def _one_liner(s: dict[str, Any]) -> str:
    """§38 当前市场一句话结论（从 KPI + breadth change 自动生成）。"""
    m = s.get("market", {})
    rel = m.get("reliable_count")
    dist = s.get("state_distribution_current", {})
    n_fe = dist.get("FIRST_EXIT", 0)
    n_early = dist.get("TRANSITION_EARLY", 0)
    n_active = dist.get("TRANSITION_ACTIVE", 0)
    n_est = dist.get("TRANSITION_ESTABLISHED", 0)
    n_post = dist.get("POST_TRANSITION", 0)
    active_br = m.get("active_transition_breadth")
    chg20 = m.get("active_transition_breadth_change_20d")
    net = m.get("net_transition_flow")
    retest = dist.get("RETEST", 0)

    if active_br is None:
        return "数据不足，无法生成当前市场结论。"
    if chg20 is not None and chg20 >= 0:
        direction = f"Active Transition Breadth 过去 20D 上升 {chg20 * 100:+.1f}pp，市场正在从 Bottom 状态向 Trend 状态扩张。"
    else:
        direction = f"Active Transition Breadth 过去 20D 下降 {chg20 * 100 if chg20 is not None else 0:+.1f}pp，同时 Retest 数={retest}，Trend Transition 正在收缩。"
    return (f"当前 {int(rel) if rel else 0} 只 reliable ETF 中，{n_fe} 只刚离开长期底部，"
            f"{n_early + n_active} 只处于 Early/Active Transition，{n_est} 只处于已建立 Transition，"
            f"{n_post} 只已完成底部切换。{direction}（净 flow={_fmt(net)}）")


def _kpi_row(s: dict[str, Any]) -> str:
    dist = s.get("state_distribution_current", {})
    m = s.get("market", {})
    kpis = "".join(
        f'<div class="kpi"><b>{dist.get(st, 0)}</b><span>{_STATE_CN[st]}</span></div>'
        for st in _KPI_ORDER
    )
    chg = m.get("active_transition_breadth_change_20d")
    chg_txt = f"{chg * 100:+.1f}pp" if chg is not None else "—"
    gold = (f'<div class="kpi gold"><b>{_pct(m.get("active_transition_breadth"))}</b>'
            f'<span>ACTIVE TRANSITION BREADTH</span></div>'
            f'<div class="kpi"><b>{chg_txt}</b><span>20D change</span></div>'
            f'<div class="kpi"><b>{_pct(m.get("transition_breadth"))}</b><span>TRANSITION BREADTH</span></div>')
    return kpis + gold


def _validation_table(s: dict[str, Any]) -> str:
    checks = s.get("validation", {}).get("checks", {})
    rows = ""
    for k, v in checks.items():
        rows += (f"<tr><td>{k}</td><td>{v['name']}</td><td>{_pill(v['ok'])}</td>"
                 f"<td style='font-size:.82em'>{str(v.get('detail', ''))[:160]}</td></tr>")
    return f'<table><tr><th>ID</th><th>检查项</th><th>状态</th><th>依据</th></tr>{rows}</table>'


def _pill(ok: bool) -> str:
    return f'<span class="pill {"pass" if ok else "fail"}">{"PASS" if ok else "FAIL"}</span>'


def _state_table() -> str:
    cur = _load_current()
    if len(cur) == 0:
        return "<p>无当前状态数据。</p>"
    # 排序：POST/ESTABLISHED 后放，展示 active transition 优先
    order = {"FIRST_EXIT": 0, "TRANSITION_EARLY": 1, "TRANSITION_ACTIVE": 2,
             "TRANSITION_ESTABLISHED": 3, "RETEST": 4, "POST_TRANSITION": 5,
             "BOTTOM": 6, "UNRELIABLE": 7}
    cur = cur.copy()
    cur["_ord"] = cur["transition_state"].apply(lambda st: order.get(str(st), 9))
    cur = cur.sort_values(["_ord", "fund_code"]).head(120)
    rows = ""
    for _, r in cur.iterrows():
        state = str(r["transition_state"])
        age = r.get("days_since_first_exit")
        age_txt = (f"{int(age)}D" if isinstance(age, (int, float)) and age == age else "—")
        bucket = str(r.get("transition_age_bucket", "NA"))
        l1 = r.get("lane1_leadership_state")
        l1_txt = str(l1) if l1 else "—"
        rows += (f"<tr><td>{r['fund_code']}<br><span style='color:var(--zh-muted);font-size:.8em'>{r['fund_name']}</span></td>"
                 f"<td>{_ETYPE_LABEL.get(str(r['etf_type']), r['etf_type'])}</td>"
                 f"<td><b>{_STATE_CN.get(state, state)}</b></td>"
                 f"<td>{age_txt} <span style='color:var(--zh-muted);font-size:.78em'>({bucket})</span></td>"
                 f"<td>{r.get('lane2_target_stage', '—')}</td><td>{l1_txt}</td>"
                 f"<td>{_fmt(r.get('pos120'), 0)}</td>"
                 f"<td style='font-size:.8em'>{r.get('state_origin', '')}</td></tr>")
    return (f'<table><tr><th>ETF</th><th>类型</th><th>Lane 3 状态</th><th>状态年龄</th>'
            f'<th>Lane 2</th><th>Lane 1</th><th>pos120</th><th>origin</th></tr>{rows}</table>')


def _market_chart() -> str:
    """SVG 折线：active_transition_breadth + bottom_breadth 历史。"""
    m = _load_market()
    if len(m) < 2:
        return "<p>市场历史不足。</p>"
    m = m.copy()
    m["trade_date"] = pd.to_datetime(m["trade_date"])
    n = len(m)
    w, h, pad = 1000, 220, 40
    x = lambda i: pad + (w - 2 * pad) * i / max(n - 1, 1)
    series = [("active_transition_breadth", "var(--zh-blue)", "Active Transition"),
              ("transition_breadth", "var(--zh-raccoon-gold)", "Transition"),
              ("bottom_breadth", "var(--zh-brown)", "Bottom")]
    def poly(col):
        pts = []
        for i, v in enumerate(m[col]):
            if pd.isna(v):
                continue
            pts.append(f"{x(i):.1f},{h - pad - (h - 2 * pad) * float(v):.1f}")
        return " ".join(pts)
    paths = "".join(
        f'<polyline points="{poly(col)}" fill="none" stroke="{color}" stroke-width="1.6" opacity="0.9"/>'
        f'<text x="{w - pad}" y="{20 + k * 16}" font-size="11" fill="{color}">{label}</text>'
        for k, (col, color, label) in enumerate(series)
    )
    yticks = "".join(
        f'<text x="{pad - 6}" y="{h - pad - (h - 2 * pad) * f:.1f}" text-anchor="end" font-size="10" fill="#6B7C8F">{f:.0%}</text>'
        f'<line x1="{pad}" y1="{h - pad - (h - 2 * pad) * f:.1f}" x2="{w - pad}" y2="{h - pad - (h - 2 * pad) * f:.1f}" stroke="#e3edf5" stroke-dasharray="2 3"/>'
        for f in (0.0, 0.2, 0.4, 0.6, 0.8)
    )
    first, last = m["trade_date"].iloc[0].date(), m["trade_date"].iloc[-1].date()
    return (f'<svg viewBox="0 0 {w} {h}" style="width:100%;height:auto">'
            f'{yticks}{paths}'
            f'<text x="{pad}" y="{h - 8}" font-size="10" fill="#6B7C8F">{first}</text>'
            f'<text x="{w - pad}" y="{h - 8}" text-anchor="end" font-size="10" fill="#6B7C8F">{last}</text>'
            f'</svg>')


def render(summary: dict[str, Any] | None = None, out_path: Path | None = None) -> Path:
    s = summary if summary is not None else _load_summary()
    generated = s.get("generated_at", "unknown")
    gate = s.get("pass_gate", {})
    verdict = gate.get("verdict", "")
    v_cls = "pass" if str(verdict).startswith("PASS") else "fail"

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><style>{_CSS}</style>
<title>Study 3C · Trend Transition 状态分类</title></head><body><div class="wrap">
<header><h1>Lane 3 · Study 3C — Trend Transition 生命周期状态分类</h1>
<div class="meta">生成于 {generated} · persistence={s.get('persistence')} · as_of={s.get('as_of')} · n_funds={s.get('n_funds')} · 状态历史 {s.get('n_hist_rows')} 行（{s.get('calendar_start')} → {s.get('calendar_end')}）</div></header>

<div class="summary">
<h3>当前市场状态</h3>
{_one_liner(s)}
<div class="s">verdict: <span class="verdict {v_cls}">{verdict}</span>（{gate.get('n_pass')}/5）· 纯状态分类（STATE ≠ SIGNAL / PREDICTION / BUY）</div>
</div>

<h2>1. 市场 KPI（§37）</h2>
<div class="card">{_kpi_row(s)}</div>

<h2>2. 历史 Market Transition Map</h2>
<div class="card">{_market_chart()}</div>

<h2>3. 当前状态表（§39，Top 120）</h2>
<div class="card">{_state_table()}</div>

<h2>4. Validation C1-C5（§27-§28）</h2>
<div class="card">{_validation_table(s)}</div>

<h2>5. ETF Type 分层（§23）</h2>
<div class="card">{_etf_type_table(s)}</div>

<h2>6. Lane 联动（只读，§24-§25）</h2>
<div class="card">{_lane_overlap(s)}</div>

<details><summary>口径与限定</summary><p>
Study 3C 是确定性 as-of 状态分类：只用 <= as_of 的数据，状态定义不依赖 RPS/drawdown/breadth/etf_type
（§16，那些只进 state_context）。left-censored 不伪造 first_exit（§15）。Lane 1 联动为辅助验证
（覆盖率不影响 PASS/FAIL）。不预测收益率、不生成买卖信号。
</p></details>

<footer>Lane 3 · Study 3C · TREND_TRANSITION_STATE_V1（FROZEN_STATE_CLASSIFIER）· renderer 纯消费结果文件</footer>
</div></body></html>"""
    path = out_path or (STUDY_DIR / "study3c_report.html")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path


def _etf_type_table(s: dict[str, Any]) -> str:
    brk = s.get("etf_type_breakdown", [])
    if not brk:
        return "<p>无分层数据。</p>"
    rows = ""
    for r in brk:
        etype = str(r["etf_type"])
        rows += (f"<tr><td>{_ETYPE_LABEL.get(etype, etype)}</td><td>{r['n_reliable']}</td>"
                 f"<td>{_pct(r['bottom_breadth'])}</td><td>{_pct(r['transition_breadth'])}</td>"
                 f"<td>{_pct(r['active_transition_breadth'])}</td></tr>")
    return (f'<table><tr><th>ETF 类型</th><th>reliable</th><th>bottom</th>'
            f'<th>transition</th><th>active transition</th></tr>{rows}</table>')


def _lane_overlap(s: dict[str, Any]) -> str:
    l2 = s.get("lane2_overlap", [])
    l1 = s.get("lane1_overlap", {})
    l2_html = ""
    if l2:
        rows = "".join(
            f"<tr><td>{_STATE_CN.get(str(r['transition_state']), r['transition_state'])}</td>"
            f"<td>{r['lane2_target_stage']}</td><td>{r['n']}</td></tr>" for r in l2
        )
        l2_html = f'<table><tr><th>Lane3 状态</th><th>Lane2 target_stage</th><th>n</th></tr>{rows}</table>'
    l1_html = (f'<p>Lane 1 联动（辅助验证，覆盖率={_fmt(l1.get("coverage"))}）：{l1.get("detail", "—")}</p>')
    return f'<h3>Lane 2 联动（§25）</h3>{l2_html or "<p>当日无 BOTTOM/RETEST × TARGET/NEAR_MISS。</p>"}<h3>Lane 1 联动（§24）</h3>{l1_html}'
