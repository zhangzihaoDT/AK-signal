from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from src.market_facts.contract import build_snapshot
from src.market_facts.longbridge_source import identity_for, fetch_snapshot


def identity():
    return identity_for(SimpleNamespace(market="CN", exchange="SSE", symbol="600900", currency="CNY"))


def row(day="2026-10-15"):
    return dict(source_trade_date=day, open=10., high=12., low=9., close=11., volume=100., turnover=1100.)


def snapshot(rows):
    return build_snapshot(identity(), rows, date(2026, 10, 15), datetime.now(timezone.utc))


def test_short_history_not_verified():
    s = snapshot([row()])
    assert s["quality"]["target_present"]
    assert not s["quality"]["history_count_sufficient"]["360"]
    assert s["quality"]["calendar_completeness"] == "UNKNOWN"
    # Source facts do not decide operational admission; the module policy does.
    assert "production_eligible" not in s["quality"]
    assert not s["quality"]["price_basis_verified"]


@pytest.mark.parametrize("rows", [[row(), row()], [row("2026-10-16")], [dict(row(), close=float("nan"))], [dict(row(), low=12.)]])
def test_invalid_bars_rejected(rows):
    with pytest.raises(ValueError):
        snapshot(rows)


def test_missing_target_and_snapshot_revision():
    assert not snapshot([row("2026-10-14")])["quality"]["target_present"]
    assert snapshot([row()])["snapshot_id"] == snapshot([row()])["snapshot_id"]
    assert snapshot([row()])["snapshot_id"] != snapshot([dict(row(), close=10.)])["snapshot_id"]


def test_hk_and_ambiguous_identity():
    a = SimpleNamespace(market="HK", symbol="02525", currency="HKD")
    assert identity_for(a).provider_symbol == "02525.HK"
    with pytest.raises(ValueError):
        identity_for(SimpleNamespace(market="CN", symbol="900001", exchange=None))


def test_timestamp_market_timezone():
    bar = SimpleNamespace(timestamp=datetime(2026, 10, 14, 16, tzinfo=timezone.utc),
                          **{k: v for k, v in row().items() if k != "source_trade_date"})
    ctx = SimpleNamespace(history_candlesticks_by_date=lambda *args: [bar])
    s = fetch_snapshot(ctx, identity(), date(2026, 10, 15), None, None)
    assert s["quality"]["target_present"]
    bar.timestamp = datetime.fromtimestamp(datetime(2026, 10, 14, 16, tzinfo=timezone.utc).timestamp())
    s = fetch_snapshot(ctx, identity(), date(2026, 10, 15), None, None)
    assert s["quality"]["target_present"]
    assert s["bars"][0]["timestamp_conversion"] == "sdk_host_local_to_market"
