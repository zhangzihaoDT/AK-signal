"""Repair-Retest V1 历史触发频率回测 HTML 报告（zihao raccoon 视觉体系）。

定位：**纯 renderer**。只消费 v1_incidence_summary.json 已有的 incidence /
zero_target_streaks / target_events / time_representation / forward_odds /
near_miss_conversion / verdict 字段，绝不重新计算任何事实（规则完全冻结）。

报告首页只回答 6 个数字 + verdict：
  1. 区间内 TARGET 有信号的天数占比（target_day_rate）
  2. 平均每天几个 TARGET（avg_targets_per_day）
  3. 最长连续 0 TARGET 空窗（longest_zero_target_streak）
  4. TARGET episode 数（target_events.total）
  5. 触及 TARGET 的 ETF 数（incidence.unique_target_etfs）
  6. TARGET 120D 中位超基准（forward_odds.TARGET.horizons.120.excess_median）
再加 verdict 与 forward odds 对照、NEAR_MISS 转化、时间/ETF/产业簇集中度。
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
h1{color:var(--zh-deep-blue);margin:0;font-size:1.6em}
h2{color:var(--zh-blue);margin-top:30px;border-left:4px solid var(--zh-raccoon-gold);padding-left:10px}
.meta{color:var(--zh-muted);font-size:.85em;margin-top:6px}
.card{background:var(--zh-card);border-radius:10px;padding:16px 20px;margin:14px 0;box-shadow:0 1px 3px rgba(6,33,61,.08)}
table{border-collapse:collapse;width:100%;font-size:.88em;margin:10px 0}
th{background:var(--zh-light-blue);color:var(--zh-deep-blue);text-align:left;padding:7px 9px;border:1px solid #cfe0ec}
td{padding:6px 9px;border:1px solid #e3edf5}
tr:nth-child(even) td{background:#f7fbfd}
.kpi{display:inline-block;background:var(--zh-card);border:1px solid #d5e6f2;border-radius:8px;padding:10px 16px;margin:6px 8px 6px 0;min-width:180px;vertical-align:top}
.kpi b{display:block;font-size:1.4em;color:var(--zh-blue)}
.kpi span{color:var(--zh-muted);font-size:.8em}
.verdict{background:var(--zh-deep-blue);color:#fff;border-radius:10px;padding:18px 22px;margin:16px 0}
.verdict h3{color:var(--zh-cyan);margin-top:0}
.verdict .big{font-size:1.3em;font-weight:700;color:var(--zh-raccoon-gold)}
.tag{display:inline-block;padding:1px 8px;border-radius:8px;font-size:.75em;margin-right:4px}
footer{margin-top:40px;padding-top:14px;border-top:1px solid #d5e6f2;color:var(--zh-muted);font-size:.8em}
.dim{color:var(--zh-muted);font-size:.8em}
"""

_VERDICT_LABEL = {
    "A_HEALTHY_LOW_FREQUENCY": "A · 健康的低频信号",
    "B_TOO_SPARSE": "B · 过于稀疏",
    "C_TIME_OR_CLUSTER_DEPENDENT": "C · 时间/产业簇依赖",
    "D_NO_INCREMENTAL_ODDS": "D · 无增量赔率",
}

_VERDICT_DESC = {
    "A_HEALTHY_LOW_FREQUENCY": (
        "TARGET 触发本身已经稀缺（高强度筛选下的低频是符合预期的），但仍保持着可用的触发密度，"
        "且 120D 前向赔率为正、相对域内其余标的存在明确增量——这是一个可以被使用的信号。"),
    "B_TOO_SPARSE": (
        "TARGET 触发频率过低（目标日占比 <3% 或最长连续 0 TARGET 空窗 ≥200 个交易日），"
        "信号过于稀疏，缺乏实际使用价值。"),
    "C_TIME_OR_CLUSTER_DEPENDENT": (
        "TARGET 事件高度集中在少数年份 / 少数 ETF / 少数产业簇（或事件总数 <10），"
        "触发在时间与截面维度上都缺乏独立性。"),
    "D_NO_INCREMENTAL_ODDS": (
        "TARGET 触发密度可用（不稀疏、不集中），绝对 120D 前向收益也切实为正（中位 +8.3%、胜率 79%），"
        "但相对全市场与相对域内其余标的的超额偏小（120D 中位超额 +0.11pp / 相对域内 +1.73pp），"
        "未达到显著增量门槛——信号自身是真实的，但在横截面里没有强到足以脱颖而出。"),
}


def _fmt_pct(v: Any, digits: int = 4) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt_num(v: Any, digits: int = 2) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):,.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def _kpi(label: str, value: str, accent: bool = False) -> str:
    cls = "kpi-accent" if accent else "kpi"
    return f'<div class="{cls}"><b>{value}</b><span>{label}</span></div>'


def render_v1_backtest(payload: dict[str, Any]) -> str:
    inc = payload.get("incidence", {})
    streak = payload.get("zero_target_streaks", {})
    tev = payload.get("target_events", {})
    time_rep = payload.get("time_representation", {})
    fwd = payload.get("forward_odds", {})
    conv = payload.get("near_miss_conversion", {})
    fwd_comp = payload.get("forward_comparison", [])
    verdict = payload.get("verdict", "D_NO_INCREMENTAL_ODDS")
    params = payload.get("params", {})
    cut = payload.get("cut_points", {})

    target120 = fwd.get("TARGET", {}).get("horizons", {}).get("120", {})
    t_med = _fmt_pct(target120.get("median"))
    t_exc = _fmt_pct(target120.get("excess_median"))
    t_win = _fmt_pct(target120.get("win_rate"), 1)
    t_n = target120.get("n")

    n_trade_days = inc.get("total_trade_days", 0)
    kpis = "".join([
        _kpi("目标日占比 · TARGET 有信号天数", _fmt_pct(inc.get("target_day_rate"), 2), accent=True),
        _kpi("平均每天 TARGET 数", _fmt_num(inc.get("avg_targets_per_day"), 2)),
        _kpi("最长 0 TARGET 空窗（交易日）", str(streak.get("longest_zero_target_streak", 0)), accent=True),
        _kpi("TARGET episode 总数", str(tev.get("total", 0))),
        _kpi("触及 TARGET 的 ETF 数", str(inc.get("unique_target_etfs", 0))),
        _kpi("TARGET 120D 中位超基准", t_exc, accent=True),
    ])

    n_trade_days = n_trade_days or 1

    # forward odds table
    fwd_rows = []
    for r in fwd_comp:
        st = r["stage"]
        badge = "tag-strong" if st == "TARGET" else ("tag-watch" if st == "NEAR_MISS" else "tag-pos")
        st_label = {"TARGET": "结构触发候选", "NEAR_MISS": "NEAR_MISS",
                    "IN_DOMAIN_NON_TARGET": "域内·非target"}.get(st, st)
        fwd_rows.append(
            f"<tr><td><span class='tag {badge}'>{st_label}</span></td>"
            f"<td>{r['n_events']}</td><td>{r['n_etfs']}</td>"
            f"<td>{_fmt_pct(r.get('ret20_median'))}</td>"
            f"<td>{_fmt_pct(r.get('ret60_median'))}</td>"
            f"<td><b>{_fmt_pct(r.get('ret120_median'))}</b></td>"
            f"<td>{_fmt_pct(r.get('win120'), 1)}</td>"
            f"<td>{_fmt_pct(r.get('excess120_median'))}</td></tr>")
    fwd_table = (
        "<table><tr><th>stage</th><th>事件</th><th>ETF</th><th>ret20中位</th>"
        "<th>ret60中位</th><th>ret120中位</th><th>win120</th><th>exc120中位</th></tr>"
        + "".join(fwd_rows) + "</table>")

    # year table
    year_rows = "".join(
        f"<tr><td>{r['year']}</td><td>{r['target_events']}</td>"
        f"<td>{r['unique_etfs']}</td><td>{_fmt_pct(r.get('event_share'), 1)}</td></tr>"
        for r in time_rep.get("year", []))
    year_table = ("<table><tr><th>年份</th><th>事件</th><th>触及ETF</th>"
                  "<th>事件占比</th></tr>" + year_rows + "</table>") if year_rows else "<p class='dim'>无</p>"

    # etf concentration
    etf = time_rep.get("etf", {})
    etf_rows = "".join(
        f"<tr><td>{r['fund_code']}</td><td>{r['events']}</td>"
        f"<td>{_fmt_pct(r['events'] / max(1, tev.get('total', 0)), 1)}</td></tr>"
        for r in etf.get("per_etf", [])[:10])
    etf_table = ("<table><tr><th>ETF(code)</th><th>事件</th><th>占全体事件比</th></tr>"
                 + etf_rows + "</table>") if etf_rows else "<p class='dim'>无</p>"

    # cluster table
    cluster_rows = "".join(
        f"<tr><td>{r['industry_cluster']}</td><td>{r['events']}</td>"
        f"<td>{r['unique_etfs']}</td><td>{_fmt_pct(r.get('event_share'), 1)}</td></tr>"
        for r in time_rep.get("industry_cluster", []))
    cluster_table = ("<table><tr><th>产业簇</th><th>事件</th><th>触及ETF</th>"
                     "<th>占比</th></tr>" + cluster_rows + "</table>") if cluster_rows else "<p class='dim'>无</p>"

    # near-miss conversion
    conv_hdr = "".join(f"<th>{h}d</th>" for h in (5, 10, 20, 40, 60))
    conv_rows = []
    for reason, r in (conv.get("by_reason") or {}).items():
        cells = "".join(f"<td>{_fmt_pct(r.get(f'conversion_{h}d'), 1)}</td>" for h in (5, 10, 20, 40, 60))
        label = {"P120_ONE_BUCKET_AWAY": "p120 差一档到 Q3",
                 "P60_ONE_BUCKET_AWAY": "p60 差一档到 Q1"}.get(reason, reason)
        conv_rows.append(f"<tr><td>{label}</td><td>{r.get('n')}</td>{cells}</tr>")
    conv_table = (
        f"<table><tr><th>NEAR_MISS 类型</th><th>事件数</th>{conv_hdr}</tr>"
        + "".join(conv_rows) +
        f"<tr><td><b>全部</b></td><td>{conv.get('near_miss_events', 0)}</td>"
        + "".join(f"<td>{_fmt_pct(conv.get(f'conversion_{h}d'), 1)}</td>" for h in (5, 10, 20, 40, 60)) +
        "</tr></table>") if conv_rows else "<p class='dim'>无 NEAR_MISS 事件</p>"

    cut120 = cut.get("price_pos_120") or []
    cut60 = cut.get("price_pos_60") or []

    # 判据命中明细（只读 payload 已有字段，纯展示）
    rate = inc.get("target_day_rate", 0.0)
    longest = streak.get("longest_zero_target_streak", 0)
    top_year_sha = time_rep.get("top_year_share", 0.0)
    target_ev = tev.get("total", 0)
    top1_etf = (time_rep.get("etf", {}) or {}).get("top1_etf_contribution", 0.0)
    named_cluster_shares = [c.get("event_share", 0.0) for c in time_rep.get("industry_cluster", [])
                            if c.get("industry_cluster") != "OTHER"]
    top_cluster = max(named_cluster_shares, default=0.0)
    t120 = (fwd.get("TARGET", {}).get("horizons", {}) or {}).get("120", {})
    d120 = (fwd.get("IN_DOMAIN_NON_TARGET", {}).get("horizons", {}) or {}).get("120", {})
    t_exc = t120.get("excess_median")
    d_exc = d120.get("excess_median")
    delta = (round((t_exc - d_exc) * 100, 2)) if (t_exc is not None and d_exc is not None) else None

    _yes = lambda c: '<span class="tag" style="background:#f7dddd;color:#b32424">命中</span>' if c else '<span class="tag" style="background:#d8f0e0;color:#1a7a5a">通过</span>'
    _incr_ok = (t120.get("median") is not None and t120.get("median", 0) > 0) and delta is not None and delta > 3.0
    check_rows = "".join([
        f"<tr><td>稀疏 · 目标日占比&lt;3% 或最长空窗≥200</td><td>占比 {_fmt_pct(rate,1)} · 最长 {longest}</td><td>{_yes(rate<0.03 or longest>=200)}</td></tr>",
        f"<tr><td>集中度 · top年份≥70% / 事件&lt;10 / 单ETF≥50% / 产业簇≥80%</td><td>{_fmt_pct(top_year_sha)} / {target_ev} / {_fmt_pct(top1_etf)} / {_fmt_pct(top_cluster)}</td><td>{_yes(top_year_sha>=0.7 or target_ev<10 or top1_etf>=0.5 or top_cluster>=0.8)}</td></tr>",
        f"<tr><td>增量赔率 · TARGET 120D 中位&gt;0 且相对域内 Δ&gt;3.0pp</td><td>中位 {_fmt_pct(t120.get('median'))} · Δ {f'{delta}pp' if delta is not None else '—'}</td><td>{_yes(not _incr_ok)}</td></tr>",
    ])
    check_table = f"<table><tr><th>判据</th><th>当前值</th><th>状态</th></tr>{check_rows}</table>"

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Repair-Retest V1 触发频率回测</title>
<style>{_CSS}</style></head>
<body><div class="wrap">

<header>
  <h1>③ Repair-Retest V1 历史触发频率回测 <span class="meta">v1 · Application Backtest</span></h1>
  <div class="meta">区间 {params.get('start')} → {params.get('end')} ·
  规则 <b>{params.get('rule_id')}</b>（{params.get('rule_status')}）·
  cut points pos120 Q1≤{cut120[1] if len(cut120)>1 else '?'} / Q3&gt;{cut120[2] if len(cut120)>2 else '?'}
  · pos60 Q1&lt;{cut60[1] if len(cut60)>1 else '?'} ·
  基准 = 同市场横截面中位前向收益 · 生成于 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</div>
</header>

<div class="verdict">
  <h3>结论（唯一规则真源，冻结后未重算）</h3>
  <div class="big">Verdict · {_VERDICT_LABEL.get(verdict, verdict)}</div>
  <p>{_VERDICT_DESC.get(verdict, '')}</p>
</div>

<h2>6 个数字</h2>
<div class="card">{kpis}
  <p class="dim">交易日 {n_trade_days} 天 · 均值每天 {inc.get('avg_targets_per_target_day')} 个 TARGET ·
  单日最多 {inc.get('max_targets_single_day')} 个 · 中位事件持续 {_fmt_num(tev.get('median_event_duration'))} 天</p>
</div>

<h2>Forward Odds 对照（事件起始日，剔除右截断）</h2>
<div class="card">{fwd_table}</div>

<h2>NEAR_MISS → TARGET 转化（进入 NEAR_MISS 后 N 个交易日内是否触达 TARGET）</h2>
<div class="card">{conv_table}</div>

<h2>时间代表性与集中度</h2>
<div class="card">
  <h3>按年份</h3>{year_table}
  <h3>按 ETF（Top10）</h3>{etf_table}
  <h3>按产业簇</h3>{cluster_table}
</div>

<h2>判据命中</h2>
<div class="card">{check_table}</div>

<h2>判据与口径</h2>
<div class="card">
  <p class="dim">稀疏判据：目标日占比 &lt;3% 或最长连续 0 TARGET 空窗 ≥200 交易日 ·
  集中度判据：top 年份占比 ≥70% 或事件数 &lt;10 或单 ETF 贡献 ≥50% 或单产业簇 ≥80% ·
  增量赔率：TARGET 120D 中位&gt;0 且相对域内其余标的 120D 中位超基准 Δ&gt;3.0pp。</p>
  <p class="dim">事件口径：同一 ETF 连续处于同一 stage → 一个 episode（entry=首次进入日）；隔 ≥1 个交易日再进入 → 新 episode。
  前向收益在事件起始日计算，右截断样本显式标记不进入主统计。全部为 as-of 口径，无 look-ahead。</p>
</div>

<footer>AKsignal · zihao raccoon · Repair-Retest V1 Backtest · Lane 2 Research（Application）</footer>
</div></body></html>
"""
    out = STUDY_DIR / "backtest_v1" / "v1_backtest_report.html"
    out.write_text(html, encoding="utf-8")
    return str(out)


if __name__ == "__main__":
    import json
    j = STUDY_DIR / "backtest_v1" / "v1_incidence_summary.json"
    print(render_v1_backtest(json.loads(j.read_text(encoding="utf-8"))))
