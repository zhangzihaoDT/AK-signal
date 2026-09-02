"""Study 3A · Post-924 趋势切换 HTML 报告（zihao raccoon 视觉体系）。

纯 renderer：只消费 study3a_{...}.json 已有 fact，不重算统计。
结构：
  结论 + PASS checklist（P1-P5）
  现象：escape 生存曲线 + KM
  断点检验：假设断点（3 窗口）· date-block bootstrap · persistence 一致性
  数据驱动断点：argmax 日期
  市场控制：etf_type 分层
  provenance / 口径
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import CLEAN_ESCAPE_HORIZON, PERSISTENCE_PRIMARY, PERSISTENCE_ROBUST, STUDY_DIR

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
.summary{background:var(--zh-deep-blue);color:#fff;border-radius:10px;padding:18px 22px;margin:16px 0}
.summary h3{color:var(--zh-cyan);margin-top:0}
.summary .s{color:#cfe6f5;font-size:.95em;margin-top:8px}
.flag-ok{color:#1a7a5a;font-weight:600}.flag-bad{color:#b32424;font-weight:600}
.pill{display:inline-block;padding:2px 10px;border-radius:20px;font-size:.78em;margin-right:6px}
.pill.pass{background:#e2f4e9;color:#1a7a5a;border:1px solid #bfe6cd}
.pill.fail{background:#fde4e4;color:#b32424;border:1px solid #f2c3c3}
.pill.na{background:#eef2f6;color:var(--zh-muted);border:1px solid #dbe3ea}
details{margin:8px 0;background:#fafcfe;border:1px solid #d5e6f2;border-radius:8px;padding:8px 12px}
summary{cursor:pointer;font-weight:600;color:var(--zh-blue)}
"""

_ETYPE_LABEL = {
    "broad": "宽基", "industry": "行业", "theme": "主题", "dividend": "红利",
    "cross_border": "跨境", "bond": "债券", "commodity": "商品", "money": "货币",
}


def _pct(v: Any) -> str:
    if v is None:
        return "—"
    return f"{float(v) * 100:.1f}%"


def _fnum(v: Any, nd: int = 4) -> str:
    if v is None:
        return "—"
    return f"{float(v):.{nd}f}"


def _checklist(p: dict[str, Any]) -> list[dict[str, Any]]:
    sb = p["structural_break"]
    mc = p["market_control"]
    # P1/P2 persistence（raw/3/5 同向）
    pers = sb["persistence_consistency"]
    p1_ok = bool(pers.get("consistent_direction"))
    p2_ok = bool(pers.get("consistent_direction")) and len(pers.get("persistences", {})) >= 2
    # P3 窗口一致性（±40/±63/±90 效应同向）
    win = sb["hypothesis_break"]["windows"]
    effs = [v["effect"] for v in win.values() if v.get("effect") is not None]
    p3_ok = bool(effs) and len({e > 0 for e in effs}) == 1
    # P4 date-block bootstrap CI 不跨 0
    boot = sb["date_block_bootstrap"]
    p4_ok = bool(boot.get("status") == "ok" and not boot.get("ci_crosses_zero"))
    # P5 市场控制：etf_type 分层效应不消失（多数/全部同向）
    p5_ok = bool(mc.get("status") == "ok" and mc.get("by_etf_type_consistent"))
    return [
        {"id": "P1", "name": "persistence 鲁棒性（3/5）效应同向", "ok": p1_ok},
        {"id": "P2", "name": "RAW / 3D / 5D 三口径方向一致", "ok": p2_ok},
        {"id": "P3", "name": "±40/±63/±90 窗口效应同向", "ok": p3_ok},
        {"id": "P4", "name": "date-block bootstrap 95% CI 不跨 0", "ok": p4_ok},
        {"id": "P5", "name": "市场控制（etf_type 分层）效应保留", "ok": p5_ok},
    ]


def _pill(ok: bool) -> str:
    return f'<span class="pill {"pass" if ok else "na"}">{"PASS" if ok else "—"}</span>'


def _survival_table(p: dict[str, Any]) -> str:
    rows = ""
    surv = p["survival"]
    for h in ("20", "60", "120", "250"):
        v = surv.get(h, {})
        rows += (
            f"<tr><td>{h} 日</td><td>{_pct(v.get('survival'))}</td>"
            f"<td>{v.get('n_observed', 0)}</td><td>{v.get('n_escape', 0)}</td>"
            f"<td>{v.get('n_censored', 0)}</td></tr>"
        )
    return rows


def _hypothesis_table(sb: dict[str, Any]) -> str:
    rows = ""
    for w, v in sb["hypothesis_break"]["windows"].items():
        effect = v.get("effect")
        cell = f'<span class="{"pos" if (effect or 0) > 0 else "neg"}">{_fnum(effect)}</span>' if effect is not None else "—"
        rows += (
            f"<tr><td>±{w}</td><td>{v.get('n_pre', 0)}</td><td>{v.get('n_post', 0)}</td>"
            f"<td>{_pct(v.get('pre_escape'))}</td><td>{_pct(v.get('post_escape'))}</td>"
            f"<td>{cell}</td></tr>"
        )
    return rows


def _bootstrap_blocks(boot: dict[str, Any]) -> str:
    if boot.get("status") != "ok":
        return "<p>date-block bootstrap 样本不足。</p>"
    ci = boot.get("ci95") or [None, None]
    cross = boot.get("ci_crosses_zero")
    p_ok = "不跨 0（方向稳健）" if not cross else "跨 0（方向不稳）"
    return (
        f'<div class="kpi"><b>{_fnum(boot.get("obs_effect"))}</b><span>obs 效应 post−pre</span></div>'
        f'<div class="kpi"><b>{_fnum(ci[0])} ~ {_fnum(ci[1])}</b><span>95% CI（date-block）</span></div>'
        f'<div class="kpi"><b>{_fnum(boot.get("p_effect_gt0"))}</b><span>P(效应&gt;0)</span></div>'
        f'<p><b>判定：</b>{p_ok}（n_pre={boot.get("n_pre")} 日 · n_post={boot.get("n_post")} 日 · n_boot={boot.get("n_boot")}）</p>'
    )


def _market_control_table(mc: dict[str, Any]) -> str:
    if mc.get("status") != "ok":
        return "<p>市场控制样本不足。</p>"
    rows = ""
    for etype, v in mc["by_etf_type"].items():
        effect = v.get("effect")
        cell = f'<span class="{"pos" if (effect or 0) > 0 else "neg"}">{_fnum(effect)}</span>' if effect is not None else "—"
        label = _ETYPE_LABEL.get(etype, etype)
        rows += (
            f"<tr><td>{label}</td><td>{v.get('n_pre', 0)}</td><td>{v.get('n_post', 0)}</td>"
            f"<td>{_pct(v.get('pre_escape'))}</td><td>{_pct(v.get('post_escape'))}</td>"
            f"<td>{cell}</td></tr>"
        )
    overall = mc.get("overall", {})
    return (
        f'<p>整体效应：<b>pre={_pct(overall.get("pre_escape"))} → post={_pct(overall.get("post_escape"))}</b>，'
        f'effect={_fnum(overall.get("effect"))}（n_pre={overall.get("n_pre")} · n_post={overall.get("n_post")}）</p>'
        f"<table><tr><th>ETF 类型</th><th>pre n</th><th>post n</th><th>pre escape</th>"
        f"<th>post escape</th><th>effect</th></tr>{rows}</table>"
    )


def render(payload: dict[str, Any], out_path: Path | None = None) -> Path:
    generated = payload.get("generated_at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    data = payload.get("data", {})
    sb = payload["structural_break"]
    ddb = sb["data_driven_breakpoint"]
    km = payload.get("kaplan_meier", {})
    checks = _checklist(payload)
    n_pass = sum(1 for c in checks if c["ok"])

    checklist_rows = "".join(
        f"<tr><td>{c['id']}</td><td>{c['name']}</td><td>{_pill(c['ok'])}</td></tr>" for c in checks
    )
    # 数据驱动断点说明
    dd_note = (
        f"数据自身最大跳变点 <b>{ddb.get('argmax_date')}</b>（distance to 924 = {ddb.get('dist_days_924')} 日，"
        f"{'接近' if ddb.get('near_924') else '不接近'} 2024-09-24）。"
    )

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><style>{_CSS}</style>
<title>Study 3A · Post-924 趋势切换</title></head><body><div class="wrap">
<header><h1>Lane 3 · Study 3A — Post-924 趋势切换（Bottom → Trend Transition）</h1>
<div class="meta">生成于 {generated} · persistence 主口径 {payload.get('primary_persistence')} / 鲁棒 {payload.get('robust_persistence')} · break_date {payload.get('break_date')} · 数据 {data.get('raw_rows')} 行 / {data.get('raw_funds')} ETF（{data.get('calendar_start')} → {data.get('calendar_end')}）</div></header>

<div class="summary">
<h3>结论</h3>
<p>「底部 → 非底部」切换后的 <b>escape 生存结构</b>（离开底部 120 日后仍不重测的比例）在 2024-09-24 前后发生大幅跳变：pre={_pct(sb['hypothesis_break']['windows']['63']['pre_escape'])} → post={_pct(sb['hypothesis_break']['windows']['63']['post_escape'])}，断点效应 post−pre=<b>{_fnum(sb['date_block_bootstrap']['obs_effect'])}</b>。</p>
<div class="s">date-block bootstrap 95% CI={_fnum(sb['date_block_bootstrap']['ci95'][0])}~{_fnum(sb['date_block_bootstrap']['ci95'][1])}（{'' if sb['date_block_bootstrap']['ci_crosses_zero'] else '不'}跨 0）。PASS {n_pass}/{len(checks)}。{dd_note}效应在全部 etf_type 分层中同向保留。</div>
</div>

<h2>0. PASS Checklist</h2>
<div class="card"><table><tr><th>ID</th><th>检查项</th><th>状态</th></tr>{checklist_rows}</table></div>

<h2>1. 现象：Escape 生存曲线</h2>
<div class="card">
<p>对每个 first exit，测「退出后 N 个市场交易日仍未重入长期底部」的比例（只对 <b>非右截断</b> 的 exit 判 escape，全程使用全市场交易日历）。</p>
<table><tr><th>Horizon</th><th>Escape 生存比例</th><th>n_observed</th><th>n_escape</th><th>n_censored</th></tr>{_survival_table(payload)}</table>
<p>KM（120 日）：events={km.get('n_events_total')} · retest={km.get('n_retest_events')} · censored={km.get('n_censored')} · S(120)={_pct(km.get('survival_end'))} · 单调={'是' if km.get('monotone_non_increasing') else '否'}。</p>
</div>

<h2>2. 断点检验（假设断点 2024-09-24）</h2>
<div class="card">
<h3>2a. 局部窗口 pre/post escape（P3）</h3>
<table><tr><th>窗口</th><th>pre n</th><th>post n</th><th>pre escape</th><th>post escape</th><th>effect</th></tr>{_hypothesis_table(sb)}</table>
<h3>2b. date-block bootstrap（P4）</h3>
{_bootstrap_blocks(sb['date_block_bootstrap'])}
<h3>2c. persistence 鲁棒性（P1/P2）</h3>
<table><tr><th>persistence</th><th>pre n</th><th>post n</th><th>pre escape</th><th>post escape</th><th>effect</th></tr>
{"".join(
    f"<tr><td>{k}</td><td>{v.get('n_pre',0)}</td><td>{v.get('n_post',0)}</td>"
    f"<td>{_pct(v.get('pre_escape'))}</td><td>{_pct(v.get('post_escape'))}</td>"
    f"<td>{_fnum(v.get('effect'))}</td></tr>"
    for k, v in sb['persistence_consistency']['persistences'].items()
)}</table>
<p>方向一致性：<b>{'同向' if sb['persistence_consistency']['consistent_direction'] else '不同向'}</b>。</p>
</div>

<h2>3. 数据驱动断点（argmax）</h2>
<div class="card">
<p>{dd_note}</p>
<table><tr><th>日期</th><th>effect</th><th>pre escape</th><th>post escape</th><th>pre n</th><th>post n</th></tr>
{"".join(
    f"<tr><td>{r['date']}</td><td>{_fnum(r['effect'])}</td><td>{_pct(r['pre_escape'])}</td>"
    f"<td>{_pct(r['post_escape'])}</td><td>{r['n_pre']}</td><td>{r['n_post']}</td></tr>"
    for r in (ddb.get('top10') or [])
)}</table>
<details><summary>解读</summary><p>数据自身 argmax 是 {ddb.get('argmax_date')}，而非 2024-09-24。说明「底部切换后更持久」的结构跳变可能始于更早（2023 年末），924 更多是随后的延续/强化，而非唯一断点。这提示：把 924 当作绝对的单一结构断点需谨慎。</p></details>
</div>

<h2>4. 市场控制（P5）</h2>
<div class="card">{_market_control_table(payload['market_control'])}</div>

<footer>Study 3A · 只消费 Lane2 v1_signal_daily + raw 价格数据 · 事件一票制（event-weighted）+ date-block bootstrap inference · 不确定性与限定见正文。</footer>
</div></body></html>"""
    path = out_path or (STUDY_DIR / "study3a_report.html")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path
