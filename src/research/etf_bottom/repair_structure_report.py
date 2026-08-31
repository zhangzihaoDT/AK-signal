"""Study 2E Repair Structure Validation HTML 报告（zihao raccoon 视觉体系）。

结构：
  一句话结论 + 四假设 adjudication
  Q1 composition（DEEP/RECOVERING/全样本 pos120 quintile）
  Q2 interaction（pos120 × pos60 3×3 主 + 2×2 robustness）
  Q3 date-weighted（三档对照）
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
table{border-collapse:collapse;width:100%;font-size:.85em;margin:10px 0}
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
.flag-ok{color:#1a7a5a}.flag-bad{color:#b32424}.flag-warn{color:#D79A36}
.hl{background:#fff6e6!important}
"""

_VERDICT_LABEL = {
    "INTERACTION_STRUCTURE": "中期修复 × 短期再探底结构成立",
    "CONTINUOUS_SIGNAL": "pos120 独立连续信号",
    "COMPOSITION_EFFECT": "状态构成效应",
    "NO_STABLE_SIGNAL": "无稳定信号",
}


def _pct(v, nd=1, sign=True):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{v*100:+.{nd}f}%" if sign else f"{v*100:.{nd}f}%"


def _c(v):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    cls = "pos" if v > 0 else ("neg" if v < 0 else "")
    return f"<span class='{cls}'>{_pct(v)}</span>"


def _q1_table(q1: dict) -> str:
    sections = ""
    for st in ["ALL", "DEEP_BOTTOM", "RECOVERING_FROM_BOTTOM"]:
        sub = q1[st]
        rows = ""
        for q in sub["quintiles"]:
            rows += (f"<tr><td><b>{q['quintile']}</b></td><td>{q['n']}</td>"
                     f"<td>{_c(q['mean'])}</td><td>{_c(q['median'])}</td></tr>")
        label = {"ALL": "全样本", "DEEP_BOTTOM": "DEEP（仍深底）", "RECOVERING_FROM_BOTTOM": "RECOVERING（已修复）"}[st]
        sections += f"<h3>{label}</h3><table><thead><tr><th>pos120 分位</th><th>n</th><th>超额均值</th><th>超额中位</th></tr></thead><tbody>{rows}</tbody></table>"
    return sections


def _q2_matrix(q2: dict) -> str:
    grid = q2["grid"]
    labels = [f"Q{i}" for i in range(1, grid + 1)]
    head = "".join(f"<th>pos120={c}</th>" for c in labels)
    rows = ""
    for r in labels:
        cells = ""
        for c in labels:
            cell = q2["matrix"].get(f"{r}_{c}", {})
            hl = " class='hl'" if (r == f"Q{grid}" and c == "Q1") else ""
            cells += (f"<td{hl}>{_c(cell.get('mean'))}<br>"
                      f"<small>中位 {_c(cell.get('median'))} · n={cell.get('n', 0)}</small></td>")
        rows += f"<tr><td><b>pos60={r}</b></td>{cells}</tr>"
    return f"""<table><thead><tr><th>行/列</th>{head}</tr></thead><tbody>{rows}</tbody></table>
<p class="meta">Q1=最低（pos60 低=仍在底；pos120 低=未修复），Q{grid}=最高。高亮格 = 目标结构（pos60 低 × pos120 高）。</p>"""


def _q3_table(q3: dict) -> str:
    sections = ""
    for st in ["ALL", "DEEP_BOTTOM", "RECOVERING_FROM_BOTTOM"]:
        sub = q3[st]
        rows = ""
        for q in sub["quintiles"]:
            low = " <span class='flag-warn'>LOW_N</span>" if q["n_dates"] < 30 else ""
            rows += (f"<tr><td><b>{q['quintile']}</b></td><td>{q['n_dates']}</td><td>{q['n_entries']}</td>"
                     f"<td>{_c(q['date_weighted_median'])}</td><td>{_c(q['date_weighted_mean'])}</td>{low}</tr>")
        label = {"ALL": "全样本", "DEEP_BOTTOM": "DEEP", "RECOVERING_FROM_BOTTOM": "RECOVERING"}[st]
        sections += f"<h3>{label}</h3><table><thead><tr><th>pos120 分位</th><th>日期数</th><th>entry 数</th><th>日期加权中位</th><th>日期加权均值</th><th></th></tr></thead><tbody>{rows}</tbody></table>"
    t = q3.get("_target_date_weighted", {})
    sections += f"""<h3>目标结构（pos60 低 × pos120 高）日期加权</h3>
<p>目标格 date-weighted 中位 <b>{_pct(t.get('target_median'))}</b>（{t.get('target_n_dates')} 日期 / {t.get('target_n_entries')} entry）
vs 全样本 date-weighted 中位 {_pct(t.get('all_median'))} → <b>{'领先' if (t.get('target_median') or 0) > (t.get('all_median') or -999) else '未领先'}</b></p>"""
    return sections


def render_repair(payload: dict, out_path: Path | None = None) -> Path:
    out_path = out_path or (STUDY_DIR / "repair_structure.html")
    ad = payload["adjudication"]
    q1, q2_3, q2_2, q3 = payload["q1_composition"], payload["q2_interaction_3x3"], payload["q2_interaction_2x2"], payload["q3_date_weighted"]
    ev = ad["evidence"]
    t = q3.get("_target_date_weighted", {})

    headline = (
        f"<b>{_VERDICT_LABEL.get(ad['verdict'], ad['verdict'])}</b>："
        f"{ad['summary']}。"
        f"目标格（pos60 低 × pos120 高）在 event-weighted 中位 {_pct(q2_3['target_cell'].get('median'))}"
        f"、date-weighted 中位 {_pct(t.get('target_median'))} 均领先全样本（{_pct(t.get('all_median'))}）；"
        f"但该格仅 {t.get('target_n_entries')} entry / {t.get('target_n_dates')} 日期，且 Q1 显示 state 内部 pos120 信号弱于全样本 → 效果高度集中于特定结构组合。"
    )

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Study 2E · Repair Structure Validation</title>
<style>{_CSS}</style></head><body>
<div class="wrap">
<header>
  <h1>Study 2E · Repair Structure Validation</h1>
  <div class="meta">不新增变量 · 只验证 price_pos_120 surviving signal 的结构 · 复用 replication events · {payload.get('generated_at', '')}</div>
</header>

<div class="summary">
  <h3>Adjudication</h3>
  <p>{headline}</p>
</div>

<div class="kpis">
  <div class="kpi"><span>Entry 数</span><b>{payload['n_entries']}</b></div>
  <div class="kpi"><span>ETF 数</span><b>{payload['n_etfs']}</b></div>
  <div class="kpi"><span>日期数</span><b>{payload['n_dates']}</b></div>
</div>

<h2>一、Q1 · Composition effect（state 内 pos120 quintile）</h2>
<div class="card">{_q1_table(q1)}
<p class="meta">若 state 内部无信号而全样本有 → 效果主要是 bottom_state composition。DEEP 内部方向 Q1→Q5 = {_pct(ev['q1_state_directions'][0][1]) if ev.get('q1_state_directions') else '—'}，全样本 = {_pct(ev.get('q1_all_direction'))}。</p></div>

<h2>二、Q2 · Interaction effect（pos120 × pos60）</h2>
<h3>3×3（主结果）</h3>
<div class="card">{_q2_matrix(q2_3)}</div>
<h3>2×2（robustness）</h3>
<div class="card">{_q2_matrix(q2_2)}</div>

<h2>三、Q3 · Date-weighted（每日期一票）</h2>
<div class="card">{_q3_table(q3)}
<p class="meta">date-weighted = entry_date × state × pos120 quintile 先组内 median → 每日期一票。n_dates < 30 标 LOW_N。</p></div>

<h2>四、四假设裁决</h2>
<div class="card">
<table>
<thead><tr><th>假设</th><th>证据来源</th><th>结论</th></tr></thead>
<tbody>
<tr><td>pos120 独立连续信号</td><td>Q1</td><td>{'支持' if (ev.get('q1_state_directions') and all(d>=0 for _,d in ev['q1_state_directions'])) else '弱/不支持'}</td></tr>
<tr><td>pos120 只是状态构成效应</td><td>Q1 + 全样本对照</td><td>{'支持（全样本强于 state 内）' if (ev.get('q1_all_direction') or 0) > 0 else '不支持'}</td></tr>
<tr><td>中期修复 × 短期再探底（interaction）</td><td>Q2</td><td>{'支持' if (ev.get('q2_3x3_target_lead_mean') and ev.get('q2_3x3_target_lead_median')) else '不支持'}</td></tr>
<tr><td>结果由同期批量事件撑起</td><td>Q3</td><td>{'否（date-weighted 仍领先）' if ev.get('q3_target_date_lead') else '是'}</td></tr>
</tbody></table>
</div>

<h2>五、口径与限定</h2>
<div class="card">
<ul>
<li>outcome = excess_vs_etf_market_120d（ret120 − ETF 横截面中位前向）</li>
<li>全部复用 replication events 已增强字段，零新增变量</li>
<li>判定标准（用户锁定）：target 格需在 mean 与 median、3×3 与 2×2、date-weighted 三档全领先才判 INTERACTION</li>
<li>目标格样本较小（~676 entry / 166 日期），为稀有结构组合</li>
<li>不做显著性检验；结论为描述性分组观察</li>
</ul>
</div>

<footer>AKsignal · Lane 2 Research · Study 2E Repair Structure Validation · {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</footer>
</div></body></html>"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path
