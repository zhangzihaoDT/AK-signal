"""主题篮子研究报告。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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


def compare_report(
    results: list[dict[str, Any]], out_dir: Path,
    filename: str = "basket_compare.html",
    nav_field: str = "nav",
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
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
    fig.write_html(path, include_plotlyjs="cdn")
    return path
