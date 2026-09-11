"""Leader Breakout Event V1 — HTML report renderer (纯消费 payload，不重算)。"""

from __future__ import annotations

from html import escape
from typing import Any

from . import STUDY_DIR

_CSS = """
:root{--blue:#174A7C;--deep:#06213D;--cyan:#7ECDEB;--lt:#DDEFF8;--cream:#FFF9EF;
--gold:#D79A36;--text:#1F2D3D;--muted:#6B7C8F;--card:#fff;}
*{box-sizing:border-box}
body{margin:0;background:var(--cream);color:var(--text);
font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;line-height:1.55}
.wrap{max-width:1080px;margin:0 auto;padding:32px 20px 64px}
h1{color:var(--deep);font-size:26px;margin:0 0 4px}
h2{color:var(--blue);font-size:19px;margin:34px 0 10px;border-left:4px solid var(--gold);padding-left:10px}
.sub{color:var(--muted);font-size:13px;margin-bottom:18px}
.card{background:var(--card);border:1px solid #E7EEF5;border-radius:14px;padding:16px 18px;margin:12px 0;
box-shadow:0 1px 3px rgba(23,74,124,.06)}
.kpi{display:flex;flex-wrap:wrap;gap:12px}
.kpi .box{flex:1 1 160px;background:var(--lt);border-radius:10px;padding:12px 14px}
.kpi .box .v{font-size:22px;font-weight:700;color:var(--blue)}
.kpi .box .l{font-size:12px;color:var(--muted)}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:6px 9px;text-align:right;border-bottom:1px solid #EEF3F8}
th:first-child,td:first-child{text-align:left}
th{color:var(--blue);font-weight:600;background:#F7FBFE}
.pass{color:#1a7f37;font-weight:700}.fail{color:#b42318;font-weight:700}
.tag{display:inline-block;padding:1px 8px;border-radius:8px;background:var(--lt);color:var(--blue);font-size:12px;margin-right:6px}
.caveats{font-size:13px;color:var(--muted)}
.caveats li{margin:4px 0}
details{margin:8px 0}
summary{cursor:pointer;color:var(--blue);font-weight:600}
.foot{margin-top:30px;font-size:12px;color:var(--muted);border-top:1px solid #E7EEF5;padding-top:12px}
"""


def _pct(v: Any, digits: int = 1) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v) * 100:+.{digits}f}%"
    except (TypeError, ValueError):
        return "—"


def _num(v: Any, digits: int = 2) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def _stat_table(stats: dict[str, Any]) -> str:
    rows = [
        ("样本", stats.get("n")),
        ("均值", _pct(stats.get("mean"))),
        ("中位", _pct(stats.get("median"))),
        ("胜率", _pct(stats.get("win_rate"))),
    ]
    return "".join(f"<tr><td>{k}</td><td>{v if v is not None else '—'}</td></tr>" for k, v in rows)


def render_report(payload: dict[str, Any]) -> str:
    meta = payload.get("meta", {})
    q1 = payload.get("q1_forward_edge", {})
    q2 = payload.get("q2_early_vs_wait", {})
    q3 = payload.get("q3_small_position", {})
    sens = payload.get("sensitivity", {})
    gates = payload.get("gates", {})
    verdict = gates.get("verdict", "FAIL")
    vcls = "pass" if verdict == "PASS" else "fail"

    h: list[str] = [f"<!doctype html><html lang='zh'><head><meta charset='utf-8'>",
                    f"<title>Leader Breakout Event V1</title><style>{_CSS}</style></head><body><div class='wrap'>"]
    h.append("<h1>Leader Breakout Event V1</h1>")
    h.append(f"<div class='sub'>{escape(str(meta.get('rule_id')))} · "
             f"{escape(str(meta.get('rule_status')))} · frozen {escape(str(meta.get('frozen_at')))} · "
             f"spec {escape(str(meta.get('spec_source')))}</div>")

    h.append("<div class='card'><div class='kpi'>")
    h.append(f"<div class='box'><div class='v'>{meta.get('n_events', 0)}</div><div class='l'>事件数</div></div>")
    h.append(f"<div class='box'><div class='v'>{meta.get('n_symbols', 0)}</div><div class='l'>个股数</div></div>")
    h.append(f"<div class='box'><div class='v {vcls}'>{verdict}</div><div class='l'>预冻结门槛判定</div></div>")
    h.append(f"<div class='box'><div class='v'>{q2.get('n_with_confirm', 0)}/{q2.get('n_events', 0)}</div>"
             f"<div class='l'>事件后 120D 内主题确认</div></div>")
    h.append("</div>")
    h.append("<div style='margin-top:8px'>" + "".join(
        f"<span class='tag'>{escape(str(t))}</span>" for t in meta.get("themes", [])) + "</div></div>")

    # Q1
    h.append("<h2>Q1 · 前瞻收益（分层超额 vs 同主题中位）</h2><div class='card'><table>")
    h.append("<tr><th>Horizon</th><th>绝对中位</th><th>绝对均值</th><th>超额中位</th><th>超额均值</th><th>n</th></tr>")
    for hz, d in (q1.get("by_horizon") or {}).items():
        ex = d.get("excess", {})
        rt = d.get("ret", {})
        h.append(f"<tr><td>{hz}D</td><td>{_pct(rt.get('median'))}</td><td>{_pct(rt.get('mean'))}</td>"
                 f"<td>{_pct(ex.get('median'))}</td><td>{_pct(ex.get('mean'))}</td><td>{ex.get('n', 0)}</td></tr>")
    h.append("</table>")
    h.append("<details><summary>分年超额（primary horizon）</summary><table>")
    h.append("<tr><th>年份</th><th>n</th><th>超额中位</th><th>超额均值</th><th>绝对中位</th></tr>")
    for y, d in sorted((q1.get("by_year") or {}).items()):
        h.append(f"<tr><td>{escape(y)}</td><td>{d.get('n')}</td><td>{_pct(d.get('excess_median'))}</td>"
                 f"<td>{_pct(d.get('excess_mean'))}</td><td>{_pct(d.get('ret_median'))}</td></tr>")
    h.append("</table></details>")
    h.append("<details><summary>标签分层（超额中位）</summary><table>")
    h.append("<tr><th>标签</th><th>取值</th><th>n</th><th>超额中位</th><th>绝对中位</th></tr>")
    for tag, vals in (q1.get("by_tag") or {}).items():
        for val, d in vals.items():
            h.append(f"<tr><td>{escape(str(tag))}</td><td>{escape(str(val))}</td><td>{d.get('n')}</td>"
                     f"<td>{_pct(d.get('excess_median'))}</td><td>{_pct(d.get('ret_median'))}</td></tr>")
    h.append("</table></details>")
    if q1.get("ma60_x_ma120"):
        h.append("<details><summary>MA60 × MA120 条件组合</summary><table>")
        h.append("<tr><th>条件</th><th>n</th><th>超额中位</th><th>绝对中位</th></tr>")
        for k, d in q1["ma60_x_ma120"].items():
            h.append(f"<tr><td>{escape(k)}</td><td>{d.get('n')}</td><td>{_pct(d.get('excess_median'))}</td>"
                     f"<td>{_pct(d.get('ret_median'))}</td></tr>")
        h.append("</table></details>")
    h.append("</div>")

    # Q2
    h.append("<h2>Q2 · 提前 vs 等主题确认</h2><div class='card'><table>")
    h.append("<tr><th>口径</th><th>n</th><th>中位</th><th>均值</th><th>胜率</th></tr>")
    for key, label in (("early", "事件日入场（提前）"), ("wait", "等确认后入场"), ("incremental", "增量（提前−等待）")):
        s = q2.get(key, {})
        h.append(f"<tr><td>{label}</td><td>{s.get('n', 0)}</td><td>{_pct(s.get('median'))}</td>"
                 f"<td>{_pct(s.get('mean'))}</td><td>{_pct(s.get('win_rate'))}</td></tr>")
    h.append("</table>")
    h.append(f"<div class='sub'>提前优于等待比例：{_pct(q2.get('share_early_gt_wait'))}；"
             f"窗口内未确认事件 {q2.get('n_no_confirm_within_lookahead', 0)} 个（等待策略下不入场）。</div>")
    h.append("</div>")

    # Q3
    h.append("<h2>Q3 · 小仓位 sleeve（研究近似）</h2><div class='card'><table>")
    h.append("<tr><th>权重</th><th>提前 Sharpe</th><th>提前 Calmar</th><th>提前 MaxDD</th>"
             "<th>等待 Sharpe</th><th>等待 Calmar</th><th>ΔCalmar</th></tr>")
    for w, d in (q3.get("weights") or {}).items():
        em = d.get("early", {})
        wm = d.get("wait", {})
        h.append(f"<tr><td>{w}</td><td>{_num(em.get('sharpe'))}</td><td>{_num(em.get('calmar'))}</td>"
                 f"<td>{_pct(em.get('max_drawdown'))}</td><td>{_num(wm.get('sharpe'))}</td>"
                 f"<td>{_num(wm.get('calmar'))}</td><td>{_num(d.get('delta_calmar'))}</td></tr>")
    h.append("</table><div class='sub'>sleeve = 每日等权持有活跃事件 × weight，close-to-close，含 20bp 单边成本近似。</div></div>")

    # Gates
    h.append("<h2>多层门槛（预先冻结）</h2><div class='card'><table>")
    h.append("<tr><th>门槛</th><th>结果</th><th>关键值</th></tr>")
    l1 = gates.get("L1_statistical", {})
    h.append(f"<tr><td>L1 统计（date-block bootstrap 95% CI）</td>"
             f"<td class='{'pass' if l1.get('pass') else 'fail'}'>{'通过' if l1.get('pass') else '未过'}</td>"
             f"<td>point {_pct(l1.get('point'))} · CI [{_pct(l1.get('lo'))}, {_pct(l1.get('hi'))}]</td></tr>")
    l2 = gates.get("L2_economic_years", {})
    h.append(f"<tr><td>L2 经济（跨年正超额年数）</td>"
             f"<td class='{'pass' if l2.get('pass') else 'fail'}'>{'通过' if l2.get('pass') else '未过'}</td>"
             f"<td>{l2.get('positive_years')} / 需 {l2.get('min_required')} 年</td></tr>")
    l3 = gates.get("L3_path_risk", {})
    h.append(f"<tr><td>L3 路径/风险</td>"
             f"<td class='{'pass' if l3.get('pass') else 'fail'}'>{'通过' if l3.get('pass') else '未过'}</td>"
             f"<td>MAE 中位 {_pct(l3.get('median_mae'))} · 胜率 {_pct(l3.get('win_rate'))} · "
             f"单票占比 {_pct(l3.get('single_stock_share'))}</td></tr>")
    l4 = gates.get("L4_portfolio", {})
    h.append(f"<tr><td>L4 组合（sleeve vs 等待）</td>"
             f"<td class='{'pass' if l4.get('pass') else 'fail'}'>{'通过' if l4.get('pass') else '未过'}</td>"
             f"<td>Sharpe {_num((l4.get('early') or {}).get('sharpe'))} · "
             f"Calmar≥等待 {l4.get('calmar_ge_wait')}</td></tr>")
    h.append("</table>")
    h.append("<details><summary>L5 参数敏感性（阈值 × cooldown）</summary><table>")
    h.append("<tr><th>score 阈值</th><th>cooldown</th><th>n</th><th>超额中位</th><th>正向</th></tr>")
    for r in (sens.get("grid") or []):
        h.append(f"<tr><td>{r.get('threshold')}</td><td>{r.get('cooldown')}</td><td>{r.get('n_events')}</td>"
                 f"<td>{_pct(r.get('excess_median'))}</td><td>{'是' if r.get('positive') else '否'}</td></tr>")
    h.append(f"</table><div class='sub'>正向组合 {sens.get('n_positive')}/{sens.get('n_total')}</div></details>")
    h.append("</div>")

    # Caveats
    h.append("<h2>口径与限定</h2><div class='card caveats'><ul>")
    for c in meta.get("caveats", []):
        h.append(f"<li>{escape(str(c))}</li>")
    h.append("</ul></div>")

    h.append(f"<div class='foot'>Leader Breakout Event V1 · {escape(str(meta.get('generated_at')))} · "
             f"窗口 {escape(str((meta.get('window') or {}).get('start')))} → "
             f"{escape(str((meta.get('window') or {}).get('end')))}</div>")
    h.append("</div></body></html>")
    html = "".join(h)
    STUDY_DIR.mkdir(parents=True, exist_ok=True)
    out = STUDY_DIR / "leader_breakout_report.html"
    out.write_text(html, encoding="utf-8")
    return str(out)
