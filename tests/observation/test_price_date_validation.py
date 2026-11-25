import json

import pandas as pd
import pytest

from src.observation import cli, quality, report


def _write_observation_facts(folder, trade_date, source_trade_date):
    for stem in ("etf_trends", "asset_directions", "industry_directions", "stock_positions", "etf_positions"):
        pd.DataFrame({"trade_date": [pd.Timestamp(trade_date)], "value": [1], "bottom_change": ["UNCHANGED"]}).to_parquet(
            folder / f"{stem}_{trade_date.replace('-', '')}.parquet", index=False)
    industry = pd.DataFrame({
        "trade_date": [pd.Timestamp(trade_date)],
        "source_trade_date": [source_trade_date],
        "source": ["tdx_official_day"],
        "industry_code": ["881001"],
        "industry_name": ["测试行业"],
        "parent_industry": ["测试方向"],
        "RPS15": [50.0],
        "data_status": ["confirmed"],
    })
    industry.to_parquet(folder / f"industry_rotation_{trade_date.replace('-', '')}.parquet", index=False)


@pytest.mark.parametrize("source_trade_date,valid", [
    ("2026-11-24", True),
    (None, False),
    ("2026-11-23", False),
])
def test_observation_validation_requires_dated_tdx_prices(tmp_path, monkeypatch, source_trade_date, valid):
    trade_date = "2026-11-24"
    out = tmp_path / "outputs"
    folder = out / "observation"
    folder.mkdir(parents=True)
    stock_dir = tmp_path / "stock_metrics"
    stock_dir.mkdir()
    monkeypatch.setattr(cli, "outputs_dir", lambda: out)
    monkeypatch.setattr(cli, "stock_metrics_dir", lambda: stock_dir)
    monkeypatch.setattr(quality, "data_quality_warnings", lambda _date: [])
    monkeypatch.setattr(report, "identity_issues", lambda _date: [])
    from src.market_facts import policy, tdx_industry
    monkeypatch.setattr(policy, "aligned_stock_rows", lambda frame, _date: pd.Series(True, index=frame.index))
    monkeypatch.setattr(tdx_industry, "universe", lambda: pd.DataFrame({"tdx_code": ["881001"]}))

    _write_observation_facts(folder, trade_date, source_trade_date)
    (folder / f"observation_{trade_date.replace('-', '')}.json").write_text(json.dumps({
        "modules": {"industry_rotation": {"status": "available", "verified_count": 1, "expected_count": 1}},
    }), encoding="utf-8")
    pd.DataFrame({"trade_date": [pd.Timestamp(trade_date)], "source_trade_date": [pd.Timestamp(trade_date)],
                  "data_status": ["current"], "market": ["CN"], "symbol": ["600900"],
                  "name": ["测试股票"]}).to_parquet(
        stock_dir / f"stock_metrics_{trade_date.replace('-', '')}.parquet", index=False)

    if valid:
        result = cli.validate(trade_date.replace('-', ''))
        assert result["industry_price_dates_verified"] is True
        assert result["status"] == "CONFIRMED"
    else:
        with pytest.raises(ValueError, match="独立源交易日"):
            cli.validate(trade_date.replace('-', ''))
