import json

import pandas as pd

from src.observation import cli, report
from src.final_validation import cli as final_validation


def test_industry_module_unavailable_is_explicit_partial_not_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "outputs_dir", lambda: tmp_path)
    monkeypatch.setattr(cli, "stock_metrics_dir", lambda: tmp_path)
    monkeypatch.setattr(final_validation, "data_quality_warnings", lambda _date: [])
    monkeypatch.setattr(report, "identity_issues", lambda _date: [])

    folder = tmp_path / "observation"
    folder.mkdir()
    for stem in ("etf_trends", "asset_directions", "stock_positions", "etf_positions"):
        pd.DataFrame({"trade_date": [pd.Timestamp("2026-10-15")], "bottom_change": ["UNCHANGED"]}).to_parquet(
            folder / f"{stem}_20261015.parquet", index=False)
    for stem in ("industry_rotation", "industry_directions"):
        pd.DataFrame({"trade_date": pd.Series(dtype="datetime64[ns]"),
                      "industry_code": pd.Series(dtype=str)}).to_parquet(
            folder / f"{stem}_20261015.parquet", index=False)
    pd.DataFrame({"trade_date": [pd.Timestamp("2026-10-15")],
                  "source_trade_date": [pd.Timestamp("2026-10-15")],
                  "data_status": ["current"], "market": ["CN"], "symbol": ["600900"],
                  "name": ["长江电力"]}).to_parquet(tmp_path / "stock_metrics_20261015.parquet", index=False)
    (folder / "observation_20261015.json").write_text(json.dumps({
        "modules": {"industry_rotation": {"status": "unavailable", "verified_count": 0,
                                                "expected_count": 124, "reason": "target_date_missing_or_unverified"}}
    }), encoding="utf-8")

    result = cli.validate("20261015")
    assert result["ok"] is True
    assert result["status"] == "PARTIAL"
    assert result["data_complete"] is False
    assert result["industry_price_dates_verified"] is False
    assert any("行业轮动模块不可用" in warning for warning in result["warnings"])


def test_build_keeps_other_facts_and_emits_empty_industry_partition(tmp_path, monkeypatch):
    from src.sw_industry_rps import storage

    monkeypatch.setattr(cli, "outputs_dir", lambda: tmp_path / "outputs")
    monkeypatch.setattr(cli, "stock_metrics_dir", lambda: tmp_path / "stock_metrics")
    monkeypatch.setattr(cli, "sw_industry_raw_dir", lambda: tmp_path / "sw_raw")
    monkeypatch.setattr(cli, "sw_industry_processed_dir", lambda: tmp_path / "sw_processed")
    monkeypatch.setattr(storage, "compute_active_codes", lambda *_args: (["801081.SI"], [], False))
    def sw_prices_forbidden(*_args):
        raise AssertionError("TDX target must not consume SW prices, even if its cache exists")
    monkeypatch.setattr(storage, "load_metrics", sw_prices_forbidden)
    monkeypatch.setattr(report, "mark_identity", lambda frame, _date: frame)
    monkeypatch.setattr(report, "quality_lines", lambda *_args: [])

    daily = tmp_path / "daily"
    daily.mkdir()
    target = pd.Timestamp("2026-10-15")
    pd.DataFrame({
        "fund_code": ["510300"], "fund_name": ["沪深300ETF"], "rps15": [50.],
        "rps20": [50.], "rps60": [50.], "rps1": [50.], "delta_rps15": [0.],
        "liquidity": [80.], "rank_change_5d": [0.], "asset_bucket": ["broad_market"],
        "bucket_label": ["宽基"], "asset_class": ["权益"], "is_tech": [False],
        "theme": [None], "trade_date": [target], "run_date": ["2026-10-16"],
        "generated_at": ["test"], "data_status": ["confirmed"], "source": ["em"],
    }).to_parquet(daily / "rotation_20261015.parquet", index=False)
    pd.DataFrame({"fund_code": ["510300"], "price": [4.], "ma20": [3.9], "ma60": [3.8],
                  "ma20_slope": [0.1], "ma60_slope": [0.1]}).to_parquet(
        daily / "daily_indicators_20261015.parquet", index=False)
    monkeypatch.setattr(cli, "etf_signal_daily_dir", lambda: daily)
    metrics_dir = tmp_path / "stock_metrics"
    metrics_dir.mkdir()
    pd.DataFrame({"market": ["CN"], "symbol": ["600900"], "name": ["长江电力"],
                  "trend_score": [80.], "return_20d": [0.02], "data_status": ["current"]}).to_parquet(
        metrics_dir / "stock_metrics_20261015.parquet", index=False)
    monkeypatch.setattr(cli, "stock_positions", lambda _date: pd.DataFrame(
        columns=["market", "symbol", "name", "bottom_state", "bottom_change", "reliability"]))
    monkeypatch.setattr(cli, "etf_positions", lambda _date: pd.DataFrame(
        columns=["fund_code", "fund_name", "bottom_state", "bottom_change", "price_pos_360", "reliability"]))

    from src.market_facts import tdx_industry
    def fail_tdx(*args, **kwargs):
        raise ValueError('test unavailable')
    monkeypatch.setattr(tdx_industry, 'load', fail_tdx)
    cli.build("20261015")

    folder = tmp_path / "outputs" / "observation"
    industry = pd.read_parquet(folder / "industry_rotation_20261015.parquet")
    summary = json.loads((folder / "observation_20261015.json").read_text())
    assert industry.empty
    assert summary["modules"]["industry_rotation"]["provider"] == "tdx"
    assert summary["modules"]["industry_rotation"]["reason"] == "ValueError: test unavailable"
    assert len(pd.read_parquet(folder / "etf_trends_20261015.parquet")) == 1
    assert summary["industry_count"] == 0
    assert summary["missing"] == ["industry_rotation"]
    assert summary["modules"]["industry_rotation"]["status"] == "unavailable"


def test_unavailable_industry_panel_is_not_rendered_as_an_empty_signal():
    assets = pd.DataFrame(columns=["direction", "median_rps15", "change_5d", "active_ratio", "direction_state"])
    industries = pd.DataFrame(columns=["industry_name", "RPS15", "RPS5", "trend_stage"])
    etfs = pd.DataFrame(columns=["trend_active", "rps15", "liquidity", "fund_code"])
    stocks = pd.DataFrame(columns=["trend_score", "return_20d", "market", "symbol", "name"])
    etf_positions = pd.DataFrame(columns=["fund_code", "price_pos_360", "bottom_state", "bottom_change"])
    stock_positions = pd.DataFrame(columns=["market", "symbol", "price_pos_360", "bottom_state", "bottom_change"])
    html = report.render_html(
        "20261015", assets, industries, etfs, stocks,
        etf_positions, stock_positions, ["行业轮动模块降级：目标日价格已核验 0/124"],
        module_status={"industry_rotation": {"status": "unavailable", "verified_count": 0,
                                             "expected_count": 124}},
    )
    assert "行业轮动 · 本批不可用" in html
    assert "不展示旧日期或部分横截面" in html
