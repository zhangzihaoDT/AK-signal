import pandas as pd

from src.market_facts.industry_observation import classify_trend_stage, cross_industry_direction


def _snapshot():
    return pd.DataFrame([
        {"industry_code": "801016.SI", "industry_name": "种植业", "parent_industry": "农林牧渔",
         "RPS15": 90.0, "RPS1": 80.0, "delta_rps15_5d": 5.0, "strength_level": "强势"},
        {"industry_code": "801015.SI", "industry_name": "渔业", "parent_industry": "农林牧渔",
         "RPS15": 30.0, "RPS1": 20.0, "delta_rps15_5d": -3.0, "strength_level": "弱势"},
        {"industry_code": "801081.SI", "industry_name": "半导体", "parent_industry": "电子",
         "RPS15": 85.0, "RPS1": 75.0, "delta_rps15_5d": 10.0, "strength_level": "观察"},
        {"industry_code": "801082.SI", "industry_name": "元件", "parent_industry": "电子",
         "RPS15": 10.0, "RPS1": 5.0, "delta_rps15_5d": -8.0, "strength_level": "中性"},
    ])


def test_cross_industry_direction_aggregates_and_sorts_by_parent():
    result = cross_industry_direction(_snapshot())
    assert len(result) == 2
    by_name = {row["parent_industry"]: row for row in result}
    nongye = by_name["农林牧渔"]
    assert nongye["industry_count"] == 2
    assert nongye["median_rps15"] == 60.0
    assert nongye["median_delta_rps15_5d"] == 1.0
    assert nongye["active_count"] == 1
    assert nongye["active_ratio"] == 0.5
    assert nongye["representative_industry"] == "种植业"
    assert [row["median_rps15"] for row in result] == [60.0, 47.5]


def test_cross_industry_direction_handles_empty_and_optional_columns():
    assert cross_industry_direction(pd.DataFrame()) == []
    assert cross_industry_direction(pd.DataFrame({"a": [1]})) == []
    result = cross_industry_direction(_snapshot().drop(
        columns=["RPS1", "delta_rps15_5d", "strength_level"]))
    assert len(result) == 2
    assert all(row["median_rps1"] is None for row in result)
    assert all(row["median_delta_rps15_5d"] is None for row in result)
    assert all(row["active_count"] == 0 for row in result)


def _row(**values):
    return {"RPS5": 50.0, "RPS15": 50.0, "falling_out": 0, **values}


def test_trend_stage_classification_is_observation_only():
    assert classify_trend_stage(_row(RPS15=90, RPS5=85)) == "已形成趋势"
    assert classify_trend_stage(_row(RPS15=82, RPS5=75)) == "已形成趋势"
    assert classify_trend_stage(_row(RPS5=100, RPS15=2.4)) == "正在启动"
    assert classify_trend_stage(_row(RPS5=85, RPS15=40)) == "正在启动"
    assert classify_trend_stage(_row(RPS15=80, RPS5=60, falling_out=1)) == "正在退潮"
    assert classify_trend_stage(_row(RPS15=94, RPS5=35)) == "正在退潮"
    assert classify_trend_stage(_row(RPS15=40, RPS5=50)) == "—"
    assert classify_trend_stage(_row(RPS15="abc")) == "—"
