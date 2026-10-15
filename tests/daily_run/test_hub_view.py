from datetime import datetime
import json
from pathlib import Path
import shutil
import pandas as pd

from src import daily_hub, daily_run
from src.common import batch_release as release
from src.common.trading_calendar import SHANGHAI
from tests.daily_run.test_recovery_publication import harness


def setup(tmp_path, monkeypatch):
    (tmp_path / 'config').mkdir()
    shutil.copy(Path(__file__).parents[2] / 'config/a_share_calendar.json', tmp_path / 'config/a_share_calendar.json')
    calls, fail = harness(tmp_path, monkeypatch)
    return calls, fail, datetime(2026, 10, 13, 12, tzinfo=SHANGHAI)


def test_publication_manifest_and_quality_readonly(tmp_path, monkeypatch):
    _, fail, now = setup(tmp_path, monkeypatch)
    fail['enabled'] = False
    # Formal harness includes all required current quality fields.
    original = daily_run.validate_release
    def validate(td):
        value = {**original(td), 'data_complete': False, 'warnings': ['price_basis_verified=false']}
        (daily_run.outputs_dir() / 'run_day' / f'final_check_{td}.json').write_text(json.dumps(value))
        pd.DataFrame({'source_status': ['primary']}).to_parquet(
            daily_run.outputs_dir() / 'observation' / f'industry_rotation_{td}.parquet')
        return value
    monkeypatch.setattr(daily_run, 'validate_release', validate)
    assert daily_run.execute_batch(tmp_path, '20261012', True) == 0
    checkpoint = release.checkpoint_path(tmp_path, '20261012', True)
    before = release.fingerprint(checkpoint)
    v = daily_hub.view(tmp_path, now)
    assert v['lastRun']['status'] == 'PUBLISHED'
    assert v['details']['quality']['data_complete'] is False
    assert v['details']['quality']['warnings'] == ['price_basis_verified=false']
    assert len(v['artifacts']) == 2
    assert {a['extension'] for a in v['artifacts']} == {'.html', '.md'}
    assert all(a['runId'] == v['details']['publication']['run_id'] for a in v['artifacts'])
    assert release.fingerprint(checkpoint) == before
    release.atomic_json(tmp_path / '.run_day/scheduler/20261012.json',
                        {'status': 'published', 'error': 'old failure', 'next_attempt_at': '2026-10-13T13:00:00+08:00'})
    assert daily_hub.view(tmp_path, now)['details']['error'] == ''
    assert daily_hub.view(tmp_path, now)['details']['retryAt'] is None

    # A maintenance attempt for an already-published date must remain visible;
    # its previous report stays deliverable, without masquerading as new success.
    maintenance = release.new_checkpoint(tmp_path, '20261012', False, [['etf', 'calculate']])
    with release.batch_lock(tmp_path):
        active = daily_hub.view(tmp_path, now)
        assert active['lastRun']['status'] == 'RUNNING'
        assert active['details']['runId'] == maintenance['run_id']
        assert active['details']['publication']['run_id'] != maintenance['run_id']
        assert len(active['artifacts']) == 2

    # An expired calendar must block new execution while retaining old formal delivery.
    v = daily_hub.view(tmp_path, datetime(2027, 1, 1, 12, tzinfo=SHANGHAI))
    assert v['lastRun']['status'] == 'BLOCKED' and len(v['artifacts']) == 2

    # A later failed target must not consume the old release as this target's success.
    fail['enabled'] = True
    assert daily_run.execute_batch(tmp_path, '20261013', True) == 1
    v = daily_hub.view(tmp_path, datetime(2026, 10, 14, 12, tzinfo=SHANGHAI))
    assert v['lastRun']['status'] == 'WAITING'
    assert v['details']['targetDate'] == '20261013'
    assert v['details']['publication']['trade_date'] == '20261012'
    assert len(v['artifacts']) == 2


def test_running_waiting_blocked_and_no_unpublished_attachments(tmp_path, monkeypatch):
    _, _, now = setup(tmp_path, monkeypatch)
    assert daily_run.execute_batch(tmp_path, '20261012', True) == 1
    v = daily_hub.view(tmp_path, now)
    assert v['lastRun']['status'] == 'WAITING' and v['artifacts'] == []
    assert '/private/' not in v['details']['error']
    state = release.read_checkpoint(tmp_path, '20261012', True)
    state['status'] = 'running'
    release.save_checkpoint(tmp_path, state)
    with release.batch_lock(tmp_path):
        assert daily_hub.view(tmp_path, now)['lastRun']['status'] == 'RUNNING'
    release.atomic_json(tmp_path / '.run_day/scheduler/20261012.json', {'status': 'blocked', 'error': '签名不一致'})
    assert daily_hub.view(tmp_path, now)['lastRun']['status'] == 'BLOCKED'
    release.atomic_json(tmp_path / '.run_day/scheduler/20261012.json',
                        {'status': 'blocked', 'reason': 'RESTART_REQUIRED', 'error': '签名不一致'})
    recoverable = daily_hub.view(tmp_path, now)
    assert recoverable['lastRun']['status'] == 'FAILED'
    assert '先联网更新数据' in recoverable['details']['error']


def test_missed_schedule_is_visible_and_manual_target_is_actionable(tmp_path, monkeypatch):
    _, _, now = setup(tmp_path, monkeypatch)
    schedule_dir = tmp_path / '.run_day/scheduler'
    release.atomic_json(schedule_dir / 'agent_state.json', {
        'schema_version': 1, 'pid': 123, 'status': 'running',
        'started_at': now.isoformat(), 'last_checked_at': now.isoformat(),
        'last_occurrence_status': 'missed', 'last_occurrence_trade_date': '20261012',
    })
    release.atomic_json(schedule_dir / '20261012.json', {
        'schema_version': 2, 'trade_date': '20261012', 'status': 'missed',
        'source': 'scheduled', 'scheduled_at': '2026-10-13T08:30:00+08:00',
        'reason': 'MISSED_SCHEDULE', 'attempts': 0, 'history': [],
    })
    view = daily_hub.view(tmp_path, now)
    assert view['lastRun']['status'] == 'MISSED'
    assert view['details']['targetDate'] == '20261012'
    assert view['details']['schedule']['enabled'] is True
    assert view['details']['schedule']['manualPendingCount'] == 1
    assert view['details']['schedule']['missedPolicy'] == 'manual_only'
