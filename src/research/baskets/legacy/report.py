"""主题篮子研究报告（zihao raccoon 视觉体系）。

`compare_report` 把 `calculate_basket` 已算好的事实（净值/指标/滚动/贡献/子组）渲染成
单文件 HTML；本模块**不制造新事实**，只排版：

  图 + 指标汇总表（含季度再平衡两列）+ 每篮子明细（滚动/贡献/子组）+ Cross-Basket 重叠
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go

_BENCHMARK_LABEL = "沪深300"
_COLORS = ["#174A7C", "#D79A36", "#7ECDEB", "#7A4A24", "#6B7C8F", "#06213D", "#B45309"]

_CSS = """
:root{--zh-blue:#174A7C;--zh-deep-blue:#06213D;--zh-cyan:#7ECDEB;--zh-light-blue:#DDEFF8;
--zh-cream:#FFF9EF;--zh-raccoon-gold:#D79A36;--zh-brown:#7A4A24;--zh-text:#1F2D3D;
--zh-muted:#6B7C8F;--zh-card:#FFFFFF;--zh-line:#D9E2EC;}
*{box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Noto Sans SC",sans-serif;
background:var(--zh-cream);color:var(--zh-text);line-height:1.6;margin:0;padding:36px 20px}
.wrap{max-width:1180px;margin:0 auto}
h1{color:var(--zh-deep-blue);font-size:24px;margin:0 0 4px}
h2{color:var(--zh-blue);font-size:18px;margin:30px 0 12px;border-left:4px solid var(--zh-raccoon-gold);padding-left:10px}
.sub{color:var(--zh-muted);font-size:13px;margin:0 0 6px}
.badge{display:inline-block;background:var(--zh-blue);color:#fff;border-radius:6px;font-size:12px;
padding:2px 8px;margin-left:8px;vertical-align:middle}
.card{background:var(--zh-card);border:1px solid var(--zh-line);border-radius:12px;padding:16px 18px;margin:14px 0;
box-shadow:0 1px 2px rgba(23,74,124,.05)}
.note{background:var(--zh-light-blue);border-radius:8px;padding:10px 14px;font-size:12.5px;color:var(--zh-blue);margin:10px 0}
table{border-collapse:collapse;width:100%;font-size:13px;margin:6px 0}
th,td{border-bottom:1px solid var(--zh-line);padding:7px 10px;text-align:right;white-space:nowrap}
th{background:var(--zh-light-blue);color:var(--zh-blue);font-weight:600}
th:first-child,td:first-child{text-align:left}
td.name{text-align:left;color:var(--zh-text)}
tr:hover td{background:#F7FBFF}
.pos{color:#B45309}.neg{color:var(--zh-blue)}
details.card summary{cursor:pointer;font-weight:600;color:var(--zh-blue);font-size:14px;list-style:none}
details.card summary::marker{content:""}
details.card summary::-webkit-details-marker{display:none}
details.card summary::before{content:"▶ ";color:var(--zh-raccoon-gold);font-size:11px}
details.card[open] summary::before{content:"▼ "}
.mini{color:var(--zh-muted);font-size:12px;margin:2px 0 8px}
.footer{color:var(--zh-muted);font-size:11.5px;margin-top:28px;border-top:1px dashed var(--zh-line);padding-top:10px}
"""


def _fmt(v: Any, digits: int = 2) -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    if f != f:  # NaN
        return "—"
    return f"{f:+.{digits}f}"


def _cls(v: Any) -> str:
    try:
        return "pos" if float(v) > 0 else ("neg" if float(v) < 0 else "")
    except (TypeError, ValueError):
        return ""


def save_result(result: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    key = result["basket"]["key"]
    result["nav"].to_csv(out_dir / f"{key}_nav.csv", index_label="date")
    result["constituents"].to_csv(out_dir / f"{key}_constituents.csv", index=False)
    result["group_metrics"].to_csv(out_dir / f"{key}_groups.csv", index=False)
    result["rolling"].to_csv(out_dir / f"{key}_rolling.csv", index=False)
    result["contributions"].to_csv(out_dir / f"{key}_contributions.csv", index=False)
    result["quarterly_nav"].to_csv(out_dir / f"{key}_quarterly_nav.csv", index_label="date")
    (out_dir / f"{key}_metrics.json").write_text(
        json.dumps(result["metrics"], ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / f"{key}_manifest.json").write_text(
        json.dumps(result["provenance"], ensure_ascii=False, indent=2), encoding="utf-8")


def cross_basket_overlap(results: list[dict[str, Any]]) -> pd.DataFrame:
    """找出多个篮子之间的共同成分及其各自的证据阶段与收益贡献。

    目的：当一只共同成分（如拓普）同时驱动多个篮子上涨时，避免把
    「单一成分拉动」误读为「两条主题同时成立」。
    """
    keys = [r["basket"]["key"] for r in results]
    constituents = {r["basket"]["key"]: r["constituents"] for r in results}
    contributions = {r["basket"]["key"]: r["contributions"] for r in results}
    rows: list[dict[str, Any]] = []
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            ka, kb = keys[i], keys[j]
            common = sorted(set(constituents[ka]["symbol"]) & set(constituents[kb]["symbol"]))
            for symbol in common:
                ra = constituents[ka][constituents[ka]["symbol"] == symbol].iloc[0]
                rb = constituents[kb][constituents[kb]["symbol"] == symbol].iloc[0]

                def _contribution(basket_key: str, sym: str) -> float | None:
                    sub = contributions[basket_key]
                    hit = sub[sub["symbol"] == sym]
                    if hit.empty:
                        return None
                    return round(float(hit["contribution_pct_points"].sum()), 4)

                rows.append({
                    "symbol": symbol,
                    "name": str(ra.get("name", symbol)),
                    "basket_a": ka,
                    "basket_b": kb,
                    "evidence_stage_a": str(ra.get("evidence_stage", "")),
                    "evidence_stage_b": str(rb.get("evidence_stage", "")),
                    "group_a": str(ra.get("group", "")),
                    "group_b": str(rb.get("group", "")),
                    "contribution_pct_a": _contribution(ka, symbol),
                    "contribution_pct_b": _contribution(kb, symbol),
                })
    return pd.DataFrame(rows)


def _overlap_html(overlap: pd.DataFrame) -> str:
    """把跨篮子共同成分表渲染成 HTML 片段（注入报告页）。"""
    if overlap.empty:
        return ""
    head = ["成分", "篮子A", "A 证据阶段", "A 组", "A 贡献(pt)",
            "篮子B", "B 证据阶段", "B 组", "B 贡献(pt)"]
    from ..stage_log import evidence_stage_cn
    rows = []
    for _, row in overlap.iterrows():
        cells = [
            html.escape(f"{row['name']}（{row['symbol']}）"),
            html.escape(str(row["basket_a"])),
            html.escape(evidence_stage_cn(str(row["evidence_stage_a"]))) or "—",
            html.escape(str(row["group_a"])),
            _fmt(row["contribution_pct_a"]),
            html.escape(str(row["basket_b"])),
            html.escape(evidence_stage_cn(str(row["evidence_stage_b"]))) or "—",
            html.escape(str(row["group_b"])),
            _fmt(row["contribution_pct_b"]),
        ]
        rows.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    return (
        '<h2>Cross-Basket 共同成分</h2>'
        '<div class="card"><p class="mini">同一成分同时出现在多个篮子时，避免把「单一成分拉动」误读为「两条主题同时成立」。</p>'
        "<table><thead><tr>" + "".join(f"<th>{c}</th>" for c in head) + "</tr></thead><tbody>"
        + "".join(rows) + "</tbody></table></div>"
    )


def _metrics_table(results: list[dict[str, Any]]) -> str:
    cols = [
        ("收益%", "return_pct"), ("超额%", "excess_pct"), ("年化%", "annualized_return_pct"),
        ("最大回撤%", "max_drawdown_pct"), ("Sharpe", "sharpe"), ("波动%", "annualized_volatility_pct"),
        ("胜率%", "win_rate_pct"), ("Top1贡献", "top1_contribution_pct_points"),
        ("Top3贡献", "top3_contribution_pct_points"),
        ("季度收益%", "quarterly_rebalance_return_pct"), ("季度超额%", "quarterly_rebalance_excess_pct"),
    ]
    head = ["篮子"] + [c[0] for c in cols]
    rows = []
    for r in results:
        key = r["basket"]["key"]
        label = r["basket"].get("label", key)
        m = r["metrics"]
        cells = [f'<td class="name">{html.escape(str(label))}</td>']
        for _, field in cols:
            v = m.get(field)
            cells.append(f'<td class="{_cls(v)}">{_fmt(v)}</td>' if field != "sharpe" else f'<td>{_fmt(v)}</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return (
        '<h2>指标汇总</h2><div class="card"><table><thead><tr>'
        + "".join(f"<th>{c}</th>" for c in head) + "</tr></thead><tbody>"
        + "".join(rows) + "</tbody></table></div>"
    )


def _rolling_table(rolling: pd.DataFrame) -> str:
    if rolling.empty:
        return '<p class="mini">无滚动数据</p>'
    last = rolling.iloc[-1]
    windows = [20, 60, 120]
    head = ["窗口", "滚动超额%", "滚动 Sharpe", "窗口最大回撤%"]
    rows = []
    for w in windows:
        e = last.get(f"excess_return_{w}d_pct")
        s = last.get(f"sharpe_{w}d")
        d = last.get(f"drawdown_{w}d_pct")
        rows.append("<tr>"
                    f'<td class="name">{w}D</td><td class="{_cls(e)}">{_fmt(e)}</td>'
                    f"<td>{_fmt(s)}</td><td class=\"{_cls(d)}\">{_fmt(d)}</td></tr>")
    return ("<table><thead><tr>" + "".join(f"<th>{c}</th>" for c in head)
            + "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>")


def _contributions_table(contributions: pd.DataFrame, top_n: int = 12) -> str:
    if contributions.empty:
        return '<p class="mini">无贡献数据</p>'
    head = ["标的", "组", "权重%", "区间收益%", "贡献(pt)"]
    sub = contributions.sort_values("contribution_pct_points", ascending=False)
    if len(sub) > top_n:
        shown = pd.concat([sub.head(top_n), sub.tail(3)]).drop_duplicates(subset=["symbol"])
    else:
        shown = sub
    rows = []
    for _, r in shown.iterrows():
        rows.append(
            "<tr>"
            f'<td class="name">{html.escape(str(r.get("name", r["symbol"])))}（{r["symbol"]}）</td>'
            f'<td>{html.escape(str(r.get("group", "")))}</td>'
            f'<td>{_fmt(r.get("weight_pct"), 2)}</td>'
            f'<td class="{_cls(r.get("return_pct"))}">{_fmt(r.get("return_pct"))}</td>'
            f'<td class="{_cls(r.get("contribution_pct_points"))}">{_fmt(r.get("contribution_pct_points"))}</td>'
            "</tr>")
    return ("<table><thead><tr>" + "".join(f"<th>{c}</th>" for c in head)
            + "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>")


def _groups_table(group_metrics: pd.DataFrame) -> str:
    if group_metrics.empty:
        return ""
    head = ["子组", "标的数", "子组收益%"]
    rows = []
    for _, r in group_metrics.iterrows():
        n = r.get("n_assets", 0)
        n_str = "—" if n is None or pd.isna(n) else str(int(n))
        rows.append("<tr>"
                    f'<td class="name">{html.escape(str(r.get("group")))}</td>'
                    f'<td>{n_str}</td>'
                    f'<td class="{_cls(r.get("return_pct"))}">{_fmt(r.get("return_pct"))}</td></tr>')
    return ("<table><thead><tr>" + "".join(f"<th>{c}</th>" for c in head)
            + "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>")


def _basket_cards(results: list[dict[str, Any]]) -> str:
    blocks = []
    for r in results:
        key = r["basket"]["key"]
        label = r["basket"].get("label", key)
        inner = ['<p class="mini">滚动指标（最新一日）</p>' + _rolling_table(r["rolling"]),
                 '<p class="mini">个股贡献（按贡献排序，含尾部负贡献）</p>' + _contributions_table(r["contributions"])]
        g = _groups_table(r["group_metrics"])
        if g:
            inner.append('<p class="mini">子组收益</p>' + g)
        blocks.append(
            f'<details class="card"><summary>{html.escape(str(label))}（{key}）</summary>'
            + "".join(inner) + "</details>")
    return "".join(blocks)


def compare_report(
    results: list[dict[str, Any]], out_dir: Path,
    filename: str = "basket_compare.html",
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    overlap = cross_basket_overlap(results)
    if not overlap.empty:
        overlap.to_csv(out_dir / "basket_overlap.csv", index=False)

    fig = go.Figure()
    for i, result in enumerate(results):
        key = result["basket"]["key"]
        label = result["basket"].get("label", key)
        nav = result["nav"]
        fig.add_trace(go.Scatter(
            x=nav.index, y=nav["basket"], mode="lines", name=label,
            line={"color": _COLORS[i % len(_COLORS)], "width": 2.8},
        ))
    benchmark_name = _BENCHMARK_LABEL
    if results:
        nav = results[0]["nav"]
        fig.add_trace(go.Scatter(
            x=nav.index, y=nav["benchmark"], mode="lines", name=benchmark_name,
            line={"color": "#9AA8B5", "width": 2, "dash": "dash"},
        ))
    fig.update_layout(
        title=f"Research Basket vs {benchmark_name}",
        xaxis_title="交易日", yaxis_title="净值（起点 = 100）", template="plotly_white",
        hovermode="x unified", font={"color": "#1F2D3D"},
        legend={"orientation": "h", "y": 1.08, "x": 0}, margin={"t": 60},
    )
    chart_html = fig.to_html(include_plotlyjs="cdn", full_html=False)

    metrics = results[0]["metrics"] if results else {}
    title = f"Research Basket vs {benchmark_name}"
    sub = (f"窗口 {metrics.get('start_date', '—')} .. {metrics.get('end_date', '—')}"
           f" ｜ {len(results)} 个篮子 ｜ 基准 {benchmark_name}")

    sections = [
        f"<h1>{html.escape(title)}</h1>",
        f'<p class="sub">{html.escape(sub)}<span class="badge">{len(results)} baskets</span></p>',
        '<div class="note">净值均以共同首个有效交易日 = 100；窗口已截到「最近完整交易日」（盘中排除当日未收盘 bar）。'
        "HTML 仅为 renderer，事实源为各篮子 <code>_nav/_metrics/_rolling/_contributions</code> 文件。</div>",
        '<div class="card">' + chart_html + "</div>",
        _metrics_table(results),
    ]
    cards = _basket_cards(results)
    if cards:
        sections.append("<h2>每篮子明细</h2>" + cards)
    overlap_section = _overlap_html(overlap)
    if overlap_section:
        sections.append(overlap_section)
    sections.append('<div class="footer">Research Basket ｜ 口径 group_equal + buy_and_hold ｜ AKSignal · zihao raccoon</div>')

    page = (
        "<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title><style>{_CSS}</style></head>"
        "<body><div class='wrap'>" + "".join(sections) + "</div></body></html>"
    )
    path = out_dir / filename
    path.write_text(page, encoding="utf-8")
    return path
