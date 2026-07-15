from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest


@pytest.fixture
def sample_industry_data() -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=30, freq="B")
    industries = ["801016.SI", "801015.SI", "801011.SI"]
    rows = []
    for i, code in enumerate(industries):
        base = 1000 + i * 100
        for j, d in enumerate(dates):
            rows.append({
                "trade_date": d,
                "industry_code": code,
                "industry_name": f"Industry_{code}",
                "open": base + j * 2,
                "high": base + j * 2 + 5,
                "low": base + j * 2 - 3,
                "close": base + j * 2 + 1,
                "volume": 1000000,
                "amount": 100000000,
            })
    df = pd.DataFrame(rows)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df


@pytest.fixture
def sample_master() -> pd.DataFrame:
    return pd.DataFrame({
        "industry_code": ["801016.SI", "801015.SI", "801011.SI"],
        "industry_name": ["种植业", "渔业", "林业Ⅱ"],
        "parent_industry": ["农林牧渔", "农林牧渔", "农林牧渔"],
        "constituent_count": [20, 6, 4],
    })


@pytest.fixture
def sample_snapshot() -> pd.DataFrame:
    return pd.DataFrame({
        "industry_code": ["801016.SI", "801015.SI", "801011.SI"],
        "industry_name": ["种植业", "渔业", "林业Ⅱ"],
        "RPS5": [95.0, 50.0, 20.0],
        "RPS10": [92.0, 55.0, 25.0],
        "RPS15": [93.0, 45.0, 15.0],
        "return_5": [0.05, 0.01, -0.02],
        "return_10": [0.08, 0.02, -0.03],
        "return_15": [0.12, 0.01, -0.05],
        "delta_rps15": [5.0, -2.0, -3.0],
        "streak_80": [5, 0, 0],
        "streak_90": [3, 0, 0],
        "new_entry": [0, 0, 0],
        "strong_streak": [1, 0, 0],
        "accelerating": [0, 0, 0],
        "falling_out": [0, 0, 0],
        "short_term_acceleration": [2.0, 5.0, 5.0],
        "medium_term_acceleration": [-1.0, 10.0, 10.0],
    })


@pytest.fixture
def tmp_raw_dir(tmp_path: Path) -> Path:
    d = tmp_path / "raw" / "sw_industry"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def tmp_processed_dir(tmp_path: Path) -> Path:
    d = tmp_path / "processed" / "sw_industry"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def tmp_reports_dir(tmp_path: Path) -> Path:
    d = tmp_path / "reports" / "sw_industry_rps"
    d.mkdir(parents=True, exist_ok=True)
    return d
