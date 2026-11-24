from datetime import date, datetime
import json
from pathlib import Path
import shutil
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from scripts.exploration.a_share_index_technical_analysis import updater as u

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 24, 12, tzinfo=ZoneInfo('Asia/Shanghai'))


@pytest.fixture
def archive(tmp_path):
    calendar = tmp_path / 'config/market_calendars/XSHG.json'
    calendar.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / 'config/market_calendars/XSHG.json', calendar)
    scopes = {}
    for code in ('000905', '000852'):
        source = tmp_path / f'{code}.csv'
        row = u.parse_row(code, date(2026, 11, 20), record(code))
        pd.DataFrame([row]).to_csv(source, index=False)
        audit = tmp_path / f'{code}.audit.json'
        u.save_json(audit, {'outputs_sha256': {source.name: u.digest(source)}})
        scopes[code] = dict(source=source.name, source_sha256=u.digest(source), source_audit=audit.name,
                            start='2026-11-20', end='2026-11-20', bars=1)
    manifest = tmp_path / 'sources.json'
    u.save_json(manifest, dict(provider='csindex', price_basis='index_level', indices=scopes))
    return tmp_path, manifest


def record(code, pe=20.0):
    return dict(indexCode=code, open=100.0, high=110.0, low=90.0, close=105.0,
                tradingVol=1000.0, tradingValue=2000.0, peg=pe)


def fake_fetch(folder, start, end, code, name):
    return {start: record(code), end: record(code, pe=None)}, {'file': 'mock.json'}


def test_incremental_publish_and_repeat_without_requests(archive, monkeypatch):
    root, manifest = archive
    calls = []

    def fetch(*args):
        calls.append(args)
        return fake_fetch(*args)

    monkeypatch.setattr(u, 'fetch', fetch)
    u.update(root, manifest, now=NOW)
    saved = manifest.read_bytes()
    for scope in json.loads(saved)['indices'].values():
        frame = pd.read_csv(root / scope['source'])
        assert frame.trade_date.tolist() == ['2026-11-20', '2026-11-23']
        assert pd.isna(frame.pe_ttm.iloc[-1])
        assert scope['end'] == '2026-11-23' and scope['bars'] == 2
    u.update(root, manifest, now=NOW)
    assert len(calls) == 2
    assert manifest.read_bytes() == saved
    assert all(call[1:3] == (date(2026, 11, 20), date(2026, 11, 23)) for call in calls)


@pytest.mark.parametrize('failure', ['missing_target', 'revised_overlap', 'bad_ohlc'])
def test_second_index_failure_preserves_manifest(archive, monkeypatch, failure):
    root, manifest = archive
    original = manifest.read_bytes()

    def fetch(*args):
        rows, receipt = fake_fetch(*args)
        if args[3] == '000852':
            if failure == 'missing_target':
                rows.pop(args[2])
            elif failure == 'revised_overlap':
                rows[args[1]]['peg'] = 21.0
            else:
                rows[args[2]]['high'] = 1.0
        return rows, receipt

    monkeypatch.setattr(u, 'fetch', fetch)
    with pytest.raises((ValueError, AssertionError)):
        u.update(root, manifest, now=NOW)
    assert manifest.read_bytes() == original


def test_weekend_future_and_calendar_bounds():
    calendar = u.SessionCalendar.load(ROOT / 'config/market_calendars/XSHG.json')
    assert u.target_session(calendar, date(2026, 11, 23), NOW) == date(2026, 11, 20)
    with pytest.raises(ValueError, match='future'):
        u.target_session(calendar, date(2026, 11, 25), NOW)
    with pytest.raises(ValueError, match='outside coverage'):
        u.target_session(calendar, calendar.start, NOW)


def test_concurrent_updater_rejected(archive):
    root, manifest = archive
    lock = root / 'data/audits/a_share_index_technical_analysis/updater.lock'
    lock.parent.mkdir(parents=True)
    with lock.open('a') as stream:
        u.fcntl.flock(stream, u.fcntl.LOCK_EX | u.fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match='another updater'):
            u.update(root, manifest, now=NOW)
