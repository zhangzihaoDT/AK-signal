"""比较多个观察组/主题等权组合与 HS300 的近一年表现（对比折线图）。"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

COMBO = [
    {
        "key": "scania_china_watch",
        "source": "group",
        "label": "斯堪尼亚中国观察组（四组等权）",
        "color": "#174A7C",
        "group_order": ["electric_drive", "transmission", "thermal_management", "smart_chassis"],
    },
    {
        "key": "china_auto_global",
        "source": "theme",
        "label": "中国汽车全球化（五组等权）",
        "color": "#D79A36",
        "group_order": ["oem_global", "battery_global", "global_ev_components",
                        "global_auto_components", "adas_lidar"],
    },
    {
        "key": "ai_infrastructure",
        "source": "theme",
        "label": "AI 基础设施（六组等权）",
        "color": "#7ECDEB",
        "group_order": ["optical_interconnect", "server_network", "semiconductor_components",
                        "high_speed_interconnect", "server_power", "liquid_cooling"],
    },
]


def load_prices(root: Path, combo: dict) -> pd.DataFrame:
    sub = "observation_groups" if combo["source"] == "group" else "themes"
    price_path = root / "data" / "raw" / sub / combo["key"] / f"{combo['key']}_prices.parquet"
    prices = pd.read_parquet(price_path)
    prices["date"] = pd.to_datetime(prices["date"]).dt.normalize()
    return prices


def build_composite(prices: pd.DataFrame, combo: dict, index: pd.DatetimeIndex) -> pd.Series:
    """组间等权、组内等权合成净值（先各标的归一化，再对齐统一交易日）。"""
    curves: dict[str, pd.Series] = {}
    for group in combo["group_order"]:
        sub = prices[prices["group"] == group]
        stock_curves = []
        for symbol, stock in sub.groupby("symbol"):
            close = stock.set_index("date")["close"].sort_index()
            stock_curves.append(close / close.iloc[0])
        group_curve = pd.concat(stock_curves, axis=1, sort=True).mean(axis=1)
        curves[group] = group_curve.reindex(index).ffill()
    return pd.DataFrame(curves)[combo["group_order"]].mean(axis=1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--exclude", action="append", default=[],
                        help="排除组，格式 <combo_key>:<group>（可多次），如 china_auto_global:oem_global")
    parser.add_argument("--out", type=Path, default=Path("outputs/research/watchlist_compare"))
    args = parser.parse_args()

    excluded: dict[str, list[str]] = {}
    for spec in args.exclude:
        combo_key, _, group = spec.partition(":")
        excluded.setdefault(combo_key, []).append(group)

    active_combos = []
    for combo in COMBO:
        combo = dict(combo)
        removed = excluded.get(combo["key"], [])
        combo["group_order"] = [g for g in combo["group_order"] if g not in removed]
        if len(combo["group_order"]) < 2:
            raise SystemExit(f"exclude too many groups for {combo['key']}: remaining {combo['group_order']}")
        if removed:
            n = len(combo["group_order"])
            combo["label"] = f"{combo['label'].split('（')[0]}（剔除{('、'.join(removed))} · {n}组等权）".replace(" · ", "·")
        active_combos.append(combo)

    root = Path(__file__).resolve().parents[1]
    benchmark_path = root / "data" / "raw" / "_benchmark_sh000300.csv"
    benchmark = pd.read_csv(benchmark_path, parse_dates=["date"])
    benchmark["date"] = benchmark["date"].dt.normalize()
    benchmark = benchmark.set_index("date")["close"].sort_index()

    index = benchmark.index
    series = benchmark.rename("hs300").to_frame()
    for combo in active_combos:
        prices = load_prices(root, combo)
        composite = build_composite(prices, combo, index)
        composite = composite.rename(combo["key"])
        series = series.join(composite, how="inner")

    series = series.loc[series.index >= series.index.max() - pd.Timedelta(days=args.days)]
    # 统一起点：取所有曲线都有值的首个交易日，避免观察组滞后一日导致 iloc[0] 为 NaN
    series = series.loc[series.dropna().index[0]:]
    series = series / series.iloc[0] * 100

    args.out.mkdir(parents=True, exist_ok=True)
    series.to_csv(args.out / "watchlist_vs_hs300.csv", index_label="date")

    final = series.iloc[-1]
    returns = final - 100
    stats = pd.DataFrame({"final_index": final, "return_pct": returns})
    stats.to_csv(args.out / "watchlist_vs_hs300_stats.csv", header=True)

    fig = go.Figure()
    for combo in active_combos:
        fig.add_trace(go.Scatter(
            x=series.index, y=series[combo["key"]], mode="lines",
            name=combo["label"], line={"color": combo["color"], "width": 3},
        ))
    fig.add_trace(go.Scatter(
        x=series.index, y=series["hs300"], mode="lines",
        name="沪深300", line={"color": "#9AA8B5", "width": 2.5, "dash": "dash"},
    ))
    ann_text = "  |  ".join(
        [f"{'汽车（剔OEM）' if combo['key']=='china_auto_global' and excluded.get('china_auto_global') else combo['label'].split('（')[0]} {returns[combo['key']]:+.2f}%"
         for combo in active_combos]
        + [f"HS300 {returns['hs300']:+.2f}%"]
    )
    fig.update_layout(
        title="观察组 / 主题 vs 沪深300",
        xaxis_title="交易日",
        yaxis_title="净值（起点 = 100）",
        template="plotly_white",
        hovermode="x unified",
        font={"color": "#1F2D3D"},
        legend={"orientation": "h", "y": 1.08, "x": 0},
        annotations=[{
            "x": 1, "y": 0.02, "xref": "paper", "yref": "paper", "showarrow": False,
            "xanchor": "right", "text": ann_text,
        }],
    )
    fig.write_html(args.out / "watchlist_vs_hs300.html", include_plotlyjs="cdn")

    print(f"period: {series.index[0].date()} .. {series.index[-1].date()} ({len(series)} trading days)")
    print(stats.to_string(float_format=lambda x: f"{x:.2f}"))
    print(f"saved: {args.out}")


if __name__ == "__main__":
    main()
