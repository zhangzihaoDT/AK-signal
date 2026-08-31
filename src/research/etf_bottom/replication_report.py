"""Study 2D Context Broad-Sample Replication HTML 报告（zihao raccoon 视觉体系）。

结构：
  一句话结论（哪些复现、哪些被证伪）
  样本四元组（n_entries/n_etfs/n_dates/n_non_overlap）
  Layer1 全样本 quintile 表（各特征 × 双 aggregation）
  Layer2 年内 quintile 主判据表
  Layer3 2C vs Broad 方向一致性
  口径与限定
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from . import STUDY_DIR
from .replication import FEATURES

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

_FEATURE_LABEL = {
    "asset_excess_60d": "单资产相对超跌 60D",
    "asset_excess_120d": "单资产相对超跌 120D",
    "price_pos_120": "120D 位置（已修复程度）",
    "dd60": "60D 回撤（深度）",
    "market_ret_60d": "市场 60D 趋势",
    "price_pos_60": "60D 位置",
    "price_pos_360": "360D 位置",
}
_VERDICT = {"REPLICATED": ("flag-ok", "复现"), "PARTIAL": ("flag-warn", "部分"), "INSUFFICIENT": ("flag-warn", "不足")}


def _pct(v, nd=1, sign=True):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{v*100:+.{nd}f}%" if sign else f"{v*100:.{nd}f}%"


def _c(v):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    cls = "pos" if v > 0 else ("neg" if v < 0 else "")
    return f"<span class='{cls}'>{_pct(v)}</span>"


def _layer1_table(layer1: dict, feature: str) -> str:
    ev = layer1[feature]["event_weighted"]
    bal = layer1[feature]["etf_balanced"]
    rows = ""
    for i, q in enumerate(ev):
        b = bal[i] if i < len(bal) else {}
        rows += (
            f"<tr><td><b>{q['quintile']}</b></td>"
            f"<td>{q['n']}</td><td>{_c(q['median_120d'])}</td><td>{_pct(q['win_rate'], 0, False)}</td>"
            f"<td>{_c(q['excess_etf_market'])}</td><td>{_c(q['excess_hs300'])}</td>"
            f"<td>{b.get('n_etfs', '—')}</td><td>{_c(b.get('excess_etf_market'))}</td></tr>"
        )
    return f"""<table>
<thead><tr><th>分位</th><th>n</th><th>120D中位</th><th>胜率</th><th>超额(ETF横截面)</th><th>超额(HS300)</th><th>ETF数</th><th>ETF均衡超额</th></tr></thead>
<tbody>{rows}</tbody></table>"""


def _layer2_table(by_year: list[dict]) -> str:
    if not by_year:
        return "<p>无年份数据</p>"
    rows = ""
    for r in by_year:
        spread = r.get("q1_q5_spread")
        mark = " 主" if r["is_main_year"] else ""
        rows += (
            f"<tr><td><b>{r['year']}</b>{mark}</td>"
            f"<td>{r['n_q1']}/{r['n_q5']}</td>"
            f"<td>{_c(r.get('q1_excess_etf'))}</td><td>{_c(r.get('q5_excess_etf'))}</td>"
            f"<td>{_c(spread)}</td><td>{_c(r.get('q1_q5_spread_hs300'))}</td>"
            f"<td>{_pct(r.get('q1_win'), 0, False)}</td><td>{_pct(r.get('q5_win'), 0, False)}</td></tr>"
        )
    return f"""<table>
<thead><tr><th>年份</th><th>Q1/Q5样本</th><th>Q1超额</th><th>Q5超额</th><th>Q1-Q5 spread</th><th>spread(HS300)</th><th>Q1胜率</th><th>Q5胜率</th></tr></thead>
<tbody>{rows}</tbody></table>"""


def _layer3_table(layer3: list[dict]) -> str:
    rows = ""
    for r in layer3:
        cls, label = _VERDICT.get(r["year_verdict"], ("flag-warn", r["year_verdict"]))
        rows += (
            f"<tr><td><b>{_FEATURE_LABEL.get(r['feature'], r['feature'])}</b></td>"
            f"<td>{r['c2_direction']}</td>"
            f"<td>{_c(r.get('broad_q1_excess'))}</td><td>{_c(r.get('broad_q5_excess'))}</td>"
            f"<td>{_c(r.get('broad_q1_q5_spread'))}</td>"
            f"<td>{r['year_positive_count']}正/{r['year_negative_count']}负/{r['year_total_main']}年</td>"
            f"<td><span class='{cls}'><b>{label}</b></span></td></tr>"
        )
    return f"""<table>
<thead><tr><th>特征</th><th>2C 方向</th><th>Broad Q1</th><th>Broad Q5</th><th>Broad spread</th><th>年内方向</th><th>Verdict</th></tr></thead>
<tbody>{rows}</tbody></table>"""


def render_replication(payload: dict, out_path: Path | None = None) -> Path:
    out_path = out_path or (STUDY_DIR / "context_replication.html")
    l1, l2, l3 = payload["layer1"], payload["layer2"], payload["layer3"]

    # 核心结论
    pos120 = next((r for r in l3 if r["feature"] == "price_pos_120"), {})
    dd60 = next((r for r in l3 if r["feature"] == "dd60"), {})
    exc = next((r for r in l3 if r["feature"] == "asset_excess_60d"), {})
    headline = (
        f"大样本（{payload['n_entries']} entry）复现结果：<b>已修复（price_pos_120）方向在 5 个主年份全部非负 → 复现最稳</b>；"
        f"单资产相对超跌（asset_excess_60d）全样本单调（Q1 {_pct(exc.get('broad_q1_excess'))} vs Q5 {_pct(exc.get('broad_q5_excess'))}）"
        f"但年内 2021-2023 反向 → 是反弹年效应，非独立 alpha；"
        f"深度（dd60）Pooled 显示越深越好（Q1 {_pct(dd60.get('broad_q1_excess'))} vs Q5 {_pct(dd60.get('broad_q5_excess'))}）→ 2C「深度无关」在 2024/2025 反弹年被证伪（深层领涨），2023 及以前不成立。"
    )

    sections_l1 = ""
    for f in FEATURES:
        sections_l1 += f"<h3>{_FEATURE_LABEL.get(f, f)}</h3>{_layer1_table(l1, f)}"
    sections_l2 = ""
    for f in FEATURES:
        sections_l2 += f"<h3>{_FEATURE_LABEL.get(f, f)}</h3>{_layer2_table(l2[f]['by_year'])}"

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Study 2D · Context Broad-Sample Replication</title>
<style>{_CSS}</style></head><body>
<div class="wrap">
<header>
  <h1>Study 2D · Context Broad-Sample Replication</h1>
  <div class="meta">2C 的 20 个 episode 发现在 13,855 个长期底部 entry 上的复现检验 · 非 OOS（同段历史）· entry 当日特征 · 双 outcome / 双 aggregation · {payload.get('generated_at', '')}</div>
</header>

<div class="summary">
  <h3>一句话结论</h3>
  <p>{headline}</p>
</div>

<div class="kpis">
  <div class="kpi"><span>Entry 数</span><b>{payload['n_entries']}</b></div>
  <div class="kpi"><span>ETF 数</span><b>{payload['n_etfs']}</b></div>
  <div class="kpi"><span>Entry 日期</span><b>{payload['n_entry_dates']}</b></div>
  <div class="kpi"><span>非重叠(120D)</span><b>{payload['n_non_overlap']['120']}</b></div>
</div>

<h2>一、Layer 1 · 全样本 quintile（pooled relationship）</h2>
<div class="card">{sections_l1}
<p class="meta">绝对五分位跨年合并。primary=超额 vs ETF 横截面中位；secondary=超额 vs HS300。右两列为 ETF-balanced（每 ETF 一票）。</p></div>

<h2>二、Layer 2 · 年内 quintile（主判据，2021-2025 正式年份）</h2>
<div class="card">{sections_l2}
<p class="meta">每年内部独立切五分位；Q1=当年特征最弱组。Q1-Q5 spread >0 = 最弱组后续更好。「主」= 正式判据年份（2021-2025）。2026 仅 28 个成熟 120D entry，作为早期观察不参与判据。</p></div>

<h2>三、Layer 3 · 2C vs Broad 方向一致性</h2>
<div class="card">{_layer3_table(l3)}
<p class="meta">REPLICATED = 多数主年份方向一致；PARTIAL = 部分年份一致（多为反弹年驱动）。「无区分」也需验证——若大样本显示深度相关，说明 2C 小样本结论被证伪。</p></div>

<h2>四、口径与限定</h2>
<div class="card">
<ul>
<li>样本：长期底部 entry（DEEP+RECOVERING，= long_term_bottom），13855 个有 120D 收益</li>
<li>No look-ahead：全部特征用 entry 当日（asset_excess/price_pos/dd60/market_ret），不用 entry+N</li>
<li>asset_excess = 单 ETF 相对 HS300（非 2C 的产业超额，研究对象不同）</li>
<li>double aggregation：event-weighted + ETF-balanced，避免少数频繁进出 ETF 撑起结果</li>
<li>不做显著性检验（entry 间同产业/同日相关）；结论为描述性分组观察</li>
<li>命名 replication 非 OOS：真正 OOS 需未来数据或留年/留产业严格验证</li>
</ul>
</div>

<footer>AKsignal · Lane 2 Research · Study 2D Context Broad-Sample Replication · {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</footer>
</div></body></html>"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path
