from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

import pandas as pd


CSS = """
body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,'PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;margin:24px;background:#FAFBFC;color:#1F2D3D}
h1{color:#174A7C;margin:0 0 4px 0;font-size:22px}
h2{color:#174A7C;margin:18px 0 10px 0;font-size:17px}
.subtitle{color:#6B7280;font-size:14px;margin:0 0 16px 0}
.meta{color:#6B7280;font-size:12px;margin:4px 0 16px 0}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px;margin:12px 0 18px 0}
.card{border:1px solid #E5EAF0;border-radius:8px;padding:10px 12px;background:#FFFFFF}
.card .k{font-size:11px;color:#6B7280;margin-bottom:4px}
.card .v{font-size:16px;font-weight:600;color:#1F2D3D}
.card .s{font-size:11px;color:#6B7280;margin-top:4px}
.detail-card .v{font-size:13px}
table{border-collapse:collapse;width:100%;font-size:12px;margin:10px 0 18px 0}
th,td{border:1px solid #E5EAF0;padding:5px 7px;text-align:left;white-space:nowrap}
th{background:#DDEFF8;color:#174A7C;font-weight:600}
td{background:#FFFFFF}
td.right{text-align:right}
.tag{display:inline-block;padding:1px 6px;border-radius:999px;font-size:11px;font-weight:600}
.pos{color:#D79A36;font-weight:600}
.neg{color:#6B7280}
.strong{background:#174A7C;color:#FFFFFF}
.observe{background:#D79A36;color:#FFFFFF}
.normal{background:#DDEFF8;color:#174A7C}
.filter-bar{margin:10px 0;display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.filter-bar label{font-size:12px;color:#6B7280}
.filter-bar select,.filter-bar input{padding:4px 8px;border:1px solid #E5EAF0;border-radius:4px;font-size:12px}
.rotation-table td{font-size:11px;padding:3px 5px;text-align:center;min-width:50px}
.section{margin-bottom:20px}
.stats-list{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:8px}
.stats-item{border:1px solid #E5EAF0;border-radius:6px;padding:8px 10px;background:#FFFFFF;font-size:12px}
.stats-item .name{font-weight:600;color:#174A7C}
.stats-item .detail{color:#6B7280;font-size:11px}
.footer{margin-top:24px;padding-top:12px;border-top:1px solid #E5EAF0;font-size:11px;color:#6B7280}
details.quality{margin:8px 0 16px 0;padding:6px 10px;border:1px solid #E5EAF0;border-radius:6px;background:#FFFFFF;font-size:12px}
details.quality summary{cursor:pointer;color:#174A7C;font-weight:600;font-size:13px}
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
            return "<span class='tag normal'>—</span>"
        if v >= 90:
            return "<span class='tag strong'>极强</span>"
        if v >= 80:
            return "<span class='tag observe'>强势</span>"
        if v >= 70:
            return "<span class='tag normal'>观察</span>"
        if v < 50:
            return "<span class='tag' style='background:#ECF0F1;color:#7F8C8D'>弱势</span>"
        return "<span class='tag' style='background:#F8F9FB;color:#6B7280'>中性</span>"
    except (TypeError, ValueError):
        return "<span class='tag normal'>—</span>"


def _change_status(row: pd.Series) -> tuple[str, str]:
    """返回 (中文标签, 机器标签)"""
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
    if snapshot.empty:
        return "<p>无数据</p>"

    df = snapshot.copy()
    sort_col = "RPS15" if "RPS15" in df.columns else None
    if sort_col:
        df = df.sort_values(sort_col, ascending=False).reset_index(drop=True)
        df["排名"] = range(1, len(df) + 1)

    cols_display = [
        ("排名", "排名"),
        ("industry_code", "行业代码"),
        ("industry_name", "行业名称"),
        ("RPS5", "RPS5"),
        ("RPS10", "RPS10"),
        ("RPS15", "RPS15"),
        ("return_5", "5日涨幅"),
        ("return_10", "10日涨幅"),
        ("return_15", "15日涨幅"),
        ("delta_rps15", "ΔRPS15"),
        ("streak_90", "连续天数"),
        ("_strength", "强度层级"),
        ("_change", "变化状态"),
        ("short_term_acceleration", "短期动能差"),
    ]
    visible_cols = [c for c, _ in cols_display if c in df.columns or c.startswith("_")]
    header_cols = [h for c, h in cols_display if c in df.columns or c.startswith("_")]

    ths = "".join(f"<th>{h}</th>" for h in header_cols)
    rows_html: list[str] = []

    for _, r in df.iterrows():
        ch_label, ch_machine = _change_status(r)
        data_attr = f" data-status='{ch_machine}'" if ch_machine != "none" else ""
        tr_attrs = f" style='display:none'" if False else ""
        tds: list[str] = []
        for col, _ in cols_display:
            val = r.get(col)
            if col == "_strength":
                tds.append(f"<td>{_strength_tag(r.get('RPS15'))}</td>")
                continue
            if col == "_change":
                tds.append(f"<td>{escape(ch_label)}</td>")
                continue
            if col == "industry_code":
                tds.append(f"<td style='font-family:monospace'>{escape(str(val) if pd.notna(val) else '' )}</td>")
                continue
            if col.startswith("RPS"):
                cls = _rps_color(float(val)) if pd.notna(val) else ""
                txt = _num(val, 1)
                tds.append(f"<td class='right' style='{cls}'>{txt}</td>")
                continue
            if col.startswith("return_"):
                txt = _pct(val)
                cls = "pos" if pd.notna(val) and float(val) >= 0 else "neg"
                tds.append(f"<td class='right {cls}'>{txt}</td>")
                continue
            if col in ("delta_rps15", "short_term_acceleration"):
                txt = _num(val, 2)
                cls = "pos" if pd.notna(val) and float(val) >= 0 else ""
                tds.append(f"<td class='right {cls}'>{txt}</td>")
                continue
            if col == "streak_90":
                txt = str(int(val)) if pd.notna(val) else "0"
                tds.append(f"<td class='right'>{txt}</td>")
                continue
            if col == "排名":
                tds.append(f"<td class='right' style='color:#6B7280'>{int(val)}</td>")
                continue
            tds.append(f"<td>{escape(str(val) if pd.notna(val) else '—')}</td>")
        rows_html.append(f"<tr{data_attr}>" + "".join(tds) + "</tr>")

    return "\n".join([
        "<table id='strength-table'>",
        "<thead><tr>" + ths + "</tr></thead>",
        "<tbody>",
        "\n".join(rows_html),
        "</tbody></table>",
    ])


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


def render_status_changes(snapshot: pd.DataFrame) -> str:
    if snapshot.empty:
        return "<p>无数据</p>"

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
                detail_lines.append(f"ΔRPS15：{_num(r.get('delta_rps15'), 2)}")
                detail_lines.append(f"当前 RPS15：{_num(r.get('RPS15'), 1)}")
            elif sort_col == "short_term_acceleration":
                detail_lines.append(f"短期动能差：{_num(r.get('short_term_acceleration'), 2)}")
                rps5 = _num(r.get("RPS5"), 1)
                rps15_v = _num(r.get("RPS15"), 1)
                detail_lines.append(f"RPS5 / RPS15：{rps5} / {rps15_v}")
            else:
                detail_lines.append(f"RPS15：{_num(r.get('RPS15'), 1)}")
            items.append(
                f"<div class='stats-item'>"
                f"<span class='name'>{escape(str(name))}</span> "
                f"<span style='font-family:monospace;color:#6B7280'>{escape(str(code))}</span>"
                + "".join(f"<div class='detail'>{escape(l)}</div>" for l in detail_lines) +
                f"</div>"
            )
        if items:
            parts.append(f"<div class='section'><h3>{escape(title)}</h3><div class='stats-list'>")
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
        "<div class='cards'>",
        f"<div class='card detail-card'><div class='k'>15日上涨行业占比</div><div class='v'>{pct_up} / {total}</div><div class='s'>{ratio_str}</div></div>",
        f"<div class='card detail-card'><div class='k'>15日收益中位数</div><div class='v'>{_pct(med_ret)}</div><div class='s'>全行业中间水平</div></div>",
        f"<div class='card detail-card'><div class='k'>15日收益 P90−P10</div><div class='v'>{disp_str}</div><div class='s'>行业分化程度</div></div>",
        f"<div class='card detail-card'><div class='k'>新进入 / 跌出 Top10%</div><div class='v'>{new_entries} / {fallen_out}</div><div class='s'>今日新进 {new_entries}，跌出 {fallen_out}</div></div>",
        f"<div class='card detail-card'><div class='k'>连续强势 ≥3 日</div><div class='v'>{strong_streak_count}</div><div class='s'>趋势持续性</div></div>",
        "</div>",
    ])


def build_html(
    snapshot: pd.DataFrame,
    metrics: pd.DataFrame,
    validator_result: Any,
    report_date: str,
    reports_dir: Path,
    rotation_days: int = 20,
) -> tuple[Path, Path]:
    csv_path = reports_dir / f"sw_industry_rps_{report_date}.csv"
    html_path = reports_dir / f"sw_industry_rps_{report_date}.html"

    snapshot.to_csv(csv_path, index=False, encoding="utf-8-sig")

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = len(snapshot)
    quality = validator_result.status if validator_result else "unknown"
    expected = getattr(validator_result, "total_industries", None) or len(snapshot)
    missing_codes = getattr(validator_result, "missing_codes", [])
    missing_names = getattr(validator_result, "missing_names", [])

    parts: list[str] = []
    parts.append("<!DOCTYPE html><html><head><meta charset='utf-8'>")
    parts.append(f"<title>申万二级行业 RPS 监控 {report_date}</title>")
    parts.append(f"<style>{CSS}</style>")
    parts.append("</head><body>")
    parts.append("<h1>申万二级行业 RPS 监控</h1>")
    parts.append(f"<div class='subtitle'>基于申万二级行业指数的日频相对强度与轮动观察</div>")
    parts.append(f"<div class='meta'>报告日期：{report_date} | 生成时间：{now_str} | 数据质量：{quality}</div>")

    parts.append(_render_market_width_cards(snapshot))

    if missing_names:
        parts.append("<details class='quality'>")
        parts.append(f"<summary>数据覆盖：{total} / {total} 有效行业 — {quality}</summary>")
        parts.append(f"<p style='margin:6px 0 2px 0;color:#6B7280'>{len(missing_names)} 个行业已从当前申万分类中剔除，并非当日采集失败。其历史数据截至 2024-06-17。</p>")
        parts.append(f"<p style='margin:6px 0 2px 0;color:#6B7280'>未纳入行业：</p>")
        parts.append("<p style='margin:2px 0;color:#1F2D3D;font-size:11px'>" + "、".join(escape(str(n)) for n in missing_names[:10]) + "</p>")
        if len(missing_names) > 10:
            parts.append(f"<p style='margin:2px 0;color:#6B7280;font-size:11px'>…… 还有 {len(missing_names) - 10} 个</p>")
        parts.append("</details>")

    parts.append("<div class='filter-bar'>")
    parts.append("<label>搜索行业：<input type='text' id='search' placeholder='代码或名称' oninput='filterTable()'></label>")
    parts.append("<label>RPS15 筛选：<select id='rps-filter' onchange='filterTable()'>"
                 "<option value='all'>全部</option>"
                 "<option value='strong'>≥ 90</option>"
                 "<option value='observe'>≥ 80</option>"
                 "<option value='weak'>< 80</option>"
                 "</select></label>")
    parts.append("<label>变化状态筛选：<select id='status-filter' onchange='filterTable()'>"
                 "<option value='all'>全部</option>"
                 "<option value='falling_out'>跌出强势区</option>"
                 "<option value='new_entry'>首次进入</option>"
                 "<option value='strong_streak'>持续领先</option>"
                 "<option value='accelerating'>RPS15 快速上升</option>"
                 "<option value='rapid_fall'>RPS15 快速下降</option>"
                 "</select></label>")
    parts.append("</div>")

    parts.append("<h2>今日强度榜</h2>")
    parts.append(render_strength_table(snapshot, rotation_days))

    parts.append("<h2>行业轮动矩阵（最近 20 个交易日 RPS15）</h2>")
    parts.append(render_rotation_matrix(metrics, rotation_days))

    parts.append("<h2>状态变化</h2>")
    parts.append(render_status_changes(snapshot))

    parts.append("""
<script>
function filterTable() {
    var search = document.getElementById('search').value.toLowerCase();
    var rpsFilter = document.getElementById('rps-filter').value;
    var statusFilter = document.getElementById('status-filter').value;
    var table = document.getElementById('strength-table');
    if (!table) return;
    var rows = table.querySelectorAll('tbody tr');
    rows.forEach(function(row) {
        var cells = row.querySelectorAll('td');
        if (!cells.length) return;
        var code = (cells[1] ? cells[1].textContent.toLowerCase() : '');
        var name = (cells[2] ? cells[2].textContent.toLowerCase() : '');
        var rps15 = parseFloat(cells[5] ? cells[5].textContent : '0');
        var status = row.getAttribute('data-status') || '';
        var show = true;
        if (search && !code.includes(search) && !name.includes(search)) show = false;
        if (rpsFilter === 'strong' && rps15 < 90) show = false;
        if (rpsFilter === 'observe' && rps15 < 80) show = false;
        if (rpsFilter === 'weak' && rps15 >= 80) show = false;
        if (statusFilter !== 'all' && status !== statusFilter) show = false;
        row.style.display = show ? '' : 'none';
    });
}
</script>
""")

    parts.append("<div class='footer'>")
    parts.append("数据来源：申万宏源研究（swsresearch.com）| AKShare | AKsignal 申万二级行业 RPS 监控模块")
    parts.append("</div>")
    parts.append("</body></html>")

    html_path.write_text("\n".join(parts), encoding="utf-8")
    return csv_path, html_path


def save_latest_html(html_path: Path, reports_dir: Path) -> Path:
    latest_path = reports_dir / "sw_industry_rps_latest.html"
    if html_path.exists():
        content = html_path.read_text(encoding="utf-8")
        latest_path.write_text(content, encoding="utf-8")
    return latest_path


def build_report_csv(snapshot: pd.DataFrame) -> pd.DataFrame:
    if snapshot.empty:
        return snapshot
    cols = [
        "industry_code", "industry_name", "RPS5", "RPS10", "RPS15",
        "return_5", "return_10", "return_15",
        "delta_rps15", "streak_90", "new_entry", "strong_streak",
        "accelerating", "falling_out", "short_term_acceleration",
        "medium_term_acceleration",
    ]
    available = [c for c in cols if c in snapshot.columns]
    return snapshot[available].sort_values("RPS15", ascending=False).reset_index(drop=True)
