from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from src.market_facts import industry_quality as quality


def test_source_status_and_completeness_labels_remain_stable():
    assert quality.classify_source_status(["realtime", "ths_board"]) == "fallback"
    assert quality.classify_source_status(["swsresearch", "ths_board"]) == "primary"
    assert quality.classify_source_status([]) == "fallback"
    assert quality.classify_data_status("primary", False) == "confirmed"
    assert quality.classify_data_status("fallback", True) == "complete"
    assert quality.classify_data_status("fallback", False) == "partial"


@pytest.mark.parametrize("source,evidence,expected", [
    ("swsresearch", None, True),
    ("swsresearch_analysis", None, True),
    ("hist_sw", None, True),
    ("realtime", None, False),
    ("swsresearch_realtime", None, False),
    ("unknown", None, False),
    ("realtime", "2026-10-15", True),
    ("realtime", "2026-10-14", False),
    ("swsresearch", "2026-10-14", False),
    ("swsresearch", "invalid", False),
])
def test_price_date_evidence(source, evidence, expected):
    frame = pd.DataFrame({"trade_date": ["2026-10-15"], "source": [source]})
    if evidence is not None:
        frame["source_trade_date"] = evidence
    assert bool(quality.verified_price_dates(frame).iloc[0]) is expected


def test_observation_closed_day_gate_preserves_cutoff(monkeypatch):
    monkeypatch.setattr("src.common.trading_calendar.shanghai_now",
                        lambda: datetime(2026, 11, 25, 15, 9, tzinfo=ZoneInfo("Asia/Shanghai")))
    assert not quality.observation_falls_on_closed_day(pd.Timestamp("2026-11-25"))
    assert quality.observation_falls_on_closed_day(pd.Timestamp("2026-11-24"))
    assert not quality.observation_falls_on_closed_day(pd.Timestamp("2026-11-26"))
