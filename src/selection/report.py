"""
Layer ③ — 投资推荐 HTML 可视化（v0.5.0）

Recommendation Builder 产出结构的只读视图，不承担任何筛选逻辑。
报告按投资决策顺序组织：顶层「今天建议怎么表达」+ 每主题 5 块
（① 今天怎么做 → ② 为什么 → ③ 买什么 → ④ 为什么选它 → ⑤ 观察）。
概览表与全量监控表折叠进可展开附录。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("selection.report")

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
.block{border:1px solid var(--zh-border);border-radius:10px;padding:18px 20px;margin:14px 0}
.block h3{font-size:14px;font-weight:600;color:var(--zh-blue);margin:0 0 10px;padding-bottom:8px;border-bottom:1px solid var(--zh-light-blue)}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin:16px 0}
.metric-card{background:var(--zh-light-blue);border-radius:8px;padding:14px 16px;text-align:center}
.metric-value{font-size:22px;font-weight:700;color:var(--zh-blue)}
.metric-label{font-size:12px;color:var(--zh-muted);margin-top:2px}
table{width:100%;border-collapse:collapse;font-size:13px;margin:12px 0}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--zh-border)}
th{color:var(--zh-muted);font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:.03em}
tr:hover td{background:#F8FAFC}
td.num,th.num{text-align:right}
.tag{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:500;white-space:nowrap}
.tag-confirm{background:#E8F5E9;color:#2E7D32}
.tag-unconfirm{background:#FFEBEE;color:#C62828}
.tag-role{background:#E3F2FD;color:#1565C0}
.tag-watch{background:#FFF3E0;color:#E65100}
.verdict{background:var(--zh-cream);border:1px dashed var(--zh-raccoon-gold);border-radius:8px;padding:16px 20px;margin:12px 0;font-size:14px;color:var(--zh-brown)}
.verdict b{color:var(--zh-deep-blue)}
.insight{background:#F8FAFC;border-left:4px solid var(--zh-cyan);border-radius:6px;padding:14px 18px;margin:12px 0;font-size:13px;color:var(--zh-text)}
hr{border:none;border-top:1px solid var(--zh-border);margin:24px 0}
p{font-size:14px;color:var(--zh-muted);margin-bottom:12px}
.empty{color:var(--zh-muted);font-size:13px;padding:8px 0}
.align-line{font-size:12px;color:var(--zh-muted);margin:0 0 24px}
details{border:1px solid var(--zh-border);border-radius:8px;margin:12px 0;padding:0 16px 4px}
summary{cursor:pointer;font-weight:600;color:var(--zh-blue);padding:12px 0;list-style:none}
summary::before{content:"▸ ";color:var(--zh-muted)}
details[open] summary::before{content:"▾ "}
.asset-chip{display:inline-flex;align-items:center;gap:6px;background:#F8FAFC;border:1px solid var(--zh-border);border-radius:6px;padding:4px 10px;margin:4px 6px 4px 0;font-size:13px}
.asset-chip b{color:var(--zh-deep-blue)}
.asset-chip .why{color:var(--zh-muted);font-size:12px}
.reject{color:#C62828;font-size:12px}
.ok{color:#2E7D32;font-size:12px}
/* 状态变化 Badge：升级绿 / 降级红 */
.badge{display:inline-block;padding:1px 6px;border-radius:4px;font-size:11px;font-weight:600;margin-right:4px}
.badge-up{background:#E8F5E9;color:#2E7D32}
.badge-down{background:#FFEBEE;color:#C62828}
/* 趋势热力色 */
.heat-hot{color:#2E7D32;font-weight:600}
.heat-warm{color:#174A7C}
.heat-cold{color:#9E9E9E}
.heat-risk{color:#C62828}
/* Today's Movers */
.movers{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0 14px}
.mover-chip{background:#F8FAFC;border:1px solid var(--zh-border);border-radius:6px;padding:3px 8px;font-size:12px}
.mover-chip .nm{color:var(--zh-deep-blue);font-weight:600}
.mover-chip .up{color:#2E7D32}
.mover-chip .down{color:#C62828}
td.num.up{color:#2E7D32}
td.num.down{color:#C62828}

"""


def _num(v: Any) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{v}"


def _pct(v: Any) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{float(v) * 100:.0f}%"


def _state_tag(state: str) -> str:
    if state == "RECOMMENDED":
        return "<span class='tag tag-confirm'>推荐</span>"
    if state == "QUALIFIED":
        return "<span class='tag tag-role'>合格</span>"
    return "<span class='tag tag-watch'>观察</span>"


def _action_tag(lvl: str) -> str:
    if lvl == "BUY":
        return "<span class='tag tag-confirm'>买入</span>"
    if lvl == "OBSERVE":
        return "<span class='tag tag-watch'>观察</span>"
    return "<span class='tag tag-unconfirm'>等待</span>"


def _confirm_tag(confirmed: bool, breadth: str = "") -> str:
    if confirmed:
        return f"<span class='tag tag-confirm'>✓ {breadth or '已确认'}</span>"
    return "<span class='tag tag-unconfirm'>✗ 未确认</span>"


def _money(v: Any) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    if f >= 1e8:
        return f"{f / 1e8:.1f}亿"
    if f >= 1e4:
        return f"{f / 1e4:.0f}万"
    return f"{f:.0f}"


def _fmt_liquidity(v: Any) -> str:
    s = _money(v)
    return s if s == "—" else f"{s}"


# 风险 flag → 短码（附录监控表）
RISK_FLAG_SHORT = {
    "跌破MA20": "MA20↓", "跌破MA60": "MA60↓", "跌破MA120": "MA120↓",
    "MACD转弱": "MACD弱", "RS为负": "RS负", "RSI偏热": "RSI热", "RSI偏弱": "RSI弱",
}


def _risk_short(a: dict[str, Any]) -> str:
    """风险/阻塞列：flag 短码拼接（如 MA20↓ · MACD弱 · RS负），无风险则 —。"""
    flags = a.get("risk_flags") or []
    if not flags:
        return "—"
    return " · ".join(RISK_FLAG_SHORT.get(f, f) for f in flags)


def _monitor_conclusion(a: dict[str, Any]) -> str:
    """监控表当前结论四档：推荐 / 合格 / 观察 / 等待。

    推荐：进入今日候选，且无阻塞（state=RECOMMENDED 且风险门控通过）
    合格：趋势达标，但被其他门控或主题状态限制（state=QUALIFIED）
    观察：尚未达标，但走势改善或需要继续跟踪（WATCH 且趋势未明显转弱）
    等待：明显转弱或存在风险阻塞（WATCH 且 C 级 / 剔除观察 / 数据缺失）
    """
    if a.get("selection_status") == "unavailable":
        return "等待"
    state = str(a.get("state", ""))
    if state == "RECOMMENDED":
        return "推荐"
    if state == "QUALIFIED":
        return "合格"
    # WATCH：明显转弱（C 级 / 剔除观察）→ 等待，其余 → 观察
    reason = str(a.get("reason", ""))
    trend = str(a.get("trend_status", ""))
    if reason == "剔除观察" or trend == "C":
        return "等待"
    return "观察"


# watch_level 有序序位（用于状态变化方向判断）
_LEVEL_RANK = {"S": 4, "A": 3, "B": 2, "C": 1}


def _heat_class(a: dict[str, Any]) -> str:
    """趋势热力色：S/A + 高分 → 热（绿）；B → 暖（蓝）；C/低分 → 冷（灰）/风险（红）。"""
    score = a.get("score_trend")
    lvl = str(a.get("trend_status", ""))
    if lvl == "S" or (lvl == "A" and (score is None or score >= 70)):
        return "heat-hot"
    if lvl == "A" or (score is not None and score >= 70):
        return "heat-hot"
    if lvl == "B":
        return "heat-warm"
    if lvl == "C":
        return "heat-risk" if (a.get("risk_flags") or a.get("risk_gate_passed") is False) else "heat-cold"
    return "heat-cold"


def _state_change_badge(a: dict[str, Any]) -> str:
    """状态变化 Badge：升级（C→B/A→S）绿 ↑，降级（A→B）红 ↓。"""
    chg = str(a.get("state_change", "") or "")
    if not chg or "→" not in chg:
        return ""
    try:
        src, dst = chg.split("→", 1)
        src_r = _LEVEL_RANK.get(src.strip(), 0)
        dst_r = _LEVEL_RANK.get(dst.strip(), 0)
    except ValueError:
        return ""
    if dst_r > src_r:
        cls, arrow = "badge-up", "↑"
    elif dst_r < src_r:
        cls, arrow = "badge-down", "↓"
    else:
        return f"<span class='badge'>{chg}</span>"
    return f"<span class='badge {cls}'>{chg} {arrow}</span>"


def _change_key(a: dict[str, Any]) -> tuple:
    """变化优先排序键：有状态变化 → 推荐状态 → 日变化幅值（降序）。"""
    has_chg = 0 if (a.get("state_change") or "") else 1
    concl = _monitor_conclusion(a)
    concl_rank = {"推荐": 0, "合格": 1, "观察": 2, "等待": 3}.get(concl, 9)
    sc = a.get("score_change_1d")
    chg_abs = abs(sc) if sc is not None else -1
    return (has_chg, concl_rank, -chg_abs, str(a.get("name", "")))


def _movers_summary(all_wl: list[dict[str, Any]]) -> list[str]:
    """Today's Movers 小摘要：今日状态变化 + 日变化最大的上涨/下跌各 Top3。"""
    out: list[str] = []
    changed = [a for a in all_wl if (a.get("state_change") or "")]
    if changed:
        chips = "".join(
            f"<span class='mover-chip'><span class='nm'>{a.get('name', '')}</span> "
            f"<span>{_state_change_badge(a)}</span></span>"
            for a in changed)
        out.append(f"<div class='insight' style='margin:6px 0'><b>今日状态变化：</b>{chips}</div>")
    movers = [a for a in all_wl if a.get("score_change_1d") is not None]
    up = sorted(movers, key=lambda a: -(a.get("score_change_1d") or 0))[:3]
    down = sorted(movers, key=lambda a: (a.get("score_change_1d") or 0))[:3]
    if up:
        up_chips = "".join(
            f"<span class='mover-chip'><span class='nm'>{a.get('name', '')}</span> "
            f"<span class='up'>{a.get('score_change_1d'):+d}</span></span>"
            for a in up)
        out.append(f"<div class='insight' style='margin:6px 0'><b>今日走强：</b>{up_chips}</div>")
    if down:
        down_chips = "".join(
            f"<span class='mover-chip'><span class='nm'>{a.get('name', '')}</span> "
            f"<span class='down'>{a.get('score_change_1d'):+d}</span></span>"
            for a in down)
        out.append(f"<div class='insight' style='margin:6px 0'><b>今日走弱：</b>{down_chips}</div>")
    return out


def _today_block_html(t: dict[str, Any]) -> list[str]:
    today = t.get("today", {})
    parts: list[str] = []
    confirmed = t.get("confirmed", False)
    expr_label = today.get("expression_label", "")
    action = today.get("action", "WAIT")
    parts.append("<div class='block'><h3>① 今天怎么做</h3>")
    parts.append(
        f"<div class='verdict'><b>行动：</b>{_action_tag(action)} "
        f"｜ <b>表达：</b>{expr_label or '仅观察'} "
        f"｜ <b>确认：</b>{_confirm_tag(confirmed, today.get('confirmation_breadth', ''))}"
        f"<br><small>{today.get('summary', '')}</small></div>")
    parts.append("</div>")
    return parts


def _why_block_html(t: dict[str, Any]) -> list[str]:
    why = t.get("why", {})
    parts: list[str] = ["<div class='block'><h3>② 为什么</h3>"]
    confirmed = t.get("confirmed", False)
    industries = why.get("observing_industries", [])
    if confirmed:
        parts.append("<div class='insight'>"
                     f"<b>行业确认：</b>{_num(why.get('n_observe'))}/{_num(why.get('n_total'))} 个焦点行业进入观察区"
                     f"{'（其中 ' + _num(why.get('n_strong')) + ' 个强势）' if why.get('n_strong') else ''}</div>")
        if industries:
            chips = "".join(
                f"<span class='asset-chip'><b>{i.get('industry', '')}</b>"
                f"<span class='why'>RPS15 {_num(i.get('rps15'))}</span></span>"
                for i in industries)
            parts.append(f"<div>进入观察区的行业：{chips}</div>")
        parts.append('<div class="metrics">')
        for k, v, label, fmt in (
            ("median_participation", why.get("median_participation"), "中位参与率", _pct),
            ("median_hhi", why.get("median_hhi"), "中位 HHI", _num),
            ("median_top3_share", why.get("median_top3_share"), "中位 Top3", _pct),
            ("median_rps15", why.get("median_rps15"), "中位行业 RPS15", _num),
        ):
            parts.append(f"<div class='metric-card'><div class='metric-value'>{fmt(v)}</div><div class='metric-label'>{label}</div></div>")
        parts.append("</div>")
        er = why.get("expression_reason")
        if er:
            parts.append(f"<div class='insight'><b>为什么这样表达：</b>{er}</div>")
    else:
        d_ind = why.get("distance_to_industry_confirm")
        d_etf = why.get("distance_to_etf_strength")
        se = why.get("strongest_etf") or {}
        parts.append(f"<div class='insight'><b>未确认：</b>无焦点行业进入观察区"
                     f"（最强行业 RPS15 {_num(why.get('strongest_industry_rps15'))}，"
                     f"确认门槛由 Layer② 定义）</div>")
        if d_ind is not None:
            parts.append(f"<div class='insight'><b>距离行业确认还差：</b>{_num(d_ind)}</div>")
        if d_etf is not None:
            parts.append(f"<div class='insight'><b>距离 ETF 强势门槛还差：</b>{_num(d_etf)}"
                         f"（ETF 代表 {se.get('name', '—')} RPS15 {_num(se.get('rps15'))}）</div>")
    parts.append("</div>")
    return parts


def _asset_chip(a: dict[str, Any]) -> str:
    name = a.get("name", "")
    code = a.get("code", "")
    metric = a.get("trend_metric_name", "")
    mv = a.get("trend_metric_value")
    extra = ""
    if metric == "rps15" and mv is not None:
        extra = f"RPS15 {_num(mv)} · 成交 {_fmt_liquidity(a.get('liquidity'))}"
    elif a.get("asset_type") == "stock" and a.get("score_trend") is not None:
        extra = f"趋势 {_num(a.get('score_trend'))} · {a.get('trend_status', '')}"
    why_html = f"<span class='why'> · {extra}</span>" if extra else ""
    return (f"<span class='asset-chip'><b>{name}</b> <span class='why'>({code})</span>{why_html}</span>")


def _recommendation_block_html(t: dict[str, Any]) -> list[str]:
    rec = t.get("recommendation", {})
    parts: list[str] = ["<div class='block'><h3>③ 买什么</h3>"]
    etfs = rec.get("etf", [])
    stocks = rec.get("stocks", [])
    if etfs:
        parts.append("<p><b>ETF：</b></p>")
        parts.append("<p>" + "".join(_asset_chip(a) for a in etfs) + "</p>")
    else:
        parts.append("<p class='empty'>— 无推荐 ETF</p>")
    if stocks:
        parts.append("<p><b>个股：</b></p>")
        parts.append("<p>" + "".join(_asset_chip(a) for a in stocks) + "</p>")
    else:
        parts.append("<p class='empty'>— 无推荐个股</p>")
    parts.append("</div>")
    return parts


def _rationale_block_html(t: dict[str, Any]) -> list[str]:
    ra = t.get("rationale", {})
    parts: list[str] = ["<div class='block'><h3>④ 为什么选它</h3>"]
    for group, label in (("etf", "ETF"), ("stocks", "个股")):
        items = ra.get(group, [])
        if not items:
            continue
        parts.append(f"<h4 style='font-size:13px;color:var(--zh-muted);margin:8px 0'>{label}</h4>")
        parts.append("<table><tr><th>名称</th><th class='num'>RPS15</th><th class='num'>成交额</th>"
                     "<th class='num'>5日</th><th class='num'>20日</th><th>说明</th></tr>")
        for it in items:
            if label == "ETF":
                parts.append(
                    f"<tr><td>{it.get('name', '')}</td><td class='num'>{_num(it.get('rps15'))}</td>"
                    f"<td class='num'>{_fmt_liquidity(it.get('liquidity'))}</td>"
                    f"<td class='num'>{_num(it.get('return_5d'))}%</td><td class='num'>{_num(it.get('return_20d'))}%</td>"
                    f"<td>{it.get('reason', '')}</td></tr>")
            else:
                parts.append(
                    f"<tr><td>{it.get('name', '')}</td><td class='num'>{_num(it.get('score_trend'))}</td>"
                    f"<td class='num'>—</td><td class='num'>—</td><td class='num'>—</td>"
                    f"<td>{it.get('reason', '')}</td></tr>")
        parts.append("</table>")
    if not ra.get("etf") and not ra.get("stocks"):
        parts.append("<p class='empty'>—</p>")
    parts.append("</div>")
    return parts


def _watchlist_block_html(t: dict[str, Any]) -> list[str]:
    wl = t.get("watchlist", {})
    parts: list[str] = ["<div class='block'><h3>⑤ 观察</h3>"]
    etfs = wl.get("etf", [])
    stocks = wl.get("stocks", [])
    if etfs:
        parts.append("<p><b>ETF 观察：</b></p>")
        for a in etfs:
            parts.append(
                f"<span class='asset-chip'><b>{a.get('name', '')}</b>"
                f"<span class='why'>({a.get('code', '')} · RPS15 {_num(a.get('rps15'))})</span>"
                f"<span class='reject'>× {a.get('reject_reason', '')}</span></span>")
        parts.append("<p></p>")
    if stocks:
        parts.append("<p><b>个股观察：</b></p>")
        for a in stocks:
            reject = a.get("reject_reason", "")
            parts.append(
                f"<span class='asset-chip'><b>{a.get('name', '')}</b>"
                f"<span class='why'>({a.get('code', '')} · 趋势 {_num(a.get('score_trend'))})</span>"
                f"<span class='reject'>× {reject}</span></span>")
        parts.append("<p></p>")
    if not etfs and not stocks:
        parts.append("<p class='empty'>— 无观察项</p>")
    parts.append("</div>")
    return parts


def _theme_html(t: dict[str, Any]) -> list[str]:
    parts: list[str] = []
    label = t.get("theme_label", t.get("theme", ""))
    confirmed = t.get("confirmed", False)
    stage = t.get("today", {}).get("stage", "")
    parts.append(f"<h3>{label}"
                 f" <span style='font-size:12px;color:var(--zh-muted)'>· {stage}</span>"
                 f" {_confirm_tag(confirmed, t.get('today', {}).get('confirmation_breadth', ''))}</h3>")
    parts += _today_block_html(t)
    parts += _why_block_html(t)
    parts += _recommendation_block_html(t)
    if confirmed:
        parts += _rationale_block_html(t)
    parts += _watchlist_block_html(t)
    return parts


def render_selection_html(
    recommendation: dict[str, Any],
    output_dir: Path,
    date_str: str,
    meta: dict[str, Any] | None = None,
) -> Path:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    html_path = output_dir / f"tradable_candidates_{date_str}.html"
    output_dir.mkdir(parents=True, exist_ok=True)

    parts = [
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='UTF-8'><meta name='viewport' content='width=device-width, initial-scale=1.0'>",
        f"<title>③ 投资建议 · {date_str}</title>",
        f"<style>{CSS}</style></head><body><div class='container'>",
        "<h1>③ 投资建议（多主题）</h1>",
        f"<div class='subtitle'>报告日期 {date_str[:4]}-{date_str[4:6]}-{date_str[6:8]} · 生成于 {now_str} · "
        f"Decision Layer：只消费 Layer①/②（Observation）已落盘的事实，禁止联网/重算 · "
        f"本报告由 Recommendation Builder 按「投资决策顺序」排版，不产生新事实 · "
        f"只回答「买什么」，买多少/何时买卖由 Layer 4 决定</div>",
    ]

    # 数据对齐（单行，运行质量信息，不占决策层）
    alignment = (meta or {}).get("alignment", {})
    layers = (meta or {}).get("layers", {})
    if alignment:
        align_txt = {
            "aligned": "对齐", "stale_industry": "行业滞后", "stale_etf": "ETF 滞后",
            "no_industry": "无行业确认", "no_etf": "无 ETF 数据", "no_data": "无数据",
        }.get(alignment.get("alignment_status", ""), alignment.get("alignment_status", ""))
        lag = alignment.get("industry_lag_days")
        lag_txt = "" if lag in (None, 0) else f"，行业滞后 {lag} 个交易日"
        le = layers.get("etf", {}); ls = layers.get("sw_industry", {})
        parts.append(
            f"<div class='align-line'>信号对齐：<b>{align_txt}</b>{lag_txt} · selection_date={alignment.get('selection_date', '')}"
            f" · Layer① ETF {le.get('trade_date', '—')}/{le.get('data_status', '—')}"
            f" · Layer② 行业 {ls.get('trade_date', '—')}/{ls.get('data_status', '—')}</div>")

    # Selection 输入覆盖
    coverage = (meta or {}).get("coverage", {})
    if coverage:
        degraded = coverage.get("degraded_assets") or []
        cov_txt = (f"Selection coverage：<b>{coverage.get('selection_coverage', '—')}</b>"
                   f"（{coverage.get('selection_coverage_pct', '—')}% · ETF 复用 {coverage.get('etf_reused', '—')}"
                   f" · 个股输入 {coverage.get('stock_inputs_loaded', '—')} · Online fetch {coverage.get('online_fetches', 0)}）")
        if degraded:
            cov_txt += f"<br><small style='color:#C62828'>缺失/不可用 {len(degraded)}：{'、'.join(degraded)}</small>"
        parts.append(f"<div class='align-line'>{cov_txt}</div>")

    # 口径说明
    parts.append(
        "<div class='insight' style='border-left-color:#D79A36'><b>口径说明：</b>"
        "ETF 的 RPS15 是<b>相对全市场 ETF 横截面</b>的百分位（Layer① rotation）；"
        "行业的 RPS15 是<b>相对 124 个申万二级行业横截面</b>的百分位（Layer②）。"
        "两者标尺不同，<b>不可直接对比</b>。主题确认 = 任一焦点行业 RPS15 达 Layer② 观察门槛（存在性判定）；"
        "ETF 候选按 ETF 自身动量（趋势门）+ 主题确认选出，<b>不要求 ETF 对应行业也确认</b>。"
        "<br><b>Fact 与 Policy 边界：</b>本报告展示的 RPS/趋势分是 Layer①/② 的<b>事实</b>（原值保留，不因策略筛选而改变）；"
        "「推荐 / 观察 / 未推荐原因」是 Layer③ 的<b>策略决策</b>。筛选不修正事实本身。</div>")

    # 配置降级提示
    config_issues = (meta or {}).get("config_issues") or {}
    if config_issues:
        issue_parts: list[str] = []
        unregistered = config_issues.get("unregistered_themes") or []
        if unregistered:
            issue_parts.append(f"asset pool 存在未注册 theme（不进入候选，需加入 config/themes_two_directions.yaml）：{'、'.join(unregistered)}")
        cross = config_issues.get("cross_theme_assets") or {}
        if cross:
            shown = "、".join(f"{code}({'/'.join(ths)})" for code, ths in list(cross.items())[:8])
            issue_parts.append(f"跨主题资产（primary 归属 = 首个 bucket）：{shown}")
        if issue_parts:
            parts.append(f"<div class='insight' style='border-left-color:#D79A36'><strong>配置降级：</strong>{' ｜ '.join(issue_parts)}</div>")

    # ── 第一层：今天建议怎么表达（方向级） ─────────────────────────
    action = recommendation.get("action") or {}
    lvl = action.get("level", "WAIT")
    summary = recommendation.get("summary") or {}
    parts.append('<div class="section"><h2>今天建议怎么表达</h2>')
    dir_txt = (f"{action.get('direction_label', '')} · {action.get('theme_label', '')}"
               if action.get("theme_label") else "—")
    parts.append(f"<div class='verdict'><b>今日方向：</b>{_action_tag(lvl)} · {dir_txt}</div>")
    if action.get("expression_label"):
        parts.append(f"<div class='insight'><b>表达方式：</b>{action.get('expression_label', '')}</div>")
    parts.append(f"<p><b>原因：</b>{action.get('summary', '')}</p>")
    closest = recommendation.get("closest_theme")
    if closest:
        se = closest.get("strongest_etf") or {}
        d_ind = closest.get("distance_to_industry_confirm")
        d_etf = closest.get("distance_to_etf_strength")
        parts.append(
            f"<div class='insight'><b>最接近出现行业确认：</b>{closest.get('theme_label', '')}"
            f"（{closest.get('bucket_label', '')}） · 代表 ETF {se.get('name', '—')} · 当前 RPS15 {_num(se.get('rps15'))}"
            f" · 行业确认尚差 {_num(d_ind)} · ETF 强势门槛尚差 {_num(d_etf)}</div>")
    parts.append('<div class="metrics">')
    parts.append(f"<div class='metric-card'><div class='metric-value'>{summary.get('recommended_actions', 0)}</div><div class='metric-label'>推荐行动</div></div>")
    parts.append(f"<div class='metric-card'><div class='metric-value'>{summary.get('qualified_candidates', 0)}</div><div class='metric-label'>合格候选</div></div>")
    parts.append(f"<div class='metric-card'><div class='metric-value'>{summary.get('confirmed_themes', '0/0')}</div><div class='metric-label'>确认主题</div></div>")
    parts.append(f"<div class='metric-card'><div class='metric-value'>{summary.get('strongest_etf_theme', '—')}</div><div class='metric-label'>ETF 表现最强</div></div>")
    parts.append('</div></div>')

    # ── 第二层：逐 Bucket → Theme，5 块布局 ───────────────────────
    buckets = recommendation.get("buckets", [])
    parts.append('<div class="section"><h2>主题投资建议</h2>')
    for b in buckets:
        parts.append(f"<h3 style='color:var(--zh-blue)'>{b.get('bucket_label', '')} · {b.get('objective', '')}</h3>")
        for sub in b.get("themes", []):
            parts += _theme_html(sub)
    parts.append('</div>')

    # ── 附录（折叠）：概览 + 全量监控表 ────────────────────────────
    parts.append('<div class="section"><h2>附录（点击展开）</h2>')

    parts.append("<details><summary>多主题相对状态概览</summary>")
    parts.append("<table><tr><th>Bucket</th><th>主题</th><th>阶段</th><th class='num'>中位RPS15</th><th>最强ETF</th><th class='num'>ETF RPS15</th><th>结论</th></tr>")
    for b in buckets:
        for sub in b.get("themes", []):
            why = sub.get("why", {})
            today = sub.get("today", {})
            stage = today.get("stage", "")
            m = sub.get("monitoring", {})
            se = sub.get("strongest_etf") or {}
            d_ind = why.get("distance_to_industry_confirm")
            confirmed = sub.get("confirmed", False)
            d_txt = "已确认" if confirmed else (_num(d_ind) if d_ind is not None else "—")
            concl = "已确认" if confirmed else ("最接近行业确认" if (closest and sub.get("theme") == closest.get("theme")) else "暂不关注")
            parts.append(
                f"<tr><td><b>{b.get('bucket_label', '')}</b></td><td>{sub.get('theme_label', '')}</td><td>{stage}</td>"
                f"<td class='num'>{_num(why.get('median_rps15'))}</td><td>{se.get('name', '—')}</td>"
                f"<td class='num'>{_num(se.get('rps15'))}</td><td>{d_txt} · {concl}</td></tr>")
    parts.append("</table></details>")

    tier_label = {"LEADER": "龙头", "HIGH_BETA": "高弹性", "UPSTREAM": "上游"}
    parts.append("<details><summary>核心资产监控（固定观察池全量）</summary>")
    parts.append("<div class='empty' style='padding:0 0 12px'>阅读顺序：是谁 → 属于哪条产业链 → 现在强不强 → 今天变好还是变坏 → 为什么没入选 → 当前该怎么看。"
                 "悬停行可看最近趋势达标日期。</div>")

    def _theme_assets(sub: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        wl = sub.get("monitoring", {})
        for tier in ("leaders", "high_beta", "equipment"):
            for a in wl.get(tier, []):
                # 优先展示资产池原始赛道标签（算力芯片/光模块/...），缺省回退 role 聚合标签
                lab = a.get("tier_label") or tier_label.get(a.get("role", ""), tier)
                out.append({**a, "_tier": lab})
        return out

    def _monitor_table_html(assets: list[dict[str, Any]]) -> list[str]:
        """单主题监控表：Today's Movers 摘要 + 变化优先排序的 7 列表。"""
        out: list[str] = []
        if not assets:
            out.append("<div class='empty'>— 无固定观察池标的</div>")
            return out
        out += _movers_summary(assets)
        assets.sort(key=_change_key)
        out.append("<table><tr><th>标的</th><th>赛道</th><th>趋势</th><th class='num'>日变化</th><th>状态轨迹</th><th>风险 / 阻塞</th><th>当前结论</th></tr>")
        for a in assets:
            sc = a.get("score_change_1d")
            sc_txt = "—" if sc is None else f"{sc:+d}"
            sc_cls = " class='up'" if (sc is not None and sc > 0) else (" class='down'" if (sc is not None and sc < 0) else "")
            # 趋势：watch_level · 趋势分（如 A · 75）+ 热力色
            trend_txt = "—"
            heat = _heat_class(a)
            if a.get("score_trend") is not None:
                wl = a.get("trend_status", "")
                trend_txt = (f"{wl} · {_num(a.get('score_trend'))}" if wl and wl != "UNKNOWN"
                             else _num(a.get("score_trend")))
            # 状态轨迹：变化 Badge + 持续天数（如 [C→B ↑] 1d）
            state_chg = a.get("state_change", "") or ""
            days = a.get("days_in_state")
            traj_parts = ""
            if state_chg:
                traj_parts += _state_change_badge(a)
            if days:
                traj_parts += f"{days}d"
            traj_txt = traj_parts if traj_parts else "—"
            # 风险/阻塞：短码（如 MA20↓ · MACD弱 · RS负），无则 —
            risk_txt = _risk_short(a)
            # 当前结论：推荐 / 合格 / 观察 / 等待
            concl = _monitor_conclusion(a)
            # 悬停提示：最近趋势达标 + 完整 reason + 状态
            tip_parts = [f"最近趋势达标 {a.get('last_trend_qualified_date', '—')}",
                         f"reason {a.get('reason', '—')}",
                         f"状态 {a.get('state', '—')} / 数据 {a.get('data_status', '—')}"]
            tip = " ｜ ".join(tip_parts)
            out.append(
                f"<tr title='{tip}'><td>{a.get('name', '')}</td><td>{a.get('_tier', '')}</td>"
                f"<td class='{heat}'>{trend_txt}</td>"
                f"<td class='num{sc_cls}'>{sc_txt}</td>"
                f"<td>{traj_txt}</td>"
                f"<td>{risk_txt}</td>"
                f"<td>{concl}</td></tr>")
        out.append("</table>")
        return out

    for b in buckets:
        for sub in b.get("themes", []):
            theme_assets = _theme_assets(sub)
            if not theme_assets:
                continue
            parts.append(f"<h4 style='color:var(--zh-blue);margin:16px 0 6px'>{sub.get('theme_label', '')}"
                         f" <span style='font-size:12px;color:var(--zh-muted)'>({len(theme_assets)} 只)</span></h4>")
            parts += _monitor_table_html(theme_assets)
    parts.append("</details></div>")

    parts.append(f'<hr><div style="text-align:center;font-size:12px;color:var(--zh-muted);padding:20px 0">AKsignal · Layer ③ 投资建议 · 报告自动生成于 {now_str}</div>')
    parts.append("</div></body></html>")

    html_path.write_text("\n".join(parts), encoding="utf-8")
    logger.info("selection html: %s", html_path)
    return html_path
