from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest


@pytest.fixture
def sample_ohlcv() -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=60, freq="B")
    base = 100.0
    rows = []
    for i, d in enumerate(dates):
        rows.append({
            "date": d,
            "open": base + i * 0.5,
            "high": base + i * 0.5 + 2.0,
            "low": base + i * 0.5 - 1.0,
            "close": base + i * 0.5 + 0.5,
            "volume": 1_000_000 + i * 1000,
        })
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df


@pytest.fixture
def tmp_stock_report_dir(tmp_path: Path) -> Path:
    d = tmp_path / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def tmp_data_dirs(tmp_path: Path) -> dict[str, Path]:
    return {
        "raw": tmp_path / "raw",
        "processed": tmp_path / "processed",
        "reports": tmp_path / "reports",
    }
