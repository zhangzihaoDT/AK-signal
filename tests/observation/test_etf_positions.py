"""回归 ETF 日报复用的价格位置算法。"""

import numpy as np
import pandas as pd

from src.research.etf_bottom.price_map import compute_row


def _prices(values: np.ndarray) -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.bdate_range("2024-10-01", periods=len(values)),
        "close": values,
    })


def test_smooth_decline_is_classified_as_deep_bottom():
    rising = np.linspace(100, 300, 300)
    falling = 300 * np.exp(np.linspace(0, np.log(0.5), 80))
    frame = _prices(np.concatenate([rising, falling]))

    row = compute_row("A", "fixture", "equity", frame, frame.date.iloc[-1])

    assert row["price_pos_60"] <= 20
    assert row["price_pos_120"] <= 20
    assert row["price_pos_360"] <= 20
    assert row["bottom_state"] == "DEEP_BOTTOM"
    assert row["long_term_bottom"] is True


def test_old_corporate_action_only_invalidates_360_day_window():
    values = np.linspace(100, 160, 500)
    values[200] = values[199] * 0.5
    frame = _prices(values)

    row = compute_row("E", "fixture", "equity", frame, frame.date.iloc[-1])

    assert row["unreliable_60"] is False
    assert row["unreliable_120"] is False
    assert row["unreliable_360"] is True
    assert row["price_pos_60"] is not None
    assert row["price_pos_120"] is not None
    assert row["price_pos_360"] is None
    assert row["bottom_state"] == "UNRELIABLE"


def test_recent_corporate_action_invalidates_nested_windows():
    values = np.linspace(100, 160, 500)
    values[-5] = values[-6] * 0.5
    frame = _prices(values)

    row = compute_row("F", "fixture", "equity", frame, frame.date.iloc[-1])

    assert row["unreliable_60"] is True
    assert row["unreliable_120"] is True
    assert row["unreliable_360"] is True
    assert row["bottom_state"] == "UNRELIABLE"


def test_insufficient_360_day_history_stays_unknown():
    frame = _prices(np.linspace(100, 80, 200))

    row = compute_row("S", "fixture", "equity", frame, frame.date.iloc[-1])

    assert row["bottom_state"] == "UNRELIABLE"
    assert row["long_term_bottom"] is False
