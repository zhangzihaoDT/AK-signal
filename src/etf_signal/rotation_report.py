"""
A股全市场 ETF 轮动 — Layer 1 日报（v0.7.1 三问三答）

生成 etf_rotation_{date}.html。Layer① 只回答三个问题：
  ① 全市场大类资产往哪里动：钱在不同大类资产之间如何流动（跨资产方向）
  ② 当前有哪些趋势活跃 ETF：按方向去重，每个方向一只流动性最好的代表
  ③ 我关注主题的 ETF 表现：配置中固定主题 ETF 池（theme_etf + sub_industry_etf）

主语是 ETF 产品：强度 / 流动性 / 可交易表达。全市场行业脉搏（哪个行业在轮动、
加速、扩散、降温）归 Layer②（见 sw_industry_rps）。数据覆盖 / 异常数量等审计信息
只保留在页脚一行，不进正文。
"""

from __future__ import annotations

import logging
from datetime import datetime
from html import escape
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
.subtitle{font-size:14px;color:var(--zh-muted);margin-bottom:28px}
.section{background:var(--zh-card);border-radius:12px;box-shadow:0 1px 4px rgba(0,0,0,.06);padding:26px 30px;margin-bottom:22px}
.section h2{font-size:18px;font-weight:600;color:var(--zh-blue);margin-bottom:6px;padding-bottom:8px;border-bottom:2px solid var(--zh-light-blue)}
.section h3{font-size:15px;font-weight:600;color:var(--zh-text);margin:18px 0 8px}
.q{font-size:13px;color:var(--zh-muted);margin-bottom:16px}
table{width:100%;border-collapse:collapse;font-size:13px;margin:8px 0 16px}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--zh-border)}
th{color:var(--zh-muted);font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:.03em}
tr:hover td{background:#F8FAFC}
td.num,th.num{text-align:right}
.tag{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:500}
.tag-buy{background:#E8F5E9;color:#2E7D32}
.tag-watch{background:#FFF3E0;color:#E65100}
.tag-strong{background:#E3F2FD;color:#1565C0}
.tag-oos{background:#F0F0F0;color:#9E9E9E}
.tag-lag{background:#FDEBD0;color:#B9770E}
.tag-warn{background:#FDEDEC;color:#C0392B}
.direction-state{font-weight:600;color:var(--zh-blue)}
.up{color:#2E7D32;font-weight:600}
.down{color:#C62828;font-weight:600}
.neg{color:#6B7C8F;font-weight:600}
.brief{background:#F8FAFC;border-left:4px solid var(--zh-cyan);border-radius:6px;padding:12px 16px;margin:12px 0;font-size:13px;color:var(--zh-text)}
.brief b{color:var(--zh-blue)}
.footer{margin-top:24px;padding-top:12px;border-top:1px solid var(--zh-border);font-size:12px;color:var(--zh-muted);display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px}
.answer-note{font-size:12px;color:var(--zh-muted);margin:4px 0 14px}
"""

STATE_LABELS = {
    "BUY_CANDIDATE": ("买入候选", "tag-buy"),
    "STRONG_WATCH": ("强势关注", "tag-strong"),
    "WATCH": ("观察", "tag-watch"),
    "OUT_OF_SCOPE": ("趋势不足", "tag-oos"),
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


def _pct(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return f"{float(v) * 100:.0f}%"


def _state_tag(state: str) -> str:
    lbl, tag = STATE_LABELS.get(state, (state, ""))
    return f'<span class="tag {tag}">{lbl}</span>' if tag else escape(str(state))


# ── ④ 三 Lane 路径视图 ─────────────────────────────────────────
# 展示层翻译（机器枚举保持原样，不重判）
_3LANE_L3_CN = {
    "BOTTOM": "底部",
    "FIRST_EXIT": "刚离底部",
    "TRANSITION_EARLY": "切换中·早期",
    "TRANSITION_ACTIVE": "切换中",
    "TRANSITION_ESTABLISHED": "趋势建立",
    "RETEST": "回到底部",
    "POST_TRANSITION": "已完成",
    "UNRELIABLE": "数据不足",
}
_3LANE_L3_TAG = {
    "BOTTOM": "tag-oos", "FIRST_EXIT": "tag-watch", "TRANSITION_EARLY": "tag-watch",
    "TRANSITION_ACTIVE": "tag-strong", "TRANSITION_ESTABLISHED": "tag-strong",
    "RETEST": "tag-oos", "POST_TRANSITION": "tag-strong", "UNRELIABLE": "tag-oos",
}
_3LANE_L2_CN = {"DEEP_BOTTOM": "深度底部", "RECOVERING_FROM_BOTTOM": "底部修复",
                "RECENT_BOTTOM": "近期底部", "NORMAL": "正常", "UNRELIABLE": "数据不足"}
_3LANE_L1_CN = {"BUY_CANDIDATE": "买入候选", "STRONG_WATCH": "强势关注",
                "WATCH": "观察", "OUT_OF_SCOPE": "趋势不足"}


def _yes_no(v) -> str:
    if v is True:
        return '<span class="up">是</span>'
    if v is False:
        return '<span class="neg">否</span>'
    return "—"


def _three_lane_section(three_lane: pd.DataFrame | None, date_str: str) -> str:
    """④ 三 Lane 路径视图：底部 → 刚离底部 → 切换中 → 趋势建立 → 强势。

    默认只展示「活跃路径」子集（Lane 2 底部 ∪ Lane 3 非 POST/UNRELIABLE ∪
    Lane 1 非 OUT_OF_SCOPE）；全量折叠在 <details> 里。机器枚举原样保留，
    展示层只做翻译，不重新判断。
    """
    if three_lane is None or len(three_lane) == 0:
        return ""
    from src.etf_signal.three_lane import is_active_path

    active = three_lane[three_lane.apply(is_active_path, axis=1)]

    def _row_html(r) -> str:
        l2_ltb = r.get("lane2_long_term_bottom")
        l3 = str(r.get("lane3_transition_state", ""))
        l1 = str(r.get("lane1_trend_state", ""))
        l2_state = str(r.get("lane2_bottom_state", ""))
        age = r.get("lane3_days_since_first_exit")
        age_txt = f"{int(age)}D" if isinstance(age, (int, float)) and age == age else "—"
        l2_target = str(r.get("lane2_target_stage", ""))
        l1_txt = _3LANE_L1_CN.get(l1, l1) if l1 and l1 not in ("", "nan", "None") else "—"
        l3_cn = _3LANE_L3_CN.get(l3, l3) if l3 else "—"
        l3_tag = _3LANE_L3_TAG.get(l3, "")
        name = str(r.get("fund_name", ""))
        code = str(r.get("fund_code", ""))
        return (
            f"<tr><td><b>{escape(code)}</b><br><span style='color:#6B7C8F;font-size:.85em'>{escape(name)}</span></td>"
            f"<td>{_yes_no(l2_ltb)} <span style='color:#6B7C8F;font-size:.85em'>{_3LANE_L2_CN.get(l2_state, l2_state) if l2_state and l2_state not in ('nan','None') else ''}</span></td>"
            f"<td>{l2_target}</td>"
            f"<td><span class='tag {l3_tag}'>{escape(l3_cn)}</span></td>"
            f"<td class='num'>{age_txt}</td>"
            f"<td>{escape(l1_txt)}</td></tr>"
        )

    head = ("<tr><th>ETF</th><th>Lane 2 底部</th><th>Lane 2 target</th>"
            "<th>Lane 3 迁移</th><th class='num'>离开底部天数</th><th>Lane 1 趋势</th></tr>")
    active_rows = "".join(_row_html(r) for _, r in active.iterrows())
    all_rows = "".join(_row_html(r) for _, r in three_lane.iterrows())

    wl_date = three_lane["_watchlist_date"].dropna()
    wl_date_txt = str(wl_date.iloc[0].date()) if len(wl_date) else "—"
    lag_flag = ""
    if len(wl_date) and date_str:
        try:
            target = datetime.strptime(str(date_str), "%Y%m%d").date()
            eff_wl_raw = str(wl_date.iloc[0])
            eff_wl = datetime.strptime(eff_wl_raw[:10], "%Y-%m-%d").date()
            if eff_wl < target:
                lag_days = (target - eff_wl).days
                lag_flag = (f"<span class='tag tag-lag'>Lane 1 滞后 {lag_days} 交易日"
                            f"（watchlist {eff_wl} < trade_date {target}）——交叉分析勿把时间差当状态先后</span>")
            elif eff_wl != target:
                lag_flag = "<span class='tag tag-warn'>Lane 1 日期异常（future/未知）</span>"
        except (ValueError, TypeError):
            pass
    n_total = len(three_lane)
    return (
        f'<div class="section"><h2>④ 三 Lane 路径视图</h2>'
        f"<p class='q'>趴在底部 → 刚离底部 → 切换中 → 趋势建立 → 成为强势资产。"
        f"三条 Lane 各自保留原始语义（Lane 2 底部=raw long_term_bottom；Lane 3 迁移=状态机；"
        f"Lane 1 趋势=watchlist trend_state），本视图只归集展示。"
        f"仅展示活跃路径 {len(active)}/{n_total} 只（底部 ∪ 迁移中 ∪ 趋势活跃）；全量见折叠区与 "
        f"<a href='three_lane_{date_str}.csv'>three_lane_{date_str}.csv</a>。</p>"
        f"<table>{head}{active_rows}</table>"
        f"<details><summary>全部 {n_total} 只 ETF（可审计）</summary>"
        f"<table>{head}{all_rows}</table></details>"
        f"<p class='answer-note'>Lane 1 watchlist 数据日：{wl_date_txt}{lag_flag}</p>"
        f"</div>"
    )


def render_rotation_report(
    rotation: pd.DataFrame,
    output_dir: Path,
    date_str: str,
    master: pd.DataFrame | None = None,
    watchlist: pd.DataFrame | None = None,
    prev_active_codes: set[str] | None = None,
    theme_pool: list[dict[str, Any]] | None = None,
    coverage: dict[str, int] | None = None,
    data_status: str = "",
    three_lane: pd.DataFrame | None = None,
) -> Path:
    """生成 etf_rotation_{date}.html（v0.7.1 三问三答）。

    数据准备（cross_asset_direction / active_etf_representatives / theme pool）都在
    本函数内消费 rotation + master；渲染三个问答区块 + 页脚。
    """
    from src.etf_signal import rotation as rot_mod

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    html_path = output_dir / f"etf_rotation_{date_str}.html"
    output_dir.mkdir(parents=True, exist_ok=True)

    master = master if master is not None else pd.DataFrame()
    watchlist = watchlist if watchlist is not None else pd.DataFrame()
    coverage = coverage or {}
    prev_active = prev_active_codes or set()

    parts = [
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='UTF-8'><meta name='viewport' content='width=device-width, initial-scale=1.0'>",
        f"<title>① A股全市场 ETF轮动 · {date_str}</title>",
        f"<style>{CSS}</style></head><body><div class='container'>",
        "<h1>① A股全市场 ETF轮动</h1>",
        f"<div class='subtitle'>大类资产 → 趋势ETF → 我的主题ETF · 报告日期 {date_str[:4]}-{date_str[4:6]}-{date_str[6:8]} · 生成于 {now_str}</div>",
    ]

    # ══════════════════ ① 全市场大类资产往哪里动 ══════════════════
    parts.append('<div class="section"><h2>① 大类资产往哪里动</h2>')
    parts.append("<p class='q'>资金正在从哪些大类资产流向哪些大类资产（跨资产方向，非申万行业）。</p>")
    cross_rows = rot_mod.cross_asset_direction(rotation)
    if cross_rows:
        parts.append("<table><tr>")
        parts.append("<th>大类资产</th><th class='num'>RPS15 中位</th><th class='num'>5日变化</th>")
        parts.append("<th class='num'>趋势活跃占比</th><th>代表 ETF</th><th>当前方向</th></tr>")
        for r in cross_rows:
            parts.append(
                f"<tr><td><b>{r['direction']}</b></td>"
                f"<td class='num'>{_num(r['median_rps15'])}</td>"
                f"<td class='num'>{_sign(r['change_5d'])}</td>"
                f"<td class='num'>{_pct(r['active_ratio'])}</td>"
                f"<td>{escape(str(r['rep_name']))}</td>"
                f"<td class='direction-state'>{escape(str(r['direction_state']))}</td></tr>")
        parts.append("</table>")
    else:
        parts.append("<p>无跨资产数据。</p>")
    parts.append('</div>')

    # ══════════════════ ② 当前趋势活跃 ETF ══════════════════
    parts.append('<div class="section"><h2>② 趋势活跃 ETF</h2>')
    parts.append("<p class='q'>当前有哪些方向处于趋势活跃；每个方向只保留一只流动性最好的代表 ETF。</p>")
    reps, total_directions = rot_mod.active_etf_representatives(rotation, master, top_n=40)
    active_total = int((rotation["trend_state"].astype(str) != "OUT_OF_SCOPE").sum()) \
        if not rotation.empty and "trend_state" in rotation.columns else 0

    # 新增 / 退出趋势活跃简报
    cur_active = set(rotation[rotation["trend_state"].astype(str) != "OUT_OF_SCOPE"]["fund_code"]) \
        if not rotation.empty and "trend_state" in rotation.columns else set()
    if prev_active:
        added = cur_active - prev_active
        removed = prev_active - cur_active
        name_map = {}
        if not master.empty and "fund_code" in master.columns:
            name_map = dict(zip(master["fund_code"], master["fund_name"]))
        def _names(codes):
            return "、".join(str(name_map.get(c, c)) for c in list(codes)[:6]) or "—"
        parts.append(
            f"<div class='brief'><b>新增趋势活跃：</b>{_names(added)}　"
            f"<b>退出趋势活跃：</b>{_names(removed)}</div>")

    if reps:
        parts.append("<table><tr>")
        parts.append("<th>方向</th><th>代表 ETF</th><th>状态</th>")
        parts.append("<th class='num'>RPS15</th><th class='num'>RPS20</th>")
        parts.append("<th>流动性</th><th class='num'>同类活跃</th></tr>")
        for r in reps:
            parts.append(
                f"<tr><td><b>{escape(str(r['direction']))}</b></td>"
                f"<td>{escape(str(r['fund_name']))}</td>"
                f"<td>{_state_tag(r['trend_state'])}</td>"
                f"<td class='num'>{_num(r['rps15'])}</td>"
                f"<td class='num'>{_num(r['rps20'])}</td>"
                f"<td>{'高' if (r['liquidity'] or 0) >= 80 else '中' if (r['liquidity'] or 0) >= 50 else '低'}</td>"
                f"<td class='num'>{r['same_count']}</td></tr>")
        parts.append("</table>")
        shown = min(len(reps), 40)
        parts.append(
            f"<p class='answer-note'>共 {total_directions} 个活跃方向 · 正文展示按强度/流动性排序的 {shown} 个 · "
            f"全部 {active_total} 只活跃 ETF 见 <a href='watchlist_active_{date_str}.csv'>watchlist_active_{date_str}.csv</a></p>")
    else:
        parts.append("<p>当前无趋势活跃 ETF。</p>")
    parts.append('</div>')

    # ══════════════════ ③ 我关注主题的 ETF ══════════════════
    parts.append('<div class="section"><h2>③ 我的主题 ETF</h2>')
    parts.append("<p class='q'>我长期关心的方向现在处于什么位置，即使它们没有进入全市场前列。仅展示配置中固定主题池（theme_etf + sub_industry_etf）。</p>")
    if theme_pool:
        rot_idx = rotation.set_index("fund_code") if not rotation.empty else pd.DataFrame().set_index("fund_code")
        cur_themes = []
        for tp in theme_pool:
            code = str(tp.get("fund_code", ""))
            row = rot_idx.loc[code] if code in rot_idx.index else None
            cur_themes.append({"tp": tp, "row": row})
        for theme_key in sorted({t["tp"]["theme"] for t in cur_themes}):
            label = {t["tp"]["theme"]: t["tp"]["theme_label"] for t in cur_themes}.get(theme_key, theme_key)
            parts.append(f"<h3>{escape(str(label))}</h3>")
            parts.append("<table><tr>")
            parts.append("<th>ETF</th><th>赛道</th><th class='num'>趋势 RPS15/20/60</th>")
            parts.append("<th>今日/变化</th><th>流动性</th><th>状态</th></tr>")
            theme_rows = [t for t in cur_themes if t["tp"]["theme"] == theme_key]
            for t in theme_rows:
                tp = t["tp"]
                row = t["row"]
                if row is None or row.empty:
                    parts.append(
                        f"<tr><td>{escape(str(tp['name']))}</td><td>{escape(str(tp.get('note', '')))}</td>"
                        f"<td class='num' colspan='4'>无行情</td></tr>")
                    continue
                ts = row.get("trend_state", "")
                rps15, rps20, rps60 = row.get("rps15"), row.get("rps20"), row.get("rps60")
                rps1, delta = row.get("rps1"), row.get("delta_rps15")
                trend_txt = f"{_num(rps15)} / {_num(rps20)} / {_num(rps60)}"
                today_txt = f"RPS1 {_num(rps1)} · Δ{_sign(delta)}"
                liq = row.get("liquidity")
                liq_txt = "高" if pd.notna(liq) and float(liq) >= 80 else "中" if pd.notna(liq) and float(liq) >= 50 else "低"
                parts.append(
                    f"<tr><td>{escape(str(tp['name']))}</td><td>{escape(str(tp.get('note', '')))}</td>"
                    f"<td class='num'>{trend_txt}</td>"
                    f"<td>{today_txt}</td>"
                    f"<td>{liq_txt}</td>"
                    f"<td>{_state_tag(str(ts))}</td></tr>")
            parts.append("</table>")
    else:
        parts.append("<p>未配置主题 ETF 池（config/selection_universe.yaml theme_etf + sub_industry_etf）。</p>")
    parts.append('</div>')

    # ══════════════════ ④ 三 Lane 路径视图 ══════════════════
    parts.append(_three_lane_section(three_lane, date_str))

    # ── 页脚：日期｜数据状态｜异常数量（审计信息不进正文）──
    n_flag = int((rotation["data_quality_flag"].astype(str) != "").sum()) \
        if not rotation.empty and "data_quality_flag" in rotation.columns else 0
    status_txt = data_status or coverage.get("data_status", "")
    parts.append('<div class="footer">')
    parts.append(f"<span>报告日期：{date_str}</span>")
    parts.append(f"<span>数据状态：{escape(str(status_txt) or '—')}</span>")
    parts.append(f"<span>异常 ETF：{n_flag} 只</span>")
    parts.append(f"<span>AKsignal · Layer ① A股全市场 ETF 轮动</span>")
    parts.append("</div>")
    parts.append("</div></body></html>")

    html_path.write_text("\n".join(parts), encoding="utf-8")
    logger.info("  html: %s", html_path)
    return html_path
