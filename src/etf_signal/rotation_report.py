"""
A股全市场 ETF 轮动 — Layer 1 日报（v0.7.0 Market Pulse 市场脉搏）

生成 etf_rotation_{date}.html，回答 ARCHITECTURE.md ① 的核心问题：
「AI、科技、半导体在全部 A 股 ETF 资产中处于什么位置？」

报告结构（v0.7.0）：
  ① Market Pulse  今天市场一句话：热度（Today）/ 趋势（Trend）/ 加速（Velocity）/ 风险（Risk）
  ② 四维观察表    全市场 ETF 横截面：趋势 RPS15 / 今日 RPS1 / 动量 ΔRPS15 / 流动性（仍按 RPS15 排序）
  ③ 板块轮动表    中位 RPS / 5日排名变动 / 强势占比 / 内部离散度 / Top10%/20%
  ④ 主线焦点      AI/科技/半导体 判断块（是否正在成为 A 股主线）
  ⑤ 收益排名       5/10/20 日收益最强与最弱
  ⑥ 发现漏斗摘要   发现链路漏斗 + BUY_CANDIDATE 卡片

RPS1 / ΔRPS15 / 流动性 均为 Observation 展示指标：不参与排序（主表仍按 RPS15 排），
不进入 Selection（Decision 仍只看 RPS15 / TrendState / Amount）。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger("etf_signal.rotation_report")

CSS = """
:root {
  --zh-blue: #174A7C; --zh-deep-blue: #06213D; --zh-cyan: #7ECDEB;
  --zh-light-blue: #DDEFF8; --zh-cream: #FFF9EF; --zh-raccoon-gold: #D79A36;
  --zh-brown: #7A4A24; --zh-text: #1F2D3D; --zh-muted: #6B7C8F;
  --zh-card: #FFFFFF; --zh-border: #E8EDF2;
}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Noto Sans SC",sans-serif;background:var(--zh-cream);color:var(--zh-text);line-height:1.6;padding:40px 24px}
.container{max-width:1000px;margin:0 auto}
h1{font-size:28px;font-weight:600;color:var(--zh-deep-blue);margin-bottom:4px}
.subtitle{font-size:14px;color:var(--zh-muted);margin-bottom:32px}
.section{background:var(--zh-card);border-radius:12px;box-shadow:0 1px 4px rgba(0,0,0,.06);padding:28px 32px;margin-bottom:24px}
.section h2{font-size:18px;font-weight:600;color:var(--zh-blue);margin-bottom:20px;padding-bottom:10px;border-bottom:2px solid var(--zh-light-blue)}
.section h3{font-size:15px;font-weight:600;color:var(--zh-text);margin:18px 0 10px}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:16px 0}
.metric-card{background:var(--zh-light-blue);border-radius:8px;padding:14px 16px;text-align:center}
.metric-value{font-size:24px;font-weight:700;color:var(--zh-blue)}
.metric-label{font-size:12px;color:var(--zh-muted);margin-top:2px}
table{width:100%;border-collapse:collapse;font-size:13px;margin:12px 0}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--zh-border)}
th{color:var(--zh-muted);font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:.03em}
tr:hover td{background:#F8FAFC}
td.num,th.num{text-align:right}
.tag{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:500}
.tag-buy{background:#E8F5E9;color:#2E7D32}
.tag-watch{background:#FFF3E0;color:#E65100}
.tag-strong{background:#E3F2FD;color:#1565C0}
.tag-oos{background:#F5F5F5;color:#9E9E9E}
.tag-complete{background:#E8F5E9;color:#2E7D32}
.tag-incomplete{background:#FFF8E1;color:#F57F17}
.tag-flagged{background:#FFEBEE;color:#C62828}
.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:20px}
@media(max-width:640px){.grid-2{grid-template-columns:1fr}}
.insight{background:#F8FAFC;border-left:4px solid var(--zh-cyan);border-radius:6px;padding:14px 18px;margin:12px 0;font-size:13px;color:var(--zh-text)}
.insight strong{color:var(--zh-blue)}
.verdict{background:var(--zh-cream);border:1px dashed var(--zh-raccoon-gold);border-radius:8px;padding:16px 20px;margin:12px 0;font-size:14px;color:var(--zh-brown)}
.verdict b{color:var(--zh-deep-blue)}
.judgment{background:#F4F9FC;border:1px solid var(--zh-light-blue);border-radius:10px;padding:20px 24px;margin:16px 0;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px;line-height:1.9;color:var(--zh-text)}
.judgment .k{color:var(--zh-muted)}
.judgment .v{font-weight:600;color:var(--zh-deep-blue)}
.funnel-row{display:flex;align-items:center;gap:14px;width:100%;border-radius:8px;padding:12px 20px;margin-bottom:6px}
.funnel-label{font-size:13px;font-weight:500;color:var(--zh-muted);min-width:110px;text-align:right}
.funnel-bar{height:34px;border-radius:6px;display:flex;align-items:center;justify-content:flex-end;padding:0 16px;font-weight:600;font-size:14px;color:#fff}
hr{border:none;border-top:1px solid var(--zh-border);margin:24px 0}
p{font-size:14px;color:var(--zh-muted);margin-bottom:12px}
.up{color:#2E7D32;font-weight:600}
.down{color:#C62828;font-weight:600}
.stars{color:var(--zh-raccoon-gold);letter-spacing:.05em;white-space:nowrap}
.pulse{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:16px 0}
.pulse-item{background:var(--zh-cream);border:1px solid var(--zh-border);border-radius:8px;padding:14px 16px}
.pulse-label{font-size:11px;color:var(--zh-muted);text-transform:uppercase;letter-spacing:.03em}
.pulse-value{font-size:20px;font-weight:700;color:var(--zh-deep-blue);margin-top:4px}
.pulse-sub{font-size:12px;color:var(--zh-muted);margin-top:2px}
.grid-3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:20px;margin:12px 0}
@media(max-width:760px){.grid-3{grid-template-columns:1fr}}
.rank-name{font-size:13px;color:var(--zh-text)}
.rank-val{font-size:12px;font-weight:600;color:var(--zh-muted);text-align:right}
.scroll-table{max-height:480px;overflow:auto;border:1px solid var(--zh-border);border-radius:8px;margin:12px 0}
"""

STATE_LABELS = {
    "BUY_CANDIDATE": ("买入候选", "tag-buy"),
    "STRONG_WATCH": ("强势关注", "tag-strong"),
    "WATCH": ("观察", "tag-watch"),
    "OUT_OF_SCOPE": ("范围外", "tag-oos"),
}


def _num(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return f"{float(v):.1f}"


def _sign(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    v = float(v)
    if v > 0:
        return f'<span class="up">+{v:.0f}</span>'
    if v < 0:
        return f'<span class="down">{v:.0f}</span>'
    return f"{v:.0f}"


def _stars(v: Any) -> str:
    """按 RPS 值给 0-5 星（仅 Observation 展示，非评分）。"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    v = float(v)
    if v >= 80:
        return "★★★★★"
    if v >= 65:
        return "★★★★☆"
    if v >= 50:
        return "★★★☆☆"
    if v >= 35:
        return "★★☆☆☆"
    return "★☆☆☆☆"


def _velocity_arrow(v: Any) -> str:
    """ΔRPS15 方向箭头（仅 Observation 展示）。"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    v = float(v)
    if v >= 10:
        return "↑↑"
    if v > 0:
        return "↑"
    if v <= -10:
        return "↓↓"
    if v < 0:
        return "↓"
    return "→"


def _rank_rows(items: list[dict[str, Any]], signed: bool = False) -> str:
    """渲染一张排行榜表格（Today's Leaders / Trend Leaders / Fast Movers）。"""
    if not items:
        return "<p style='font-size:12px;color:var(--zh-muted)'>暂无数据</p>"
    rows = []
    for i, it in enumerate(items, 1):
        val = it["value"]
        val_html = (f'<span class="up">+{val:.0f}</span>' if signed and val > 0 else
                    f'<span class="down">{val:.0f}</span>' if signed and val < 0 else
                    f"{val:.0f}")
        rows.append(f"<tr><td class='num'>{i}</td><td class='rank-name'>{it['fund_name']}</td>"
                    f"<td class='rank-val'>{val_html}</td></tr>")
    return "<table><tr><th class='num'>#</th><th>名称</th><th class='num'>数值</th></tr>" + "".join(rows) + "</table>"


def render_rotation_report(
    rotation: pd.DataFrame,
    bucket_table: pd.DataFrame,
    market: dict[str, Any],
    focus: dict[str, Any],
    regime: dict[str, Any],
    watchlist: pd.DataFrame,
    cards: list[dict],
    output_dir: Path,
    date_str: str,
    n_indicators: int = 0,
    account_candidates: pd.DataFrame | None = None,
    theme_groups: list[dict[str, Any]] | None = None,
    pulse: dict[str, Any] | None = None,
    leaders: dict[str, list[dict[str, Any]]] | None = None,
    coverage: dict[str, int] | None = None,
) -> Path:
    """生成 etf_rotation_{date}.html（v0.7.0 Market Pulse）。

    pulse / leaders 由 rotation.market_pulse / rotation.leader_lists 产出
    （纯 Observation 展示）；coverage 由 rotation.coverage 产出（数据口径统一命名）；
    缺省时从 rotation 现场计算兜底。
    """
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    html_path = output_dir / f"etf_rotation_{date_str}.html"
    output_dir.mkdir(parents=True, exist_ok=True)

    if pulse is None:
        from src.etf_signal import rotation as rot_mod
        pulse = rot_mod.market_pulse(rotation, regime)
    if leaders is None:
        from src.etf_signal import rotation as rot_mod
        leaders = rot_mod.leader_lists(rotation)
    if coverage is None:
        from src.etf_signal import rotation as rot_mod
        coverage = rot_mod.coverage(rotation, watchlist)

    parts = [
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='UTF-8'><meta name='viewport' content='width=device-width, initial-scale=1.0'>",
        f"<title>① A股全市场 ETF 轮动 · {date_str}</title>",
        f"<style>{CSS}</style></head><body><div class='container'>",
        "<h1>① A股全市场 ETF 轮动 · Market Pulse</h1>",
        f"<div class='subtitle'>报告日期 {date_str[:4]}-{date_str[4:6]}-{date_str[6:8]} · 生成于 {now_str} · 全市场横截面 RPS · 四维观察：趋势 RPS15（主排序）/ 今日 RPS1 / 动量 ΔRPS15 / 流动性</div>",
    ]

    # ══════════════════ SECTION 1: Market Pulse（市场脉搏）══════════════════
    parts.append('<div class="section"><h2>① Market Pulse · 市场脉搏</h2>')
    parts.append("<p>今天市场：强度（Level）在哪里、热度（Today）在哪里、速度（Velocity）在哪里。RPS1 与 ΔRPS15 仅作观察，不参与排序与 Selection。</p>")

    # 市场脉搏卡：Today's Hot / Trend Leader / Acceleration / Risk
    th = pulse.get("today_hot") or {}
    tl = pulse.get("trend_leader") or {}
    ac = pulse.get("acceleration") or {}
    rk = pulse.get("risk") or {}
    parts.append('<div class="pulse">')
    parts.append(
        f"<div class='pulse-item'><div class='pulse-label'>Today's Hot · 今日热点</div>"
        f"<div class='pulse-value'>{th.get('label', '—')} <span class='stars'>{_stars(th.get('median_rps1'))}</span></div>"
        f"<div class='pulse-sub'>主题中位 RPS1 {_num(th.get('median_rps1'))}</div></div>")
    parts.append(
        f"<div class='pulse-item'><div class='pulse-label'>Trend Leader · 趋势龙头</div>"
        f"<div class='pulse-value'>{tl.get('label', '—')} <span class='stars'>{_stars(tl.get('median_rps15'))}</span></div>"
        f"<div class='pulse-sub'>主题中位 RPS15 {_num(tl.get('median_rps15'))}</div></div>")
    accel_arrow = _velocity_arrow(ac.get("median_delta_rps15"))
    accel_val = _num(ac.get("median_delta_rps15"))
    if accel_val == "—":
        accel_html = "—"
    elif accel_arrow in ("↑", "↑↑"):
        accel_html = f'<span class="up">{accel_arrow} {accel_val}</span>'
    elif accel_arrow in ("↓", "↓↓"):
        accel_html = f'<span class="down">{accel_arrow} {accel_val}</span>'
    else:
        accel_html = f"{accel_arrow} {accel_val}"
    parts.append(
        f"<div class='pulse-item'><div class='pulse-label'>Acceleration · 趋势加速</div>"
        f"<div class='pulse-value'>{ac.get('label', '—')} {accel_html}</div>"
        f"<div class='pulse-sub'>主题中位 ΔRPS15</div></div>")
    parts.append(
        f"<div class='pulse-item'><div class='pulse-label'>Risk · 风险</div>"
        f"<div class='pulse-value'>{rk.get('level', '—')}</div>"
        f"<div class='pulse-sub'>{rk.get('preference', '—')} · {regime.get('note', '')}</div></div>")
    parts.append('</div>')

    # 数据口径（P0-3：分母统一命名，百分比一律注明分母）
    parts.append('<div class="metrics">')
    cov_items = [
        (str(coverage.get("master_count", "—")), "master_count · 全量 ETF"),
        (str(coverage.get("price_current_count", "—")), "price_current · 横截面有效"),
        (str(coverage.get("rps_eligible_count", "—")), "rps_eligible · 指标完整"),
        (str(coverage.get("trend_active_count", "—")), "trend_active · 趋势活跃"),
    ]
    for val, lbl in cov_items:
        parts.append(f'<div class="metric-card"><div class="metric-value">{val}</div><div class="metric-label">{lbl}</div></div>')
    parts.append('</div>')
    n_flag = int((rotation["data_quality_flag"].astype(str) != "").sum()) if "data_quality_flag" in rotation.columns else 0
    if n_flag:
        parts.append(f"<p style='font-size:12px'>数据异常标记：{n_flag} 只 ETF 检出单日异常（份额折算/除权/异常行情），原值保留、不参与 RPS 横截面排名（见附录）。</p>")

    # 全市场观察指标卡
    preference = regime.get("preference", "—")
    parts.append('<div class="metrics">')
    metric_items = [
        (_num(market.get("median_rps15")), "RPS15 中位"),
        (_num(market.get("median_rps1")), "RPS1 中位（今日热度）"),
        (f"{_num(market.get('median_delta_rps15'))}", "ΔRPS15 中位（动量）"),
        (f"{market.get('up_ratio_15d', 0) * 100:.0f}%", f"15日上涨占比（分母 {coverage.get('price_current_count', '—')}）"),
        (preference, "市场风险偏好"),
    ]
    for val, lbl in metric_items:
        parts.append(f'<div class="metric-card"><div class="metric-value">{val}</div><div class="metric-label">{lbl}</div></div>')
    parts.append('</div>')

    # 三张排行榜：Today's Leaders / Trend Leaders / Fast Movers
    parts.append("<h3>今日 · 趋势 · 加速 排行榜</h3>")
    parts.append('<div class="grid-3">')
    parts.append("<div><h3 style='font-size:14px'>Today's Leaders · 今日领涨（RPS1）</h3>" + _rank_rows(leaders.get("today", [])) + "</div>")
    parts.append("<div><h3 style='font-size:14px'>Trend Leaders · 趋势龙头（RPS15）</h3>" + _rank_rows(leaders.get("trend", [])) + "</div>")
    parts.append("<div><h3 style='font-size:14px'>Fast Movers · 趋势加速（ΔRPS15）</h3>" + _rank_rows(leaders.get("fast", []), signed=True) + "</div>")
    parts.append('</div>')

    # 横截面强度分位分布（含 v0.7.0 新增观察指标）
    if "rps15" in rotation.columns:
        parts.append("<h3>横截面强度分布</h3>")
        parts.append("<table><tr><th>指标</th><th class='num'>P25</th><th class='num'>中位数</th><th class='num'>P75</th><th class='num'>P90</th><th class='num'>≥90 数量</th></tr>")
        for col, label in [("rps15", "RPS15 · 趋势"), ("rps20", "RPS20"),
                           ("rps60", "RPS60"), ("rps1", "RPS1 · 今日"),
                           ("delta_rps15", "ΔRPS15 · 动量")]:
            if col not in rotation.columns:
                continue
            s = rotation[col].dropna()
            if s.empty:
                continue
            p25, p50, p75, p90 = s.quantile([0.25, 0.50, 0.75, 0.90])
            parts.append(
                f"<tr><td>{label}</td>"
                f"<td class='num'>{p25:.1f}</td><td class='num'>{p50:.1f}</td>"
                f"<td class='num'>{p75:.1f}</td><td class='num'>{p90:.1f}</td>"
                f"<td class='num'>{(s >= 90).sum()}</td></tr>")
        parts.append("</table>")

    # 趋势状态分布
    if not watchlist.empty and "trend_state" in watchlist.columns:
        parts.append("<h3>趋势状态分布</h3>")
        state_counts = watchlist["trend_state"].value_counts()
        parts.append("<table><tr><th>趋势状态</th><th class='num'>数量</th><th class='num'>占比</th></tr>")
        for state, cnt in state_counts.items():
            lbl, tag = STATE_LABELS.get(state, (state, ""))
            tag_html = f'<span class="tag {tag}">{lbl}</span>' if tag else lbl
            pct = cnt / len(watchlist) * 100
            parts.append(f"<tr><td>{tag_html}</td><td class='num'>{cnt}</td><td class='num'>{pct:.1f}%</td></tr>")
        parts.append("</table>")

    parts.append(f"<div class='insight'><strong>市场状态：</strong>{regime.get('note', '—')}</div>")
    parts.append('</div>')

    # ══════════════════ SECTION 2: 四维观察表（v0.7.0）══════════════════
    parts.append('<div class="section"><h2>② 四维观察表 · 全市场 ETF</h2>')
    parts.append("<p>趋势（RPS15，主排序）/ 今日（RPS1）/ 动量（ΔRPS15）/ 流动性（成交额百分位）。仅观察展示，不参与 Selection。</p>")
    cols_needed = [c for c in ("rps15", "rps1", "delta_rps15", "liquidity") if c in rotation.columns]
    view = rotation[rotation["fund_name"].ne("")]
    if "rps15" in rotation.columns:
        view = view.sort_values("rps15", ascending=False, na_position="last")
    if not view.empty and cols_needed:
        parts.append('<div class="scroll-table"><table><tr>')
        parts.append("<th>ETF</th><th class='num'>趋势 · RPS15</th><th class='num'>今日 · RPS1</th>")
        parts.append("<th class='num'>动量 · ΔRPS15</th><th class='num'>流动性</th>")
        parts.append("</tr>")
        for _, r in view.iterrows():
            rps15 = r.get("rps15")
            rps1 = r.get("rps1")
            delta = r.get("delta_rps15")
            liq = r.get("liquidity")
            tech = " · 科技" if r.get("is_tech") else ""
            flagged = bool(r.get("data_quality_flag")) if "data_quality_flag" in view.columns else False
            flag_mark = ' <span style="color:#C62828" title="数据异常：单日折算/除权，不参与 RPS 排名">⚠</span>' if flagged else ""
            delta_html = _sign(delta) if pd.notna(delta) else "—"
            arrow_html = _velocity_arrow(delta)
            delta_cell = "—" if delta_html == "—" else f"{arrow_html} {delta_html}"
            parts.append(
                f"<tr><td>{r['fund_name']}{tech}{flag_mark}</td>"
                f"<td class='num'><span class='stars'>{_stars(rps15)}</span> {_num(rps15)}</td>"
                f"<td class='num'>{_num(rps1)}</td>"
                f"<td class='num'>{delta_cell}</td>"
                f"<td class='num'><span class='stars'>{_stars(liq)}</span></td></tr>")
        parts.append("</table></div>")
        n_flagged = int((view["data_quality_flag"].astype(str) != "").sum()) if "data_quality_flag" in view.columns else 0
        flag_note = f" · {n_flagged} 只数据异常（⚠）不参与 RPS 排名" if n_flagged else ""
        parts.append(f"<p style='font-size:12px'>共 {len(view)} 只 · 按 RPS15 降序（排序规则不变）{flag_note}</p>")
    else:
        parts.append("<p>无四维观察数据。</p>")
    parts.append('</div>')

    # ══════════════════ SECTION 3: 板块轮动表 ══════════════════
    parts.append('<div class="section"><h2>③ 板块轮动表</h2>')
    parts.append("<p>按资产桶聚合的横截面强度。5 日排名变动为正表示该板块过去 5 个交易日排名整体上升。</p>")
    if not bucket_table.empty:
        parts.append("<table><tr>")
        parts.append("<th>资产大类</th><th>板块</th><th class='num'>ETF 数</th>")
        parts.append("<th class='num'>RPS15 中位</th><th class='num'>5日排名变动</th>")
        parts.append("<th class='num'>强势占比(≥80)</th><th class='num'>内部离散(σ)</th>")
        parts.append("<th class='num'>Top10%</th><th class='num'>Top20%</th>")
        parts.append("</tr>")
        for _, r in bucket_table.iterrows():
            sr = r["strong_ratio"]
            sr_html = "—" if pd.isna(sr) else f"{sr * 100:.0f}%"
            parts.append(
                f"<tr><td>{r['asset_class']}</td><td>{r['bucket_label']}</td>"
                f"<td class='num'>{r['etf_count']}</td>"
                f"<td class='num'>{_num(r['median_rps15'])}</td>"
                f"<td class='num'>{_sign(r['rps15_rank_change_5d'])}</td>"
                f"<td class='num'>{sr_html}</td>"
                f"<td class='num'>{_num(r['rps15_std'])}</td>"
                f"<td class='num'>{r['top10_count']}</td>"
                f"<td class='num'>{r['top20_count']}</td></tr>")
        parts.append("</table>")
    else:
        parts.append("<p>无板块聚合数据。</p>")
    parts.append('</div>')

    # ══════════════════ SECTION 4: 主线焦点 ══════════════════
    parts.append('<div class="section"><h2>④ 多主题主线焦点</h2>')
    parts.append("<p>每个配置主题（AI 基础设施 / 高端装备 / 电力 / 运营商 / 公用事业 / 行业轮动）在全市场 ETF 中的位置，由下列数字判断。</p>")
    if theme_groups:
        parts.append("<table><tr>")
        parts.append("<th>Bucket</th><th>主题</th><th class='num'>ETF 数</th>")
        parts.append("<th class='num'>RPS15 中位</th><th class='num'>5日排名变动</th>")
        parts.append("<th class='num'>Top10%</th><th class='num'>Top20%</th><th>这一层判断</th>")
        parts.append("</tr>")
        for tg in theme_groups:
            n = tg.get("etf_count", 0)
            rc = tg.get("rank_change_5d")
            rc_txt = "—" if rc is None else _sign(rc)
            top10, top20 = tg.get("top10", 0), tg.get("top20", 0)
            parts.append(
                f"<tr><td>{tg.get('bucket_label', '')}</td><td>{tg.get('theme_label', '')}</td>"
                f"<td class='num'>{n}</td>"
                f"<td class='num'>{_num(tg.get('median_rps15'))}</td>"
                f"<td class='num'>{rc_txt}</td>"
                f"<td class='num'>{top10}</td><td class='num'>{top20}</td>"
                f"<td>{tg.get('verdict', '—')}</td></tr>")
        parts.append("</table>")
        # 兼容旧焦点块（AI/科技/半导体 汇总）
        n_tech = focus.get("tech_count", 0)
        if n_tech > 0:
            parts.append('<div class="judgment">')
            parts.append(f'全市场 ETF 数量（master_count）：<span class="k">{coverage.get("master_count", 0)}</span><br>')
            parts.append(f'AI/科技/半导体 ETF：<span class="v">{n_tech}</span><br>')
            parts.append(f'板块 RPS15 中位数：<span class="v">{_num(focus.get("median_rps15"))}</span><br>')
            parts.append(f'过去 5 日排名提升：<span class="v">{_sign(focus.get("rank_change_5d"))}</span><br>')
            parts.append('</div>')
            parts.append(f"<div class='verdict'><b>AI/科技/半导体 这一层决定：</b>{focus.get('verdict', '—')}</div>")
    else:
        parts.append("<p>无主题焦点数据。</p>")
    parts.append('</div>')

    # ══════════════════ SECTION 5: 收益排名 ══════════════════
    parts.append('<div class="section"><h2>⑤ 收益排名</h2>')
    if not rotation.empty:
        has_flag = "data_quality_flag" in rotation.columns
        for period, label in [(1, "1 日"), (5, "5 日"), (10, "10 日"), (20, "20 日")]:
            col = f"return_{period}d"
            if col not in rotation.columns:
                continue
            s = rotation.dropna(subset=[col]).copy()
            if s.empty:
                continue
            if has_flag:
                s = s[s["data_quality_flag"].fillna("") == ""]
            if s.empty:
                continue
            top = s.nlargest(8, col)
            bottom = s.nsmallest(8, col)
            parts.append(f"<h3>{label}收益 最强 / 最弱</h3>")
            parts.append('<div class="grid-2">')
            parts.append("<table><tr><th>最强</th><th class='num'>收益</th></tr>")
            for _, r in top.iterrows():
                tech = " · 科技" if r.get("is_tech") else ""
                parts.append(f"<tr><td>{r['fund_name']}{tech}</td><td class='num' style='color:#2E7D32'>+{r[col]:.1f}%</td></tr>")
            parts.append("</table>")
            parts.append("<table><tr><th>最弱</th><th class='num'>收益</th></tr>")
            for _, r in bottom.iterrows():
                tech = " · 科技" if r.get("is_tech") else ""
                parts.append(f"<tr><td>{r['fund_name']}{tech}</td><td class='num' style='color:#C62828'>{r[col]:.1f}%</td></tr>")
            parts.append("</table>")
            parts.append("</div>")
        if has_flag:
            parts.append("<p style='font-size:12px'>榜单已剔除 data_quality_flag 异常的 ETF（原值保留，仅不参与排名展示）。</p>")
    else:
        parts.append("<p>无收益排名数据。</p>")
    parts.append('</div>')

    # ══════════════════ SECTION 6: 发现漏斗摘要 ══════════════════
    parts.append('<div class="section"><h2>⑥ 发现漏斗摘要</h2>')
    # P0-3：漏斗各阶段统一命名（coverage），并明确每级分母（相对 master_count）
    mc = coverage.get("master_count", len(rotation) if not rotation.empty else 0)
    n_tradable = 0
    if account_candidates is not None and not account_candidates.empty and "account_tradable" in account_candidates.columns:
        n_tradable = int((account_candidates["account_tradable"] == True).sum())
    n_cards = len(cards)
    funnel_labels = ["master_count · 全量 ETF", "price_current · 横截面有效",
                     "rps_eligible · 指标完整", "trend_active · 趋势活跃", "candidate_cards · 候选卡片"]
    funnel_counts = [mc, coverage.get("price_current_count", 0),
                     coverage.get("rps_eligible_count", 0), coverage.get("trend_active_count", 0), n_cards]
    funnel_colors = ["#174A7C", "#1E6BA8", "#4A90C4", "#7ECDEB", "#B0DCF5"]
    max_w = funnel_counts[0] or 1
    for i in range(5):
        w = funnel_counts[i] / max_w * 100 if funnel_counts[i] else 0
        denom_txt = f" · {funnel_counts[i]}/{mc} ({w:.0f}%)" if i and mc else ""
        parts.append(
            f'<div class="funnel-row">'
            f'<div class="funnel-label">{funnel_labels[i]}</div>'
            f'<div class="funnel-bar" style="width:{max(w, 1)}%;background:{funnel_colors[i]}">{funnel_counts[i]}{denom_txt}</div>'
            f'</div>')

    # BUY_CANDIDATE 卡片表
    buy_cards = [c for c in cards if c.get("trend", {}).get("trend_state") == "BUY_CANDIDATE"]
    if buy_cards:
        parts.append(f"<h3>买入候选（BUY_CANDIDATE · 国金可交易 · 共 {len(buy_cards)} 只）</h3>")
        parts.append("<table><tr><th>代码</th><th>名称</th><th class='num'>RPS15</th><th class='num'>RPS20</th><th class='num'>RPS60</th><th class='num'>5日收益</th><th>卡片状态</th></tr>")
        for c in buy_cards:
            status = c.get("card_status", "")
            tag_cls = {"complete": "tag-complete", "incomplete": "tag-incomplete", "flagged": "tag-flagged"}.get(status, "")
            base = c.get("base_info", {})
            trend = c.get("trend", {})
            parts.append(
                f"<tr><td>{base.get('code', '')}</td><td>{base.get('name', '')}</td>"
                f"<td class='num'>{trend.get('rps15', 0):.1f}</td>"
                f"<td class='num'>{trend.get('rps20', 0):.1f}</td>"
                f"<td class='num'>{trend.get('rps60', 0):.1f}</td>"
                f"<td class='num'>{trend.get('return_5d', 0):+.1f}%</td>"
                f"<td><span class='tag {tag_cls}'>{status}</span></td></tr>")
        parts.append("</table>")

    parts.append('</div>')

    # ── Footer ──
    parts.append(f'<hr><div style="text-align:center;font-size:12px;color:var(--zh-muted);padding:20px 0">AKsignal · Layer ① A股全市场 ETF 轮动 · 报告自动生成于 {now_str}</div>')
    parts.append("</div></body></html>")

    html_path.write_text("\n".join(parts), encoding="utf-8")
    logger.info("  html: %s", html_path)
    return html_path
