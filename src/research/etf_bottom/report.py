"""Study 1 HTML 报告（zihao raccoon 视觉体系）。

结构：
  一句话结论
  四组核心比较表（Entry state × 20D/60D/120D：mean / win_rate / MAE / MFE）
  辅助分布（days to MA20/MA60、n_entries per ETF）
  按 ETF 类型分层
  taxonomy 审计摘要
  provenance / 口径
"""

from __future__ import annotations

import json
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
.flag-ok{color:#1a7a5a}.flag-bad{color:#b32424}
"""

_ETYPE_LABEL = {
    "PRICE_LOW": "价格低位（P756≤20%）",
    "PRICE_LOW_DD30": "低位 + 深跌（DD30≤-20%）",
    "MA20_RECOVERY": "MA20 恢复",
    "MA60_RECOVERY": "MA60 恢复",
}
_TYPE_LABEL = {
    "broad": "宽基", "industry": "行业", "theme": "主题", "dividend": "红利",
    "cross_border": "跨境", "bond": "债券", "commodity": "商品", "money": "货币",
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
    for etype in ("PRICE_LOW", "PRICE_LOW_DD30", "MA20_RECOVERY", "MA60_RECOVERY"):
        rec = summary.get(etype, {})
        meta = rec.get("_meta", {})
        n = meta.get("n_events", 0)
        n_etf = meta.get("n_etfs", 0)
        cells = ""
        for h in ("20", "60", "120"):
            r = rec.get(h, {})
            ret = r.get("ret", {})
            mae = r.get("mae", {})
            mfe = r.get("mfe", {})
            cells += f"<td>{_cell(ret.get('mean'))}<br><small>中位 {_pct(ret.get('median'))} · 胜率 {_pct(ret.get('win_rate'), 0, sign=False)}</small></td>"
            cells += f"<td>{_cell(mae.get('mean'))}</td><td>{_cell(mfe.get('mean'))}</td>"
        rows += (
            f"<tr><td><b>{_ETYPE_LABEL[etype]}</b><br>"
            f"<small>n={n} 事件 · {n_etf} 只 ETF</small></td>{cells}</tr>"
        )
    return f"""<table>
<thead><tr><th>Entry state</th>
<th colspan='2'>20D 收益 / 胜率</th><th>MAE</th><th>MFE</th>
<th colspan='2'>60D 收益 / 胜率</th><th>MAE</th><th>MFE</th>
<th colspan='2'>120D 收益 / 胜率</th><th>MAE</th><th>MFE</th></tr></thead>
<tbody>{rows}</tbody></table>"""


def _aux_table(summary: dict) -> str:
    rows = ""
    for etype in ("PRICE_LOW", "MA20_RECOVERY", "MA60_RECOVERY"):
        rec = summary.get(etype, {})
        d20 = rec.get("_days_low_to_ma20", {})
        d60 = rec.get("_days_low_to_ma60", {})
        n_entries = rec.get("_n_entries_per_etf", {})
        cens = rec.get("_censored_recovery", 0)
        rows += (
            f"<tr><td><b>{_ETYPE_LABEL[etype]}</b></td>"
            f"<td>{d20.get('n', 0) if isinstance(d20, dict) else '—'}</td>"
            f"<td>{_fmt_days(d20.get('median')) if isinstance(d20, dict) else '—'}</td>"
            f"<td>{_fmt_days(d60.get('median')) if isinstance(d60, dict) else '—'}</td>"
            f"<td>{cens if etype == 'PRICE_LOW' else '—'}</td>"
            f"<td>{_fmt_n(n_entries.get('median')) if isinstance(n_entries, dict) else '—'}</td></tr>"
        )
    return f"""<table>
<thead><tr><th>状态</th><th>恢复样本 n</th><th>days_low→MA20 中位</th><th>days_low→MA60 中位</th><th>未恢复(截尾)</th><th>每 ETF 事件数中位</th></tr></thead>
<tbody>{rows}</tbody></table>"""


def _fmt_days(v):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{v:.0f}"


def _fmt_n(v):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{v:.1f}"


def _type_table(by_type: dict) -> str:
    sections = ""
    for etype in ("PRICE_LOW", "MA20_RECOVERY", "MA60_RECOVERY"):
        sub = by_type.get(etype, {})
        if not sub:
            continue
        rows = ""
        for et, rec in sub.items():
            cells = ""
            for h in ("20", "60", "120"):
                r = rec.get(h, {})
                ret = r.get("ret", {})
                cells += f"<td>{_cell(ret.get('mean'))}<br><small>胜率 {_pct(ret.get('win_rate'), 0, sign=False)} · n={ret.get('n', 0)}</small></td>"
            rows += f"<tr><td>{_TYPE_LABEL.get(et, et)}</td>{cells}</tr>"
        sections += f"<h3>{_ETYPE_LABEL[etype]}</h3><table><thead><tr><th>类型</th><th colspan='2'>20D</th><th colspan='2'>60D</th><th colspan='2'>120D</th></tr></thead><tbody>{rows}</tbody></table>"
    return sections


def _dd30_table(dd30: dict) -> str:
    if not dd30:
        return "<p>无数据</p>"
    rows = ""
    for band, rec in dd30.items():
        cells = ""
        for h in ("20", "60", "120"):
            r = rec.get(h, {})
            ret = r.get("ret", {})
            cells += f"<td>{_cell(ret.get('mean'))}<br><small>中位 {_pct(ret.get('median'))} · n={ret.get('n', 0)}（非重叠 {r.get('n_non_overlap', '—')}）</small></td>"
        rows += f"<tr><td><b>DD30 {band}</b><br><small>n={rec.get('n', 0)} 事件</small></td>{cells}</tr>"
    return f"<table><thead><tr><th>入场时 30 日回撤区间</th><th colspan='2'>20D</th><th colspan='2'>60D</th><th colspan='2'>120D</th></tr></thead><tbody>{rows}</tbody></table>"


def render_report(payload: dict, out_path: Path | None = None) -> Path:
    out_path = out_path or (STUDY_DIR / "study1_price_bottom.html")
    summary = payload["summary_equity"] or payload["summary"]
    by_type = payload["by_type"]
    universe = payload["universe"]

    # 一句话结论：MA20 相对低位基线是否改善（核心口径=剔除货币/债券）
    low = summary.get("PRICE_LOW", {})
    ma20 = summary.get("MA20_RECOVERY", {})
    low120 = low.get("120", {}).get("ret", {})
    ma20120 = ma20.get("120", {}).get("ret", {})
    low_mean, ma20_mean = low120.get("mean"), ma20120.get("mean")
    headline = _build_headline(low_mean, ma20_mean, low120, ma20120)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Study 1 Price Bottom · ETF 长期底部赔率</title>
<style>{_CSS}</style></head><body>
<div class="wrap">
<header>
  <h1>Study 1 · Price Bottom — ETF 长期价格底部赔率</h1>
  <div class="meta">729 只 FULL ETF（历史≥756 交易日）· P756 价格分位 → MA20/MA60 恢复 · 前向 20/60/120 交易日 · 全离线 · {payload.get('generated_at','')}</div>
</header>

<div class="summary">
  <h3>一句话结论</h3>
  {headline}
</div>

<h2>一、四组核心比较</h2>
<div class="card">{_core_table(summary)}</div>
<p class="meta">核心口径：剔除货币/债券（价格近零波动，P756 分位无意义）。均值为主，括号小字为对应统计；MAE=窗口内最低 close 相对事件日收益（负值），MFE=最高 close 收益。n 为事件数（合并连续低位后）。</p>

<h2>二、辅助分布</h2>
<div class="card">{_aux_table(summary)}</div>

<h2>二·b、DD30 深跌分档（又低又深跌是更好还是更危险）</h2>
<div class="card">{_dd30_table(payload.get('dd30_sensitivity') or {})}
<p class="meta">⚠ 时间代表性警示：DD30≤-20% 的深跌事件 93% 集中在 2025-2026（依赖 756 日完整窗口 + 深跌环境），
其上「120D +21.3% / 胜率 75%」的结论 <b>不代表跨周期稳定</b>，仅为近两年结构性观察，不可外推。</p>
</div>

<h2>三、按 ETF 类型分层</h2>
<div class="card">{_type_table(by_type) if by_type else '<p>无分层数据</p>'}</div>

<h2>四、Taxonomy 审计（旁路，不影响主样本）</h2>
<div class="card">
<p>生产 classifier 宽基先于行业的顺序会把「创业板新能源」类产品划入宽基。本研究用 research-only 校准（行业优先），
冲突数 <b>{payload['taxonomy_audit'].get('n_conflicts')}</b> / {payload['taxonomy_audit'].get('total')}（{_pct(payload['taxonomy_audit'].get('conflict_rate'),1,False)}），不影响 Price Bottom 结论，仅用于类型异质性。</p>
</div>

<h2>五、口径与限定</h2>
<div class="card">
<ul>
<li>Universe：{universe.get('n_full_etf')} 只 FULL ETF，类型分布 {universe.get('type_distribution')}</li>
<li>价格分位 P756：当前 close 在过去 756 个交易日的百分位（≤20% 为低位）</li>
<li>DD30：当前 close 距 30 日高点回撤（≤-20% 深跌）</li>
<li>复权：东财本地 raw close 直接使用；单日 |ret|≥20% 标记 corporate_action（审计列，不剔除）</li>
<li>基准：同市场横截面中位前向收益（全离线、确定性）</li>
<li>不依赖 mapping / valuation：本研究中不消费 tracking_index，纯价格状态</li>
</ul>
</div>

<footer>AKsignal · Lane 2 Research · Study 1 Price Bottom · {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</footer>
</div></body></html>"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path


def _build_headline(low_mean, ma20_mean, low120, ma20120) -> str:
    parts = []
    if low_mean is not None:
        parts.append(f"价格低位（P756≤20%）入场后 120 日均值 {_pct(low_mean)}、中位 {_pct(low120.get('median'))}、胜率 {_pct(low120.get('win_rate'), 0, False)}")
    if ma20_mean is not None:
        diff = ma20_mean - low_mean if low_mean is not None else None
        parts.append(f"MA20 恢复后 120 日均值 {_pct(ma20_mean)}、胜率 {_pct(ma20120.get('win_rate'), 0, False)}")
        if diff is not None:
            parts.append(f"相对直接底部入场 {'改善 ' + _pct(diff) if diff > 0 else '反而更差 ' + _pct(diff)}")
    if not parts:
        return "<p>样本不足，无法给出可靠结论。</p>"
    return "<p>" + "；".join(parts) + "。</p>"


# ── Study 1B Deep Stress Robustness ──────────────────────────────

def _year_table(years: list[dict]) -> str:
    if not years:
        return "<p>无年份样本</p>"
    rows = ""
    for y in years:
        cells = ""
        for h in ("20", "60", "120"):
            r = y.get(str(h), {})
            cells += f"<td>{_cell(r.get('mean'))}<br><small>胜率 {_pct(r.get('win_rate'), 0, False)} · n={r.get('n', 0)}</small></td>"
        rows += f"<tr><td><b>{y['year']}</b></td><td>{y['n']}</td><td>{y['n_etfs']}</td>{cells}</tr>"
    return f"<table><thead><tr><th>年份</th><th>事件数</th><th>ETF 数</th><th colspan='2'>20D</th><th colspan='2'>60D</th><th colspan='2'>120D</th></tr></thead><tbody>{rows}</tbody></table>"


def _cluster_table(bt: dict) -> str:
    if not bt or bt.get("status") != "ok":
        return f"<p>样本不足（{bt.get('status', 'unknown')}）</p>"
    lo, hi = bt.get("mean_p95_ci", [None, None])
    wlo, whi = bt.get("win_p95_ci", [None, None])
    rows = f"""<table>
<thead><tr><th>指标</th><th>观测值</th><th>Cluster bootstrap 均值</th><th>95% CI</th><th>p（显著）</th></tr></thead>
<tbody>
<tr><td>120D 均值</td><td>{_pct(bt.get('obs_mean'))}</td><td>{_pct(bt.get('mean_mean'))}</td>
<td>[{_pct(lo)}, {_pct(hi)}]</td><td>{_pct(bt.get('mean_p_gt0'), 0, False)}（&gt;0）</td></tr>
<tr><td>120D 胜率</td><td>{_pct(bt.get('obs_win_rate'), 0, False)}</td><td>{_pct(bt.get('win_mean'), 0, False)}</td>
<td>[{_pct(wlo, 0, False)}, {_pct(whi, 0, False)}]</td><td>{_pct(bt.get('win_p_gt0p5'), 0, False)}（&gt;0.5）</td></tr>
</tbody></table>
<p class="meta">Cluster bootstrap：块=单只 ETF 的全部事件（保留组内相关），重抽样单位=ETF，B={bt.get('n_boot')}，n={bt.get('n_events')} 事件 / {bt.get('n_etfs')} ETF。</p>"""
    yb = bt.get("year_cluster_bootstrap") or {}
    if yb:
        ylo, yhi = yb.get("mean_p95_ci", [None, None])
        rows += f"""<p><b>年份块 bootstrap（时间聚类敏感性）</b>：重抽样单位=年份，B={bt.get('n_boot')}，年份 {yb.get('years')} 事件分布 {yb.get('year_event_counts')}。</p>
<table>
<thead><tr><th>指标</th><th>bootstrap 均值</th><th>95% CI</th><th>p</th></tr></thead>
<tbody>
<tr><td>120D 均值</td><td>{_pct(yb.get('mean_mean'))}</td><td>[{_pct(ylo)}, {_pct(yhi)}]</td><td>{_pct(yb.get('mean_p_gt0'), 0, False)}（&gt;0）</td></tr>
<tr><td>120D 胜率</td><td>{_pct(yb.get('win_mean'), 0, False)}</td><td>—</td><td>{_pct(yb.get('win_p_gt0p5'), 0, False)}（&gt;0.5）</td></tr>
</tbody></table>
<p class="meta">若年份块 bootstrap 的均值/胜率 p 明显弱于 ETF 块 bootstrap，说明结论主要由单一年份（2025）撑起。</p>"""
    return rows


def _concentration_table(conc: dict) -> str:
    if not conc:
        return "<p>无数据</p>"
    rows = ""
    for t in conc.get("top_etfs", []):
        rows += f"<tr><td>{t['fund_code']}</td><td>{t.get('fund_name', '')}</td><td>{t['n_events']}</td></tr>"
    return f"""<table>
<thead><tr><th>基金代码</th><th>名称</th><th>事件数</th></tr></thead><tbody>{rows}</tbody></table>
<p class="meta">总事件 {conc.get('total_events')} / {conc.get('n_etfs')} ETF；每 ETF 事件数中位 {conc.get('median_events_per_etf')}、p90 {conc.get('p90_events_per_etf')}、最大 {conc.get('max_events_per_etf')}。</p>"""


def _param_scan_table(scan: dict) -> str:
    scans = scan.get("scan", [])
    if not scans:
        return "<p>无参数扫描数据</p>"
    rows = ""
    for r in scans:
        if r.get("n", 0) == 0:
            rows += f"<tr><td>{r.get('p_low'):g}</td><td>{r.get('dd30'):g}</td><td>0</td><td>—</td><td>—</td><td>—</td><td>—</td></tr>"
            continue
        rows += (
            f"<tr><td>{r.get('p_low'):g}</td><td>{r.get('dd30'):g}</td><td>{r.get('n')}</td>"
            f"<td>{_cell(r.get('20_mean'))}</td><td>{_cell(r.get('60_mean'))}</td>"
            f"<td>{_cell(r.get('120_mean'))}<br><small>胜率 {_pct(r.get('120_win'), 0, False)}</small></td></tr>"
        )
    return f"""<table>
<thead><tr><th>P756 阈值</th><th>DD30 阈值</th><th>n</th><th>20D</th><th>60D</th><th>120D 均值/胜率</th></tr></thead>
<tbody>{rows}</tbody></table>"""


def render_robustness_report(payload: dict, out_path: Path | None = None) -> Path:
    out_path = out_path or (STUDY_DIR / "study1b_deep_stress.html")
    r = payload.get("robustness", {})
    dd30 = r.get("cluster_bootstrap_dd30", {})
    low = r.get("cluster_bootstrap_low", {})

    # 结论评估：是否达到候选策略升级门槛
    passed = _gate_assessment(r)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Study 1B · Deep Stress Robustness</title>
<style>{_CSS}</style></head><body>
<div class="wrap">
<header>
  <h1>Study 1B · Deep Stress Robustness</h1>
  <div class="meta">对 PRICE_LOW×DD30（120D +21.3%）的三重压力测试 · 年份去集 / ETF 重复暴露 / 参数选择效应 · {payload.get('generated_at', '')}</div>
</header>

<div class="summary">
  <h3>升级门槛评估</h3>
  {passed}
</div>

<h2>一、年份去集中（多年份是否为正）</h2>
<h3>PRICE_LOW_DD30（深跌）按年份</h3>
<div class="card">{_year_table(r.get('year_breakdown_dd30'))}</div>
<div class="card">
<h3>排除近期年份后的深跌收益</h3>
{_period_table(r.get('exclude_recent_dd30'))}
</div>
<h3>PRICE_LOW（全部低位）按年份</h3>
<div class="card">{_year_table(r.get('year_breakdown_low'))}</div>

<h2>二、ETF 重复暴露与 cluster 调整显著性</h2>
<h3>PRICE_LOW_DD30 · Cluster bootstrap（120D）</h3>
<div class="card">{_cluster_table(dd30)}</div>
<h3>PRICE_LOW · Cluster bootstrap（120D，参照）</h3>
<div class="card">{_cluster_table(low)}</div>
<h3>深跌事件集中度（哪些 ETF 反复入场）</h3>
<div class="card">{_concentration_table(r.get('concentration_dd30'))}</div>

<h2>三、参数选择效应（P756 × DD30 阈值扫描）</h2>
<div class="card">{_param_scan_table(r.get('parameter_sensitivity'))}</div>

<footer>AKsignal · Lane 2 Research · Study 1B Deep Stress Robustness · {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</footer>
</div></body></html>"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path


def _period_table(excl: dict) -> str:
    if not excl:
        return "<p>无数据</p>"
    rows = ""
    for period, rec in excl.items():
        cells = ""
        for h in ("20", "60", "120"):
            r = rec.get(str(h), {})
            cells += f"<td>{_cell(r.get('mean'))}<br><small>胜率 {_pct(r.get('win_rate'), 0, False)} · n={r.get('n', 0)}</small></td>"
        rows += f"<tr><td><b>{period}</b></td><td>{rec.get('n', 0)}</td><td>{rec.get('n_etfs', 0)}</td>{cells}</tr>"
    return f"<table><thead><tr><th>区间</th><th>事件数</th><th>ETF 数</th><th colspan='2'>20D</th><th colspan='2'>60D</th><th colspan='2'>120D</th></tr></thead><tbody>{rows}</tbody></table>"


def _gate_assessment(r: dict) -> str:
    dd30 = r.get("cluster_bootstrap_dd30", {})
    yb = dd30.get("year_cluster_bootstrap") or {}
    ybd30 = r.get("exclude_recent_dd30", {}).get("<= 2024", {})
    scans = r.get("parameter_sensitivity", {}).get("scan", [])
    base = next((s for s in scans if abs(s.get("p_low", 0) - 20) < 1e-9 and abs(s.get("dd30", 0) + 0.20) < 1e-9), {})
    deeper = next((s for s in scans if abs(s.get("p_low", 0) - 20) < 1e-9 and abs(s.get("dd30", 0) + 0.25) < 1e-9), {})
    base_120 = base.get("120_mean")
    deeper_120 = deeper.get("120_mean")
    # 单调性：更深门槛（-0.25）不应显著劣化；若 -0.20 是孤峰而 -0.25 暴跌 → 参数选择效应，判失败
    monotone_ok = (base_120 is not None and deeper_120 is not None
                   and deeper_120 >= base_120 * 0.5)

    checks = []
    # 1) 多年份为正
    ybreak = r.get("year_breakdown_dd30", [])
    yrs = [y for y in ybreak if (y.get("120") or {}).get("mean") is not None]
    multi_ok = len(yrs) >= 2 and all((y.get("120") or {}).get("mean", 0) > 0 for y in yrs)
    checks.append(("多年份为正", multi_ok,
                   f"有 120D 的年份 {len(yrs)} 个：{'/'.join(str(y['year']) + '=' + _pct(y['120']['mean']) for y in yrs)}（需≥2 年且全正）"))
    # 2) 去除近期后仍为正
    checks.append(("去除 2025-26 后仍正", (ybd30.get("120", {}) or {}).get("mean", -1) > 0,
                   f"≤2024 深跌 n={ybd30.get('n', 0)}，120D {_pct((ybd30.get('120', {}) or {}).get('mean'))}"))
    # 3) cluster 显著 + 时间聚类稳健（需 ETF 块与年份块均显著）
    etf_p = dd30.get("mean_p_gt0", 0)
    yr_p = yb.get("mean_p_gt0", 0)
    checks.append(("cluster 显著（含时间聚类）", etf_p >= 0.95 and yr_p >= 0.95,
                   f"ETF 块 p={etf_p}；年份块 p={yr_p}（两者均显著才成立）"))
    # 4) 120D > 10%
    checks.append(("120D > 10%", (dd30.get("obs_mean") or 0) > 0.10,
                   f"观测 120D {_pct(dd30.get('obs_mean'))}"))
    # 5) win > 60%
    checks.append(("win > 60%", (dd30.get("obs_win_rate") or 0) > 0.60,
                   f"观测胜率 {_pct(dd30.get('obs_win_rate'), 0, False)}"))
    # 6) 参数单调（DD30 更深不劣化；-0.20 孤峰则判失败）
    checks.append(("参数单调（更深不劣化）", monotone_ok,
                   f"DD30 -0.20→{_pct(base_120)} vs 更深 -0.25→{_pct(deeper_120)}（更深不显著劣化）"))

    score = sum(1 for _, ok, _ in checks if ok)
    if score >= 5:
        verdict = "<p class='flag-ok'><b>通过门槛</b>：Lane 2 升级为值得开始 portfolio simulation 的候选策略。</p>"
    elif score >= 3:
        verdict = "<p class='flag-bad'><b>部分通过</b>：还需补强（见各检查）才值得进入 portfolio simulation。</p>"
    else:
        verdict = "<p class='flag-bad'><b>未通过</b>：结论主要由 2025 单年撑起，暂不升级为候选策略。</p>"
    items = "".join(
        f"<li>{'✅' if ok else '❌'} <b>{name}</b>：{note}</li>" for name, ok, note in checks
    )
    return f"{verdict}<ul>{items}</ul>"
