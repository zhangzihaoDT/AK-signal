from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from src.stock_trend.report import (
    build_price_chart,
    reorder_summary_df,
    public_summary_df,
    load_previous_summary,
    render_compact_signal_table,
    render_asset_details,
    render_summary_table,
    write_daily_report,
    _watch_level_style,
    _action_style,
    _rs_style,
)


def test_build_price_chart_returns_figure(sample_ohlcv):
    fig = build_price_chart(sample_ohlcv, title="Test Chart")
    assert isinstance(fig, go.Figure)
    assert len(fig.data) > 0


def test_build_price_chart_with_mas(sample_ohlcv):
    from src.stock_trend.indicators import add_indicators
    df = add_indicators(sample_ohlcv)
    fig = build_price_chart(df, title="MA Test")
    traces = [t.name for t in fig.data]
    assert "MA20" in traces
    assert "MA60" in traces


class TestWatchLevelStyle:
    def test_S(self):
        bg, fg = _watch_level_style("S")
        assert bg and fg

    def test_default(self):
        bg, fg = _watch_level_style("")
        assert bg and fg


class TestActionStyle:
    def test_focus(self):
        bg, fg = _action_style("重点观察")
        assert bg and fg

    def test_default(self):
        bg, fg = _action_style("invalid")
        assert bg and fg


class TestRsStyle:
    def test_strong_positive(self):
        bg, fg = _rs_style(0.2)
        assert "ffb3b3" in bg

    def test_moderate_positive(self):
        bg, fg = _rs_style(0.1)
        assert "ffe0e0" in bg

    def test_strong_negative(self):
        bg, fg = _rs_style(-0.2)
        assert "b3d9ff" in bg

    def test_none(self):
        bg, fg = _rs_style(None)
        assert bg and fg


def test_public_summary_df_drops_score():
    df = pd.DataFrame({"name": ["A"], "symbol": ["0001"], "score": [50]})
    result = public_summary_df(df)
    assert "score" not in result.columns


def test_reorder_summary_df():
    df = pd.DataFrame({"z": [1], "name": ["A"], "symbol": ["0001"]})
    result = reorder_summary_df(df)
    cols = list(result.columns)
    assert cols.index("name") < cols.index("symbol")


def test_load_previous_summary_no_dir(tmp_path):
    result = load_previous_summary(tmp_path / "nonexistent", pd.Timestamp("2026-07-15"))
    assert result.empty


def test_render_compact_signal_table_empty():
    html = render_compact_signal_table(pd.DataFrame())
    assert "无数据" in html


def test_render_compact_signal_table_non_empty():
    df = pd.DataFrame({
        "name": ["测试A"], "symbol": ["000001"], "market": ["CN"],
        "close": [100.0], "score_trend": [80], "watch_level": ["S"],
        "action": ["重点观察"], "relative_strength_20d": [0.15],
        "data_source": ["cache"], "data_freshness_days": [0],
        "reason": ["结构：收盘在MA20之上；量能：平稳(1.00x)"],
    })
    html = render_compact_signal_table(df)
    assert "测试A" in html
    assert "S" in html


def test_render_asset_details_empty():
    html = render_asset_details(pd.DataFrame(), {})
    assert html == ""


def test_render_asset_details_basic(sample_ohlcv):
    from src.stock_trend.indicators import add_indicators
    from src.stock_trend.report import build_price_chart
    df = add_indicators(sample_ohlcv)
    chart = build_price_chart(df, "test")
    summary = pd.DataFrame({
        "name": ["测试A"], "symbol": ["000001"], "market": ["CN"],
        "exchange": [""], "currency": ["CNY"], "category": [""],
        "data_source": ["cache"], "data_freshness_days": [0],
        "date": [pd.Timestamp("2026-03-01")], "close": [150.0],
        "score_trend": [80], "label": ["强势上行"],
        "watch_level": ["S"], "action": ["重点观察"],
        "change": ["维持强趋势"], "relative_strength_20d": [0.15],
        "risk_flags": [""], "reason": ["test"], "note": [""],
    })
    html = render_asset_details(summary, {"CN:000001": chart})
    assert "测试A" in html
    assert "000001" in html


def test_render_summary_table_empty():
    html = render_summary_table(pd.DataFrame())
    assert "无数据" in html


def test_render_summary_table_non_empty():
    df = pd.DataFrame({
        "name": ["测试A"], "symbol": ["000001"], "market": ["CN"],
        "watch_level": ["A"], "action": ["继续跟踪"],
        "relative_strength_20d": [0.05], "close": [100.0],
        "score": [75],
    })
    html = render_summary_table(df)
    assert "测试A" in html


def test_write_daily_report_creates_files(tmp_stock_report_dir, sample_ohlcv):
    from src.stock_trend.indicators import add_indicators
    from src.stock_trend.report import build_price_chart
    df = add_indicators(sample_ohlcv)
    chart = build_price_chart(df, "test")
    summary = pd.DataFrame({
        "name": ["测试A"], "symbol": ["000001"], "market": ["CN"],
        "close": [150.0], "score_trend": [80], "label": ["强势上行"],
        "watch_level": ["S"], "action": ["重点观察"],
        "change": ["维持强趋势"], "relative_strength_20d": [0.15],
        "risk_flags": [""], "reason": ["test"],
    })
    csv_path, html_path = write_daily_report(
        pd.Timestamp("2026-07-15"), summary, {"CN:000001": chart},
        out_dir=tmp_stock_report_dir,
    )
    assert csv_path.exists()
    assert html_path.exists()
    content = html_path.read_text(encoding="utf-8")
    assert "AKSignal" in content
    assert "测试A" in content
