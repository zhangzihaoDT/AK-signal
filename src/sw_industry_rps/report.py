from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

import pandas as pd


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
.subtitle{font-size:14px;color:var(--zh-muted);margin-bottom:8px}
.meta{font-size:12px;color:var(--zh-muted);margin-bottom:24px}
.section{background:var(--zh-card);border-radius:12px;box-shadow:0 1px 4px rgba(0,0,0,.06);padding:28px 32px;margin-bottom:24px}
.section h2{font-size:18px;font-weight:600;color:var(--zh-blue);margin-bottom:20px;padding-bottom:10px;border-bottom:2px solid var(--zh-light-blue)}
.section h3{font-size:15px;font-weight:600;color:var(--zh-text);margin:18px 0 10px}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin:16px 0}
.metric-card{background:var(--zh-light-blue);border-radius:8px;padding:14px 16px;text-align:center}
.metric-value{font-size:22px;font-weight:700;color:var(--zh-blue)}
.metric-label{font-size:12px;color:var(--zh-muted);margin-top:2px}
table{width:100%;border-collapse:collapse;font-size:13px;margin:12px 0}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--zh-border)}
th{color:var(--zh-muted);font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:.03em}
tr:hover td{background:#F8FAFC}
td.num,th.num{text-align:right}
.tag{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:500}
.tag-strong{background:#E8F5E9;color:#2E7D32}
.tag-observe{background:#FFF3E0;color:#E65100}
.tag-neutral{background:#FFF8E1;color:#F57F17}
.tag-weak{background:#FFEBEE;color:#C62828}
.tag-none{background:#F5F5F5;color:#9E9E9E}
.judgment{background:#F4F9FC;border:1px solid var(--zh-light-blue);border-radius:10px;padding:16px 20px;margin:16px 0;font-size:13px;color:var(--zh-text)}
.judgment b{color:var(--zh-deep-blue)}
.verdict{background:var(--zh-cream);border:1px dashed var(--zh-raccoon-gold);border-radius:8px;padding:14px 18px;margin:12px 0;font-size:13px;color:var(--zh-brown)}
.stats-list{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:10px;margin:8px 0}
.stats-item{border:1px solid var(--zh-border);border-radius:8px;padding:10px 12px;background:var(--zh-card);font-size:12px}
.stats-item .name{font-weight:600;color:var(--zh-deep-blue)}
.stats-item .code{font-family:monospace;color:var(--zh-muted);font-size:11px}
.stats-item .detail{color:var(--zh-muted);font-size:11px;margin-top:3px}
.detail-block{background:#F8FAFC;border:1px solid var(--zh-border);border-radius:8px;padding:10px 12px;margin:8px 0;font-size:12px;color:var(--zh-text)}
.detail-block summary{cursor:pointer;color:var(--zh-blue);font-weight:600}
.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:20px}
@media(max-width:640px){.grid-2{grid-template-columns:1fr}}
.up{color:#2E7D32;font-weight:600}
.down{color:#C62828;font-weight:600}
a.back{color:var(--zh-blue);text-decoration:none;font-size:13px}
a.back:hover{text-decoration:underline}
.footer{margin-top:24px;padding-top:16px;border-top:1px solid var(--zh-border);font-size:11px;color:var(--zh-muted);text-align:center}
.badge{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11px;font-weight:600}
.badge-blue{background:var(--zh-light-blue);color:var(--zh-blue)}
.rotation-table td{font-size:11px;padding:3px 5px;text-align:center;min-width:50px}
.filter-bar{margin:12px 0;display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.filter-bar label{font-size:12px;color:var(--zh-muted)}
.filter-bar select,.filter-bar input{padding:4px 8px;border:1px solid var(--zh-border);border-radius:4px;font-size:12px}
"""


def _pct(v: Any) -> str:
    try:
        val = float(v)
        if pd.isna(val):
            return "—"
        sign = "+" if val >= 0 else ""
        return f"{sign}{val * 100:.2f}%"
    except (TypeError, ValueError):
        return "—"


def _num(v: Any, decimal: int = 2) -> str:
    try:
        val = float(v)
        if pd.isna(val):
            return "—"
        return f"{val:.{decimal}f}"
    except (TypeError, ValueError):
        return "—"


def _tag(text: str, cls: str = "") -> str:
    return f"<span class='tag {cls}'>{escape(str(text))}</span>"


def _rps_color(val: float) -> str:
    if pd.isna(val):
        return ""
    if val >= 90:
        return "background:#174A7C;color:#FFFFFF;font-weight:600"
    if val >= 80:
        return "background:#D79A36;color:#FFFFFF;font-weight:600"
    if val >= 70:
        return "background:#DDEFF8;color:#174A7C"
    return ""


def _rotate_color(val: float) -> str:
    if pd.isna(val):
        return "background:#F3F4F6"
    if val >= 90:
        r, g, b = 21, 74, 124
        intensity = min(255, 180 + int((val - 90) / 10 * 75))
        return f"background:rgb({r},{g},{b});color:#FFFFFF;font-weight:600"
    if val >= 80:
        return f"background:#D79A36;color:#FFFFFF;font-weight:600"
    if val >= 70:
        return f"background:#DDEFF8;color:#174A7C"
    alpha = max(20, int(val / 70 * 60)) if val > 0 else 20
    return f"background:rgba(222,239,248,{alpha/255})"


def _strength_level(rps15: Any) -> str:
    try:
        v = float(rps15)
        if pd.isna(v):
            return "—"
        if v >= 90:
            return "极强"
        if v >= 80:
            return "强势"
        if v >= 70:
            return "观察"
        if v >= 50:
            return "中性"
        return "弱势"
    except (TypeError, ValueError):
        return "—"


def _strength_tag(rps15: Any) -> str:
    try:
        v = float(rps15)
        if pd.isna(v):
            return "<span class='tag tag-none'>—</span>"
        if v >= 90:
            return "<span class='tag tag-strong'>极强</span>"
        if v >= 80:
            return "<span class='tag tag-observe'>强势</span>"
        if v >= 70:
            return "<span class='tag tag-neutral'>观察</span>"
        if v < 50:
            return "<span class='tag tag-weak'>弱势</span>"
        return "<span class='tag tag-neutral'>中性</span>"
    except (TypeError, ValueError):
        return "<span class='tag tag-none'>—</span>"


STATE_TAG_CLS = {
    "强势延续": "tag-strong",
    "加速启动": "tag-observe",
    "高位休整": "tag-neutral",
    "一日脉冲": "tag-neutral",
    "走弱": "tag-weak",
}


def _state_tag(state: Any) -> str:
    cls = STATE_TAG_CLS.get(str(state))
    if cls is None:
        return "<span class='tag tag-none'>—</span>"
    return f"<span class='tag {cls}'>{escape(str(state))}</span>"


# ─────────────────────────────────────────────────────────────
# 第一问：行业轮动往哪里动？（一级方向聚合）
# ─────────────────────────────────────────────────────────────

def _direction_table(rows: list[dict[str, Any]]) -> str:
    """第一问产业方向表：只保留 产业方向/RPS15/Δ5/广度/代表行业 5 列。"""
    if not rows:
        return "<p>无方向数据</p>"
    ths = "".join(f"<th>{h}</th>" for h in [
        "产业方向", "RPS15", "Δ5", "广度", "代表行业",
    ])
    body: list[str] = []
    for r in rows:
        med = _num(r.get("median_rps15"), 1)
        d5 = _num(r.get("median_delta_rps15_5d"), 1)
        act = f"{int(r.get('active_count', 0))}/{r.get('industry_count', 0)}"
        body.append(
            f"<tr><td style='font-weight:600;color:var(--zh-deep-blue)'>{escape(str(r.get('parent_industry','')))}</td>"
            f"<td class='num' style='{_rps_color(r.get('median_rps15'))}'>{med}</td>"
            f"<td class='num'>{d5}</td>"
            f"<td class='num'>{act}</td>"
            f"<td>{escape(str(r.get('representative_industry','')))}</td></tr>"
        )
    return "<table><thead><tr>" + ths + "</tr></thead><tbody>" + "\n".join(body) + "</tbody></table>"


def _direction_summary_sentence(top_rows: list[dict[str, Any]]) -> str:
    """一句话产业方向总结：与下方 Top10 表严格一一对应（按表内位置）。

    核心 = 表前 3 名；正在快速增强 = 表第 4-10 名（RPS15 仍在增强）。
    弱势方向不表述（日报原则：告诉用户值得关注什么，而非完整描述市场）。
    """
    if not top_rows:
        return ""
    core = [str(r.get("parent_industry")) for r in top_rows[:3]]
    rising = [str(r.get("parent_industry")) for r in top_rows[3:10]]
    parts = []
    if core:
        parts.append(f"当前趋势核心集中在 <b>{escape('、'.join(core))}</b>")
    if rising:
        parts.append(f"{escape('、'.join(rising))} 正在快速增强")
    return "；".join(parts) + "。" if parts else ""


def render_q1_direction(snapshot: pd.DataFrame) -> str:
    """① 行业轮动往哪里动：一句话 + Top10 强方向表（精简日报）。

    不展示 Bottom3 / 31 行全表（保留在 parquet）。
    """
    if snapshot is None or snapshot.empty or "parent_industry" not in snapshot.columns:
        return "<p>无产业方向数据（parent_industry 缺失）</p>"

    from . import metrics as _metrics
    rows = _metrics.cross_industry_direction(snapshot)
    if not rows:
        return "<p>无产业方向数据</p>"

    parts: list[str] = []

    strong = [r for r in rows if r.get("direction_state") in ("强势上行", "加速")]
    # 若强势方向不足 10，补齐 RPS15 最高的其他方向
    if len(strong) < 10:
        strong_keys = {r.get("parent_industry") for r in strong}
        rest = [r for r in rows if r.get("parent_industry") not in strong_keys]
        rest = sorted(rest, key=lambda r: r.get("median_rps15") or 0, reverse=True)
        strong = strong + rest
    top_rows = strong[:10]
    parts.append(f"<div class='judgment'>{_direction_summary_sentence(top_rows)}</div>")
    parts.append(_direction_table(top_rows))
    return "\n".join(parts)


# ─────────────────────────────────────────────────────────────
# 第二问：哪些行业形成趋势？（含驱动模式，按轮动状态分组）
# ─────────────────────────────────────────────────────────────

# 趋势阶段（Observation 语义分类，v0.8.3）：
#   A 已形成趋势   RPS15 高且持续（趋势已确立并维持）
#   B 正在启动     RPS5 快速领先而 RPS15 未跟上（刚转强，如元件 RPS5=100/RPS15=2）
#   C 正在退潮     原强势行业降温/跌出（falling_out 或 RPS15 高但 RPS5 明显回落）
# 三分类回答「强是刚开始强，还是已持续很强」；驱动模式回答「龙头拉还是全行业涨」。
_TREND_STAGE_ORDER = ["已形成趋势", "正在启动", "正在退潮"]
_TREND_STAGE_LIMITS = {"已形成趋势": 10, "正在启动": 10, "正在退潮": 8}


def classify_trend_stage(row: Any) -> str:
    """把单行行业分类为趋势阶段（Observation，不改确认 Policy）。

    判定优先级：退潮 → 启动 → 已形成趋势。
      - 退潮：falling_out==1，或 RPS15 高(≥观察80) 但 RPS5 明显回落(<60)（强趋势降温）
      - 启动：RPS5 ≥ 观察(80) 且 RPS15 < 观察(80)（短期领先，趋势未确立）
      - 已形成：RPS15 ≥ 观察(80)（趋势已确立并持续）
    返回三个阶段之一；无有效数据返回 "—"。
    """
    r15 = _to_float(row.get("RPS15"))
    r5 = _to_float(row.get("RPS5"))
    falling = _to_float(row.get("falling_out"))
    if r15 is None:
        return "—"
    obs = 80.0
    # 退潮优先：今日跌出强势区，或趋势仍高但近期轮动已明显走弱
    if falling == 1:
        return "正在退潮"
    if r15 >= obs and r5 is not None and r5 < 60:
        return "正在退潮"
    # 启动：短期动量领先，趋势位置尚未确立
    if r5 is not None and r5 >= obs and r15 < obs:
        return "正在启动"
    # 已形成趋势：RPS15 站稳观察区
    if r15 >= obs:
        return "已形成趋势"
    return "—"


def _to_float(v: Any) -> float | None:
    try:
        x = float(v)
        return x if not pd.isna(x) else None
    except (TypeError, ValueError):
        return None


def _driver_brief(structure_row: dict[str, Any] | None) -> str:
    """驱动模式文案化（展示层）：主报告第二问只展示一行综合语义。

    调用独立展示模块 drive_labels.composite_drive_label，不依赖 confirmation Policy。
    完整双维 + Top3 进详情。
    """
    from . import drive_labels as _dl
    if structure_row is None:
        return "—"
    status = structure_row.get("structure_status", "")
    cs = structure_row.get("contribution_structure", "")
    bs = structure_row.get("breadth_structure", "")
    if status in ("insufficient", "failed", "not_in_scope"):
        return "数据不足/未穿透"
    label = _dl.composite_drive_label(cs, bs)
    if label == _dl._UNKNOWN_LABEL:
        return "驱动信息不足"
    top = structure_row.get("top_contributors", "")
    top_name = top.split(":")[0] if top else ""
    if top_name:
        return f"{escape(top_name)} · {label}"
    return label


def _structure_map(structure_df: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if structure_df is None or structure_df.empty:
        return {}
    m: dict[str, dict[str, Any]] = {}
    for _, r in structure_df.iterrows():
        m[str(r.get("industry_code"))] = r.to_dict()
    return m


def render_q2_trend(
    snapshot: pd.DataFrame,
    structure_df: pd.DataFrame | None = None,
) -> str:
    """② 哪些行业值得关注：一句话 + 单张表（行业/阶段/RPS15/RPS5/驱动）。

    阶段：趋势（RPS15≥80）· 启动（RPS15<80 且 RPS5≥80，按 RPS5 排）· 退潮（高 RPS15 短期明显回落）。
    驱动模式列在 structure 产物缺失时整列隐藏，顶部提示「本期未完成」。
    """
    if snapshot.empty:
        return "<p>无数据</p>"
    df = snapshot.copy()
    if "rotation_state" not in df.columns:
        from . import confirmation as _conf
        df = _conf.add_rotation_state_column(df)
    smap = _structure_map(structure_df)

    df["_stage"] = df.apply(classify_trend_stage, axis=1)
    stg = df[df["_stage"] != "—"].copy()

    # 阶段排序：趋势 → 启动 → 退潮；启动按 RPS5，其余按 RPS15
    stage_rank = {"已形成趋势": 0, "正在启动": 1, "正在退潮": 2}
    stg["_rank"] = stg["_stage"].map(stage_rank)
    stg["_sort_key"] = stg.apply(
        lambda r: r.get("RPS5") if r.get("_stage") == "正在启动" else r.get("RPS15"), axis=1)
    stg = stg.sort_values(["_rank", "_sort_key"], ascending=[True, False])

    # 总量控制：每阶段 Top5（最多 15 行）
    caps = {"已形成趋势": 5, "正在启动": 5, "正在退潮": 5}
    kept = []
    for stage, n in caps.items():
        kept.append(stg[stg["_stage"] == stage].head(n))
    rows = pd.concat(kept) if kept else stg.head(15)

    has_structure = structure_df is not None and not structure_df.empty

    parts: list[str] = []
    parts.append(
        "<p style='font-size:13px;color:var(--zh-muted)'>阶段：趋势=RPS15 站稳观察区且持续 · "
        "启动=短期动量领先但趋势未确立（RPS5 高 / RPS15 未跟上）· 退潮=原强势行业短期回落。</p>"
    )
    if not has_structure:
        parts.append("<p style='font-size:12px;color:var(--zh-muted)'>结构穿透：未完成（Enrichment，可选）</p>")

    # 表头
    head_cols = ["行业", "阶段", "RPS15", "RPS5", "驱动"] if has_structure else ["行业", "阶段", "RPS15", "RPS5"]
    ths = "".join(f"<th class='num'>{h}</th>" if h in ("RPS15", "RPS5", "驱动") else f"<th>{h}</th>"
                  for h in head_cols)
    tbody: list[str] = []
    for _, r in rows.iterrows():
        stage = str(r.get("_stage", ""))
        stage_tag = {
            "已形成趋势": "tag-strong", "正在启动": "tag-observe", "正在退潮": "tag-weak",
        }.get(stage, "tag-none")
        rps15 = _num(r.get("RPS15"), 1)
        rps5 = _num(r.get("RPS5"), 1)
        driver = ""
        if has_structure:
            sd = smap.get(str(r.get("industry_code", "")))
            driver = _driver_brief(sd)
        cells = [
            f"<td style='font-weight:600'>{escape(str(r.get('industry_name','')))}</td>",
            f"<td><span class='tag {stage_tag}'>{escape(stage)}</span></td>",
            f"<td class='num'>{rps15}</td>",
            f"<td class='num'><b>{rps5}</b></td>",
        ]
        if has_structure:
            cells.append(f"<td>{escape(driver)}</td>")
        tbody.append("<tr>" + "".join(cells) + "</tr>")

    parts.append("<table><thead><tr>" + ths + "</tr></thead><tbody>")
    parts.append("\n".join(tbody))
    parts.append("</tbody></table>")
    return "\n".join(parts)


def render_rotation_state_sections(snapshot: pd.DataFrame) -> str:
    """轮动状态分组（供折叠区/附录使用，保留原逻辑）。"""
    if snapshot.empty:
        return "<p>无数据</p>"
    df = snapshot.copy()
    if "rotation_state" not in df.columns:
        from . import confirmation as _conf
        df = _conf.add_rotation_state_column(df)
    parts: list[str] = []
    states = ["强势延续", "加速启动", "高位休整", "一日脉冲", "走弱"]
    for state in states:
        sub = df[df["rotation_state"] == state].sort_values("RPS15", ascending=False)
        if sub.empty:
            continue
        items: list[str] = []
        for _, r in sub.head(10).iterrows():
            name = r.get("industry_name", "")
            code = r.get("industry_code", "")
            detail = (f"RPS15 {_num(r.get('RPS15'), 1)} · RPS5 {_num(r.get('RPS5'), 1)} · "
                      f"RPS1 {_num(r.get('RPS1'), 1)} · Δ5 {_num(r.get('delta_rps15_5d'), 1)}")
            items.append(
                f"<div class='stats-item'><span class='name'>{escape(str(name))}</span> "
                f"<span class='code'>{escape(str(code))}</span>"
                f"<div class='detail'>{escape(detail)}</div></div>")
        parts.append(f"<div><h3>{escape(state)}</h3><div class='stats-list'>")
        parts.extend(items)
        parts.append("</div></div>")
    return "\n".join(parts) if parts else "<p>无显著轮动状态</p>"


def render_rotation_matrix(metrics: pd.DataFrame, rotation_days: int = 20) -> str:
    if metrics.empty:
        return "<p>无数据</p>"
    latest = metrics["trade_date"].max()
    cutoff = latest - pd.Timedelta(days=rotation_days * 2)
    recent = metrics[metrics["trade_date"] >= cutoff].copy()
    latest_rps = recent[recent["trade_date"] == latest][["industry_code", "RPS15"]].dropna()
    latest_rps = latest_rps.sort_values("RPS15", ascending=False)
    top_codes = latest_rps["industry_code"].head(30).tolist()
    pivot = recent[recent["industry_code"].isin(top_codes)].pivot_table(
        index="industry_code", columns="trade_date", values="RPS15", aggfunc="first"
    )
    pivot = pivot.reindex(top_codes)
    date_cols = sorted(pivot.columns, reverse=True)[:rotation_days]
    pivot = pivot[list(reversed(date_cols))]
    date_strs = [str(d.date()) for d in pivot.columns]
    ths = "<th>行业</th>" + "".join(f"<th style='font-size:10px'>{escape(d[5:])}</th>" for d in date_strs)
    rows_html: list[str] = []
    for code in pivot.index:
        tds = [f"<td style='font-weight:600;font-size:11px'>{escape(str(code))}</td>"]
        for val in pivot.loc[code]:
            style = _rotate_color(float(val)) if pd.notna(val) else ""
            txt = f"{val:.0f}" if pd.notna(val) else "—"
            tds.append(f"<td style='{style}'>{txt}</td>")
        rows_html.append("<tr>" + "".join(tds) + "</tr>")
    return "\n".join([
        "<div style='overflow-x:auto'>",
        "<table class='rotation-table'>",
        "<thead><tr>" + ths + "</tr></thead>",
        "<tbody>",
        "\n".join(rows_html),
        "</tbody></table></div>",
    ])


def _change_status(row: pd.Series) -> tuple[str, str]:
    falling_out = row.get("falling_out", 0)
    new_entry = row.get("new_entry", 0)
    strong_streak = row.get("strong_streak", 0)
    accelerating = row.get("accelerating", 0)
    drps = row.get("delta_rps15")
    if falling_out:
        return ("跌出强势区", "falling_out")
    if new_entry and strong_streak:
        return ("持续领先", "strong_streak")
    if new_entry:
        return ("首次进入", "new_entry")
    if accelerating:
        return ("RPS15 快速上升", "accelerating")
    if pd.notna(drps) and float(drps) <= -10:
        return ("RPS15 快速下降", "rapid_fall")
    return ("—", "none")


def render_strength_table(snapshot: pd.DataFrame, rotation_days: int = 20) -> str:
    """全市场 124 行业强度榜（第二问附录/折叠）。"""
    if snapshot.empty:
        return "<p>无数据</p>"
    df = snapshot.copy()
    sort_col = "RPS15" if "RPS15" in df.columns else None
    if sort_col:
        df = df.sort_values(sort_col, ascending=False).reset_index(drop=True)
        df["排名"] = range(1, len(df) + 1)
    cols_display = [
        ("排名", "排名"), ("industry_code", "行业代码"), ("industry_name", "行业名称"),
        ("RPS1", "RPS1"), ("RPS5", "RPS5"), ("RPS10", "RPS10"), ("RPS15", "RPS15"),
        ("delta_rps15_5d", "Δ5RPS15"), ("return_5", "5日涨幅"), ("return_15", "15日涨幅"),
        ("delta_rps15", "ΔRPS15"), ("_strength", "强度层级"), ("_state", "轮动状态"),
        ("_change", "变化状态"), ("short_term_acceleration", "短期动能差"),
    ]
    ths = "".join(f"<th>{h}</th>" for h in [h for _, h in cols_display if _c_in_df(_, df)])
    rows_html: list[str] = []
    for _, r in df.iterrows():
        ch_label, ch_machine = _change_status(r)
        state = r.get("rotation_state", "—")
        data_attr = f" data-status='{ch_machine}'" if ch_machine != "none" else ""
        tds: list[str] = []
        for col, _h in cols_display:
            if not _c_in_df(col, df) and not col.startswith("_"):
                continue
            val = r.get(col)
            if col == "_strength":
                tds.append(f"<td>{_strength_tag(r.get('RPS15'))}</td>"); continue
            if col == "_state":
                tds.append(f"<td>{_state_tag(state)}</td>"); continue
            if col == "_change":
                tds.append(f"<td>{escape(ch_label)}</td>"); continue
            if col == "industry_code":
                tds.append(f"<td style='font-family:monospace'>{escape(str(val) if pd.notna(val) else '')}</td>"); continue
            if col.startswith("RPS"):
                cls = _rps_color(float(val)) if pd.notna(val) else ""
                tds.append(f"<td class='num' style='{cls}'>{_num(val, 1)}</td>"); continue
            if col.startswith("return_"):
                txt = _pct(val); cls = "up" if pd.notna(val) and float(val) >= 0 else "down"
                tds.append(f"<td class='num {cls}'>{txt}</td>"); continue
            if col in ("delta_rps15", "short_term_acceleration", "delta_rps15_5d"):
                cls = "up" if pd.notna(val) and float(val) >= 0 else ""
                tds.append(f"<td class='num {cls}'>{_num(val, 2)}</td>"); continue
            if col == "排名":
                tds.append(f"<td class='num' style='color:var(--zh-muted)'>{int(val)}</td>"); continue
            tds.append(f"<td>{escape(str(val) if pd.notna(val) else '—')}</td>")
        rows_html.append(f"<tr{data_attr}>" + "".join(tds) + "</tr>")
    return "\n".join([
        "<table id='strength-table'>",
        "<thead><tr>" + ths + "</tr></thead><tbody>",
        "\n".join(rows_html), "</tbody></table>",
    ])


def _c_in_df(col: str, df: pd.DataFrame) -> bool:
    return col in df.columns or col.startswith("_")


def render_status_changes(snapshot: pd.DataFrame, drilldown_results: list[dict] | None = None) -> str:
    if snapshot.empty:
        return "<p>无数据</p>"
    drilldown_map: dict[str, dict] = {}
    if drilldown_results:
        for d in drilldown_results:
            drilldown_map[d.get("industry_code", "")] = d
    parts: list[str] = []
    new_entry = snapshot[snapshot.get("new_entry", 0) == 1]
    strong_streak = snapshot[snapshot.get("strong_streak", 0) == 1]
    accelerating = snapshot[snapshot.get("accelerating", 0) == 1]
    falling_out = snapshot[snapshot.get("falling_out", 0) == 1]
    short_up = snapshot[pd.to_numeric(snapshot.get("short_term_acceleration"), errors="coerce") > 10]
    short_down = snapshot[pd.to_numeric(snapshot.get("short_term_acceleration"), errors="coerce") < -10]
    sections = [
        ("今日首次进入强势区", new_entry, "RPS15", False),
        ("连续强势行业", strong_streak, "RPS15", False),
        ("ΔRPS15 上升最快", accelerating, "delta_rps15", False),
        ("今日跌出强势区", falling_out, "RPS15", True),
        ("短期显著走强（RPS5 >> RPS15）", short_up, "short_term_acceleration", False),
        ("短期显著回落（RPS5 << RPS15）", short_down, "short_term_acceleration", True),
    ]
    for title, df_section, sort_col, ascending in sections:
        if df_section.empty:
            continue
        df_sorted = df_section.sort_values(sort_col, ascending=ascending) if sort_col in df_section.columns else df_section
        items: list[str] = []
        for _, r in df_sorted.head(10).iterrows():
            name = r.get("industry_name", "")
            code = r.get("industry_code", "")
            detail_lines = []
            if sort_col == "delta_rps15":
                detail_lines.append(f"ΔRPS15：{_num(r.get('delta_rps15'), 2)}；当前 RPS15：{_num(r.get('RPS15'), 1)}")
            elif sort_col == "short_term_acceleration":
                detail_lines.append(f"短期动能差：{_num(r.get('short_term_acceleration'), 2)}；RPS5/RPS15：{_num(r.get('RPS5'),1)}/{_num(r.get('RPS15'),1)}")
            else:
                detail_lines.append(f"RPS15：{_num(r.get('RPS15'), 1)}")
            if title == "今日首次进入强势区" and code in drilldown_map:
                dd = drilldown_map[code]
                detail_lines.append(f"行业 {dd.get('window', 5)}日涨幅：{dd.get('industry_return_pct', 0):+.2f}%（代理 {dd.get('proxy_return_pct',0):+.2f}%）")
                from . import drive_labels as _dl
                pattern = _dl.composite_drive_label(
                    dd.get("contribution_structure", ""), dd.get("breadth_structure", ""))
                if pattern and pattern != _dl._UNKNOWN_LABEL:
                    detail_lines.append(f"驱动模式：{pattern}")
                for i, c in enumerate(dd.get("top_contributors", [])[:3], 1):
                    sign = "+" if c.get("contribution", 0) >= 0 else ""
                    detail_lines.append(f"  {i}. {escape(str(c.get('name','')))} ({c.get('ret',0):+.2f}%) 贡献{c.get('contribution',0):+.2f}pp")
            items.append(
                f"<div class='stats-item'><span class='name'>{escape(str(name))}</span> "
                f"<span class='code'>{escape(str(code))}</span>"
                + "".join(f"<div class='detail'>{escape(l)}</div>" for l in detail_lines) +
                "</div>"
            )
        if items:
            parts.append(f"<div><h3>{escape(title)}</h3><div class='stats-list'>")
            parts.extend(items)
            parts.append("</div></div>")
    return "\n".join(parts) if parts else "<p>无显著状态变化</p>"


def _render_market_width_cards(snapshot: pd.DataFrame) -> str:
    if snapshot.empty:
        return ""
    s = snapshot.copy()
    total = len(s)
    pct_up = int((pd.to_numeric(s.get("return_15"), errors="coerce") > 0).sum())
    med_ret = float(pd.to_numeric(s.get("return_15"), errors="coerce").median()) or 0
    p10 = float(pd.to_numeric(s.get("return_15"), errors="coerce").quantile(0.10)) or 0
    p90 = float(pd.to_numeric(s.get("return_15"), errors="coerce").quantile(0.90)) or 0
    dispersion = p90 - p10
    new_entries = int((s.get("new_entry", 0) == 1).sum())
    fallen_out = int((s.get("falling_out", 0) == 1).sum())
    strong_streak_count = int((s.get("strong_streak", 0) == 1).sum())
    ratio_str = f"{pct_up / total * 100:.2f}%" if total else "—"
    disp_str = f"{dispersion * 100:.2f} 个百分点" if not pd.isna(dispersion) else "—"
    return "\n".join([
        "<div class='metrics'>",
        f"<div class='metric-card'><div class='metric-value'>{pct_up}<span style='font-size:12px'>/{total}</span></div><div class='metric-label'>15日上涨行业占比</div></div>",
        f"<div class='metric-card'><div class='metric-value'>{_pct(med_ret)}</div><div class='metric-label'>15日收益中位数</div></div>",
        f"<div class='metric-card'><div class='metric-value'>{disp_str}</div><div class='metric-label'>15日收益 P90−P10 分化</div></div>",
        f"<div class='metric-card'><div class='metric-value'>{new_entries}<span style='font-size:12px'>/{fallen_out}</span></div><div class='metric-label'>新进入/跌出 Top10%</div></div>",
        f"<div class='metric-card'><div class='metric-value'>{strong_streak_count}</div><div class='metric-label'>连续强势 ≥3 日</div></div>",
        "</div>",
    ])


def build_html(
    snapshot: pd.DataFrame,
    metrics: pd.DataFrame,
    validator_result: Any,
    report_date: str,
    reports_dir: Path,
    rotation_days: int = 20,
    drilldown_results: list[dict] | None = None,
    provisional_suffix: str = "",
    structure_df: pd.DataFrame | None = None,
    confirmation_df: pd.DataFrame | None = None,
    confirmation_available: bool = False,
    tier_df: pd.DataFrame | None = None,
) -> tuple[Path, Path]:
    suffix = provisional_suffix
    csv_path = reports_dir / f"sw_industry_rps_{report_date}{suffix}.csv"
    html_path = reports_dir / f"sw_industry_rps_{report_date}{suffix}.html"

    snapshot.to_csv(csv_path, index=False, encoding="utf-8-sig")

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = len(snapshot)
    quality = validator_result.status if validator_result else "unknown"
    missing_names = getattr(validator_result, "missing_names", [])

    parts: list[str] = []
    parts.append("<!DOCTYPE html><html lang='zh-CN'><head><meta charset='UTF-8'>")
    parts.append(f"<title>② A股全市场 行业轮动 · {report_date}</title>")
    parts.append(f"<style>{CSS}</style></head><body><div class='container'>")
    parts.append("<h1>② A股全市场 行业轮动</h1>")
    provisional_tag = "（临时数据 · 仅供参考）" if suffix else ""
    parts.append(f"<div class='subtitle'>产业方向 → 趋势行业 → 我的主题支撑{provisional_tag}</div>")
    parts.append(f"<div class='meta'>报告日期 {report_date[:4]}-{report_date[4:6]}-{report_date[6:8]} · 生成 {now_str} · 覆盖 {total} 个二级行业 · 数据状态 {quality}</div>")

    # ── 第一问 ──
    parts.append('<div class="section"><h2>① 行业轮动往哪里动？（产业方向）</h2>')
    parts.append(render_q1_direction(snapshot))
    parts.append('</div>')

    # ── 第二问 ──
    parts.append('<div class="section"><h2>② 哪些行业值得关注？</h2>')
    parts.append(render_q2_trend(snapshot, structure_df))
    parts.append('</div>')

    # ── 第三问 ──
    parts.append('<div class="section"><h2>③ 我的主题获得哪些支撑？</h2>')
    if tier_df is not None or (confirmation_available and confirmation_df is not None):
        from . import confirmation_sections as _cs
        parts.append(_cs.render_theme_support(tier_df, confirmation_df, structure_df))
    else:
        parts.append("<p>主题确认尚未生成——运行 <code>confirm</code> 后可见主题支撑详情。</p>")
    parts.append('</div>')

    if missing_names:
        parts.append(f"<details class='detail-block'><summary>数据质量（{len(missing_names)} 个行业已从当前申万分类剔除）</summary>")
        parts.append(f"<p style='color:var(--zh-muted)'>未纳入行业：{'、'.join(escape(str(n)) for n in missing_names[:10])}"
                     f"{'…… 等' if len(missing_names) > 10 else ''}</p></details>")

    parts.append("<div class='footer'>AKsignal · Layer ② 行业轮动（SW-RPS）· 数据来源 申万宏源/AKShare · 生成自动</div>")
    parts.append("</div></body></html>")

    html_path.write_text("\n".join(parts), encoding="utf-8")
    return csv_path, html_path


def save_latest_html(html_path: Path, reports_dir: Path, latest_name: str = "sw_industry_rps_latest.html") -> Path:
    latest_path = reports_dir / latest_name
    if html_path.exists():
        content = html_path.read_text(encoding="utf-8")
        latest_path.write_text(content, encoding="utf-8")
    return latest_path


def build_report_csv(snapshot: pd.DataFrame) -> pd.DataFrame:
    if snapshot.empty:
        return snapshot
    cols = [
        "industry_code", "industry_name", "RPS1", "RPS5", "RPS10", "RPS15",
        "return_5", "return_10", "return_15",
        "delta_rps15_5d", "delta_rps15", "streak_90", "new_entry", "strong_streak",
        "accelerating", "falling_out", "short_term_acceleration",
        "medium_term_acceleration",
    ]
    available = [c for c in cols if c in snapshot.columns]
    return snapshot[available].sort_values("RPS15", ascending=False).reset_index(drop=True)
