"""主题篮子研究报告。"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go


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
    head = [
        "成分", "篮子A", "A 证据阶段", "A 组", "A 贡献(pt)",
        "篮子B", "B 证据阶段", "B 组", "B 贡献(pt)",
    ]
    from .stage_log import evidence_stage_cn
    rows = []
    for _, row in overlap.iterrows():
        cells = [
            f"{html.escape(str(row['name']))}（{row['symbol']}）",
            html.escape(str(row["basket_a"])),
            html.escape(evidence_stage_cn(str(row["evidence_stage_a"]))) or "—",
            html.escape(str(row["group_a"])),
            row["contribution_pct_a"],
            html.escape(str(row["basket_b"])),
            html.escape(evidence_stage_cn(str(row["evidence_stage_b"]))) or "—",
            html.escape(str(row["group_b"])),
            row["contribution_pct_b"],
        ]
        rows.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    return (
        '<div style="margin:28px 8px;font-family:system-ui,-apple-system,sans-serif;">'
        "<h2 style='font-size:18px;color:#174A7C;'>Cross-Basket 共同成分（避免把单一成分误读为双主题同时成立）</h2>"
        f"<table style='border-collapse:collapse;font-size:13px;'><thead><tr>"
        + "".join(f"<th style='border:1px solid #D9E2EC;padding:6px 10px;background:#DDEFF8;color:#174A7C;'>{c}</th>" for c in head)
        + "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def compare_report(
    results: list[dict[str, Any]], out_dir: Path,
    filename: str = "basket_compare.html",
    nav_field: str = "nav",
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    overlap = cross_basket_overlap(results)
    if not overlap.empty:
        overlap.to_csv(out_dir / "basket_overlap.csv", index=False)
    fig = go.Figure()
    colors = ["#174A7C", "#D79A36", "#7ECDEB", "#7A4A24", "#6B7C8F"]
    for i, result in enumerate(results):
        key = result["basket"]["key"]
        label = result["basket"].get("label", key)
        nav = result[nav_field]
        fig.add_trace(go.Scatter(
            x=nav.index, y=nav["basket"], mode="lines", name=label,
            line={"color": colors[i % len(colors)], "width": 2.8},
        ))
    if results:
        nav = results[0][nav_field]
        fig.add_trace(go.Scatter(
            x=nav.index, y=nav["benchmark"], mode="lines", name="沪深300",
            line={"color": "#9AA8B5", "width": 2, "dash": "dash"},
        ))
    fig.update_layout(
        title=("Research Basket（季度调仓）" if nav_field == "quarterly_nav" else "Research Basket") + " vs 沪深300",
        xaxis_title="交易日",
        yaxis_title="净值（起点 = 100）", template="plotly_white",
        hovermode="x unified", font={"color": "#1F2D3D"},
        legend={"orientation": "h", "y": 1.08, "x": 0},
    )
    path = out_dir / filename
    page = fig.to_html(full_html=True, include_plotlyjs="cdn")
    overlap_section = _overlap_html(overlap)
    if overlap_section:
        page = page.replace("</body>", overlap_section + "</body>", 1)
    path.write_text(page, encoding="utf-8")
    return path
