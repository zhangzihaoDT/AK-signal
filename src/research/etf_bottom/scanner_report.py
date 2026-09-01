"""Repair-Retest V1 全市场每日扫描 HTML 报告（zihao raccoon 视觉体系）。

定位：**纯 renderer**。只消费 scan_{date}.json 已有的 layer_a / layer_b / layer_c
字段，绝不重新计算 cohort、TARGET、transition 或 odds（这些是 scanner.py 的应用事实）。

三层结构（各回答一个不同的问题）：
  Layer A — Market Bottom Map：市场底部有多宽、集中在哪里、今天怎么迁移
  Layer B — Repair-Retest Scanner：哪些 ETF 进入 TARGET / NEAR_MISS，
             以及 DEEP↔RECOVERING 迁移、domain 进出
  Layer C — Historical Odds：in-domain ∪ near-miss ∪ watch 池的结构在冻结的
             历史研究中有没有赔率支持
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
h3{color:var(--zh-deep-blue);margin:14px 0 6px}
.meta{color:var(--zh-muted);font-size:.85em;margin-top:6px}
.card{background:var(--zh-card);border-radius:10px;padding:16px 20px;margin:14px 0;box-shadow:0 1px 3px rgba(6,33,61,.08)}
table{border-collapse:collapse;width:100%;font-size:.88em;margin:10px 0}
th{background:var(--zh-light-blue);color:var(--zh-deep-blue);text-align:left;padding:7px 9px;border:1px solid #cfe0ec}
td{padding:6px 9px;border:1px solid #e3edf5}
tr:nth-child(even) td{background:#f7fbfd}
.kpi{display:inline-block;background:var(--zh-card);border:1px solid #d5e6f2;border-radius:8px;padding:10px 16px;margin:6px 8px 6px 0;min-width:150px}
.kpi b{display:block;font-size:1.35em;color:var(--zh-blue)}
.kpi span{color:var(--zh-muted);font-size:.8em}
.kpi-accent{display:inline-block;background:var(--zh-card);border:3px solid var(--zh-raccoon-gold);border-radius:8px;padding:10px 16px;margin:6px 8px 6px 0;min-width:150px}
.kpi-accent b{display:block;font-size:1.35em;color:var(--zh-deep-blue)}
.kpi-accent span{color:var(--zh-brown);font-size:.8em;font-weight:600}
footer{margin-top:40px;padding-top:14px;border-top:1px solid #d5e6f2;color:var(--zh-muted);font-size:.8em}
.summary{background:var(--zh-deep-blue);color:#fff;border-radius:10px;padding:18px 22px;margin:16px 0}
.summary h3{color:var(--zh-cyan);margin-top:0}
.tag{display:inline-block;padding:1px 8px;border-radius:8px;font-size:.75em;margin-right:4px}
.tag-strong{background:#d8f0e0;color:#1a7a5a}.tag-watch{background:#dff1f8;color:#174A7C}
.tag-pos{background:#fff3d6;color:#7a5a00}.tag-caut{background:#f7dddd;color:#b32424}
.tag-unrel{background:#eee;color:#666}
.cohort-base{background:#e8f0f7;color:#174A7C}
.cohort-ext{background:#f7eede;color:#7a5a00}
details{background:#f7fbfd;border:1px solid #e3edf5;border-radius:6px;padding:6px 12px;margin-top:4px}
details summary{cursor:pointer;color:var(--zh-blue);font-size:.82em}
"""

_TARGET_LABEL = {
    "TARGET": "★ 结构触发候选",
    "NEAR_MISS": "▲ NEAR_MISS",
    "NON_TARGET": "域内·非target",
    "IN_DOMAIN_NON_TARGET": "域内·非target",
    "OUT_OF_DOMAIN": "域外",
    "UNRELIABLE": "数据不可靠",
}
_NEAR_MISS_REASON_LABEL = {
    "P120_ONE_BUCKET_AWAY": "p120 差一档到 Q3（修复中）",
    "P60_ONE_BUCKET_AWAY": "p60 差一档到 Q1（接近到位）",
}
_TRANSITION_LABEL = {
    "DEEP_TO_RECOVERING": "DEEP→RECOVERING", "RECOVERING_TO_DEEP": "RECOVERING→DEEP",
    "ENTER_DOMAIN": "新进入域", "EXIT_DOMAIN": "退出域", "STAY_IN_DOMAIN": "域内持平",
    "PREV_MISSING": "prev 日无数据", "OUTSIDE_DOMAIN": "域外", "N/A": "—",
}
_ODDS_LABEL = {
    "strong_observe": "★ 最强研究观察", "watch_structure": "🟢 重点等待结构",
    "position_only": "🟡 位置有意义", "cautious": "🔴 谨慎",
    "out_of_domain_good": "⚪ 历史不错·当前非机会", "out_of_domain_unknown": "⚪ 历史不足·当前不适用",
    "out_of_domain_bad": "🔴 历史负·无吸引力", "unreliable": "⚠️ 先解决数据",
}
_EVIDENCE_LABEL = {
    "CROSS_YEAR_SUPPORTED": "跨年正向证据", "YEAR_DEPENDENT": "依赖年份",
    "NEGATIVE_HISTORY": "历史负收益", "INSUFFICIENT_HISTORY": "历史不足",
}


def _pct(v, nd=1, sign=True):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{v*100:+.{nd}f}%" if sign else f"{v*100:.{nd}f}%"


def _cohort_tag(cohort: str) -> str:
    cls = "cohort-base" if cohort == "BASE" else ("cohort-ext" if cohort == "EXTENSION" else "tag-unrel")
    return f"<span class='tag {cls}'>{cohort}</span>"


def _layer_a_section(a: dict) -> str:
    def _kpi(lab: str, val, accent: bool = False) -> str:
        cls = "kpi-accent" if accent else "kpi"
        return f"<div class='{cls}'><span>{lab}</span><b>{val}</b></div>"

    kpis = "".join(
        _kpi(lab, a[k], accent=(k == "target_total"))
        for lab, k in [("结构触发候选", "target_total"),
                       ("NEAR_MISS", "near_miss_total"),
                       ("长期底部域", "long_term_bottom_total"),
                       ("reliable", "reliable_total")])

    state_rows = "".join(
        f"<tr><td>{st}</td><td>{n}</td></tr>" for st, n in sorted(a["state_counts"].items()))
    trans_rows = "".join(
        f"<tr><td>{_TRANSITION_LABEL.get(t, t)}</td><td>{n}</td></tr>"
        for t, n in sorted(a["transition_counts"].items(), key=lambda kv: -kv[1]))
    cluster_rows = "".join(
        f"<tr><td>{cl}</td><td>{n}</td></tr>" for cl, n in a["cluster_concentration"].items())
    near_reason_rows = "".join(
        f"<tr><td>{_NEAR_MISS_REASON_LABEL.get(k, k)}</td><td>{n}</td></tr>"
        for k, n in sorted(a.get("near_miss_by_reason", {}).items(), key=lambda kv: -kv[1]))
    cohort = a["cohort"]
    flat_note = (f" · 排除近零波动（货币/债券）= {a['flat_price_total']} 只（audit only）"
                 if a.get("flat_price_total") else "")

    return f"""
<h2>Layer A · Market Bottom Map</h2>
<div class="card">
  <div class="kpis">
    {kpis}
  </div>
  <p class="meta">reliable 全市场 = {a['reliable_total']}（BASE {cohort.get('BASE', 0)} / EXTENSION {cohort.get('EXTENSION', 0)}）· unreliable（数据门未过，audit only）= {a['unreliable_total']}{flat_note} · 当日迁移 = 相对统一 market prev-trade-date（{a.get('prev_trade_date', '—')}）</p>
</div>

<h3>底部状态分布</h3>
<div class="card"><table><thead><tr><th>状态</th><th>数量</th></tr></thead><tbody>{state_rows}</tbody></table></div>

<h3>今日迁移</h3>
<div class="card"><table><thead><tr><th>迁移</th><th>数量</th></tr></thead><tbody>{trans_rows}</tbody></table></div>

<h3>长期底部集中度（industry cluster）</h3>
<div class="card"><table><thead><tr><th>产业簇 / 类型</th><th>数量</th></tr></thead><tbody>{cluster_rows}</tbody></table></div>
""" + (f"""
<h3>NEAR_MISS 结构</h3>
<div class="card"><table><thead><tr><th>接近方式</th><th>数量</th></tr></thead><tbody>{near_reason_rows}</tbody></table></div>
""" if near_reason_rows else "")


def _layer_b_section(rows: list[dict]) -> str:
    if not rows:
        return '<h2>Layer B · Repair-Retest Scanner</h2>\n<div class="card"><p class="meta">当前无 ETF 处于长期底部域。</p></div>'
    body = ""
    for r in rows:
        stage = str(_TARGET_LABEL.get(r["target_stage"], r["target_stage"]))
        if r.get("near_miss_reason"):
            stage += f"<br><span class='tag tag-watch'>{_NEAR_MISS_REASON_LABEL.get(r['near_miss_reason'], r['near_miss_reason'])}</span>"
        body += (
            f"<tr><td><b>{r['fund_code']}</b> · {r.get('fund_name', '')} {_cohort_tag(r['cohort'])}</td>"
            f"<td>{stage}</td>"
            f"<td>{r.get('industry_cluster', '—')}</td>"
            f"<td>{r.get('bottom_state', '')}</td>"
            f"<td>{_num(r.get('p60'))}</td><td>{_num(r.get('p120'))}</td><td>{_num(r.get('p360'))}</td>"
            f"<td>{_TRANSITION_LABEL.get(r.get('transition', 'N/A'), r.get('transition', 'N/A'))}</td></tr>")
    return f"""
<h2>Layer B · Repair-Retest Scanner</h2>
<div class="card">
<table>
<thead><tr><th>ETF</th><th>V1 阶段</th><th>产业簇</th><th>底部状态</th><th>pos60</th><th>pos120</th><th>pos360</th><th>迁移</th></tr></thead>
<tbody>{body}</tbody></table>
<p class="meta">TARGET = pos60 Q1 × pos120 Q3 · NEAR_MISS = Q1×Q2（p120 差一档）或 Q2×Q3（p60 差一档），cut points 全部来自 frozen V1，不重算。cohort = 数据质量门内 BASE/EXTENSION 标记。迁移相对统一 market prev-trade-date；prev 日无数据标「prev 日无数据」。</p>
</div>"""


def _num(v, nd=1):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{v:.{nd}f}"


def _layer_c_section(rows: list[dict]) -> str:
    if not rows:
        return '<h2>Layer C · Historical Odds</h2>\n<div class="card"><p class="meta">无值得评估的当前结构。</p></div>'
    body = ""
    for r in rows:
        h = r.get("odds") or {}
        med = h.get("median_120d")
        med_cls = "pos" if (med is not None and med > 0) else ("neg" if (med is not None and med < 0) else "")
        odds = r.get("odds_assessment", "unreliable")
        tag = {"strong_observe": "tag-strong", "watch_structure": "tag-watch",
               "position_only": "tag-pos", "cautious": "tag-caut",
               "out_of_domain_good": "tag-pos", "out_of_domain_unknown": "tag-watch",
               "out_of_domain_bad": "tag-caut", "unreliable": "tag-unrel"}.get(odds, "tag-unrel")
        n = h.get("n")
        body += (
            f"<tr><td><b>{r['fund_code']}</b> · {r.get('fund_name', '')} {_cohort_tag(r['cohort'])}</td>"
            f"<td>{_TARGET_LABEL.get(r['target_stage'], r['target_stage'])}</td>"
            f"<td>{str(n) if n is not None else '—'}</td>"
            f"<td class='{med_cls}'>{_pct(med, 1)}</td>"
            f"<td>{_pct(h.get('win_rate'), 0, False)}</td>"
            f"<td>{_num(h.get('payoff_ratio'))}</td>"
            f"<td>{_EVIDENCE_LABEL.get(r.get('evidence_label', ''), r.get('evidence_label', ''))}</td>"
            f"<td><span class='tag {tag}'>{_ODDS_LABEL.get(odds, odds)}</span></td></tr>")
    return f"""
<h2>Layer C · Historical Odds</h2>
<div class="card">
<table>
<thead><tr><th>ETF</th><th>V1 阶段</th><th>n</th><th>120D 中位</th><th>胜率</th><th>Payoff</th><th>时间证据</th><th>最终判断</th></tr></thead>
<tbody>{body}</tbody></table>
<p class="meta">仅对 in-domain ∪ near-miss ∪ watch 池附历史赔率（避免 901 只全跑）。n = 该 ETF 历史长期底部 entry 数；证据=冻结历史研究中的跨年/样本审计。</p>
</div>"""


def render_scan(payload: dict, out_path: Path | None = None) -> Path:
    out_path = out_path or (STUDY_DIR / f"scan_{str(payload['as_of']).replace('-', '')}.html")
    a = payload["layer_a_market_bottom_map"]
    dom = payload["domain"]
    watch_total = payload.get("watch_pool_total")
    headline = (
        f"{a['reliable_total']} 只 reliable ETF（BASE {a['cohort']['BASE']} / EXTENSION {a['cohort']['EXTENSION']}）中，"
        f"{a['long_term_bottom_total']} 只处于长期底部（DEEP {a['deep_total']} / RECOVERING {a['recovering_total']}），"
        f"命中结构触发候选 {a['target_total']} 只、NEAR_MISS {a['near_miss_total']} 只（差一档待观察）。"
        f"今日迁移：新进入域 {a['transition_counts'].get('ENTER_DOMAIN', 0)}、退出域 {a['transition_counts'].get('EXIT_DOMAIN', 0)}、"
        f"DEEP→RECOVERING {a['transition_counts'].get('DEEP_TO_RECOVERING', 0)}、RECOVERING→DEEP {a['transition_counts'].get('RECOVERING_TO_DEEP', 0)}。"
    )
    funnel_note = (
        f"漏斗：全市场 ETF → reliable（{a['reliable_total']}）→ 长期底部域（{a['long_term_bottom_total']}）→ 结构触发候选 TARGET → Current Odds → 最终判断。"
        f"{watch_total} 只观察池是独立的人工关注名单（我主动盯谁），不是这个漏斗的上一级。"
        if watch_total else ""
    )

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Repair-Retest V1 Full-Market Scan</title>
<style>{_CSS}</style></head><body>
<div class="wrap">
<header>
  <h1>Repair-Retest V1 · 全市场每日底部扫描</h1>
  <div class="meta">as_of={payload['as_of']} · 规则={payload['rule_id']}（{payload['rule_status']}）· {payload['generated_at']}</div>
</header>

<div class="summary">
  <h3>一句话结论</h3>
  <p>{headline}</p>
  <p class="meta">{funnel_note}</p>
</div>

{_layer_a_section(a)}
{_layer_b_section(payload['layer_b_repair_retest_scanner'])}
{_layer_c_section(payload['layer_c_historical_odds'])}

<h2>口径与限定</h2>
<div class="card"><ul>
<li>规则 = <code>{payload['rule_id']}</code> 冻结 spec（<code>{payload['rule_spec_source']}</code>），872 只今天的数据只进规则、永不反向重算 cut points（防自适应漂移）</li>
<li>reliable = full_360_sample ∧ ¬unreliable_360 ∧ ¬flat_price_noise（货币/债券近零波动单列 audit，不进底部判定）</li>
<li>domain guard = pos120≤{dom['price_pos_120_max']} 且 pos360≤{dom['price_pos_360_max']}（长期底部）；in_domain = reliable ∧ long_term_bottom</li>
<li>TARGET = pos60 Q1 × pos120 Q3（cut：p60&lt;14.55 且 p120&gt;15.82）；NEAR_MISS = Q1×Q2（p120 差一档）或 Q2×Q3（p60 差一档）</li>
<li>cohort 数据驱动：BASE = reliable ∧ hist≥756（V1 原始研究支持范围）；EXTENSION = reliable ∧ 360≤hist&lt;756（规则外推）。cohort 只是报告标记，不改变规则</li>
<li>no look-ahead：全部用 as_of={payload['as_of']} 当日及以前可观察信息；迁移相对统一 market prev-trade-date（{a.get('prev_trade_date', '—')}），prev 日无数据标 PREV_MISSING 且保留 prev_actual_trade_date 审计</li>
<li>本报告是市场观察，不是买入建议</li>
</ul></div>

<footer>AKsignal · Lane 2 · Repair-Retest V1 Daily Scan · {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</footer>
</div></body></html>"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path
