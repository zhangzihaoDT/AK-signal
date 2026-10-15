from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

import pytest

from src import daily_run, daily_schedule as schedule
from src.common import batch_release as release
from src.common.trading_calendar import TradingCalendar, SHANGHAI
from tests.daily_run.test_recovery_publication import harness


@pytest.fixture
def root(tmp_path):
    (tmp_path / 'config').mkdir()
    shutil.copy(Path(__file__).parents[2] / 'config/a_share_calendar.json',
                tmp_path / 'config/a_share_calendar.json')
    return tmp_path


def at(value):
    return datetime.fromisoformat(value).replace(tzinfo=SHANGHAI)


@pytest.mark.parametrize('instant,target', [
    ('2026-10-14T08:30', '20261013'),
    ('2026-10-14T18:00', '20261013'),  # Wake after close: latest 08:30 batch, not today's bar.
    ('2026-10-12T08:30', '20261009'),
    ('2026-10-10T08:30', '20261009'),  # Saturday adjustment workday is closed.
    ('2026-10-08T08:30', '20260930'),
    ('2026-10-07T08:30', '20260930'),
    ('2026-02-24T08:30', '20260213'),
    ('2026-01-01T08:30', '20251231'),
    ('2026-10-14T08:29', '20261012'),  # Eligible previous occurrence during early wake.
])
def test_exchange_calendar_and_wake_target(root, instant, target):
    calendar = TradingCalendar(root / 'config/a_share_calendar.json')
    assert calendar.scheduled_target(at(instant))[0] == target


def test_timezone_not_host_timezone(root):
    calendar = TradingCalendar(root / 'config/a_share_calendar.json')
    assert calendar.scheduled_target(datetime(2026, 10, 14, 0, 30, tzinfo=timezone.utc))[0] == '20261013'
    with pytest.raises(ValueError, match='时区'):
        calendar.complete_day(datetime(2026, 10, 14))


@pytest.mark.parametrize('failure', ['missing', 'expired', 'corrupt', 'source_missing'])
def test_calendar_fail_closed(root, failure):
    path = root / 'config/a_share_calendar.json'
    when = at('2027-01-01T07:00') if failure == 'expired' else at('2026-10-14T08:30')
    if failure == 'missing':
        path.unlink()
    elif failure in ('corrupt', 'source_missing'):
        value = json.loads(path.read_text())
        value['trading_days' if failure == 'corrupt' else 'sources'].pop()
        if failure == 'source_missing':
            value['sources'] = []
        path.write_text(json.dumps(value))
    result = schedule.scheduled_poll(root, at=when,
                                     launch=lambda *a: pytest.fail('spawn despite invalid calendar'))
    assert result['reason'] == 'CALENDAR_UNAVAILABLE'


def test_scheduled_run_launches_only_during_0830_minute(root):
    calls = []
    launch = lambda *args: calls.append(args) or 12345
    result = schedule.scheduled_poll(root, at=at('2026-10-14T08:30'), launch=launch)
    assert result['occurrences'][0]['status'] == 'running'
    assert calls[0][1:3] == ('20261013', False)
    assert schedule.scheduled_poll(root, at=at('2026-10-14T08:31'), launch=launch,
                                   alive=lambda _pid: True)['occurrences'][0]['reason'] == 'PROCESS_RUNNING'
    record = json.loads((root / '.run_day/scheduler/20261013.json').read_text())
    assert record['attempts'] == 1 and record['source'] == 'scheduled'
    assert record['offline'] is False


def test_missed_schedule_waits_for_manual_button(root):
    calls = []
    launch = lambda *args: calls.append(args) or 99
    missed = schedule.scheduled_poll(root, at=at('2026-10-14T08:31'), launch=launch)
    assert missed['occurrences'][0]['status'] == 'missed'
    assert not calls
    assert schedule.scheduled_poll(root, at=at('2026-10-14T09:00'), launch=launch)['occurrences'][0]['status'] == 'missed'
    resumed = schedule.manual_run(root, at=at('2026-10-14T09:01'), launch=launch)
    assert resumed['status'] == 'running' and resumed['source'] == 'manual'
    assert calls[0][1:3] == ('20261013', False)


def test_scheduler_restart_marks_each_elapsed_date_missed_without_running(root):
    calls = []
    launch = lambda *args: calls.append(args) or 9
    schedule.scheduled_poll(root, at=at('2026-10-14T08:00'), launch=launch)
    result = schedule.scheduled_poll(root, at=at('2026-10-16T08:31'), launch=launch)
    records = [json.loads((root / '.run_day/scheduler' / f'{day}.json').read_text())
               for day in ('20261013', '20261014', '20261015')]
    assert [r['status'] for r in records] == ['missed', 'missed', 'missed']
    assert result['occurrences'][-1]['status'] == 'missed'
    assert calls == []


def test_failed_scheduled_attempt_is_not_retried_until_manual_run(root):
    calls = []
    launch = lambda *args: calls.append(args) or 12
    schedule.scheduled_poll(root, at=at('2026-10-14T08:30'), launch=launch)
    failed = schedule.scheduled_poll(root, at=at('2026-10-14T08:31'), launch=launch,
                                     alive=lambda _pid: False)
    assert failed['occurrences'][0]['status'] == 'failed'
    assert len(calls) == 1
    schedule.scheduled_poll(root, at=at('2026-10-14T09:00'), launch=launch)
    assert len(calls) == 1
    assert schedule.manual_run(root, at=at('2026-10-14T09:01'), launch=launch)['status'] == 'running'
    assert len(calls) == 2


def test_spawn_failure_is_failed_and_not_automatically_retried(root):
    result = schedule.scheduled_poll(root, at=at('2026-10-14T08:30'),
                                     launch=lambda *_: (_ for _ in ()).throw(OSError('spawn error')))
    assert result['occurrences'][0]['status'] == 'failed'
    later = schedule.scheduled_poll(root, at=at('2026-10-14T08:31'),
                                    launch=lambda *_: pytest.fail('automatic retry'))
    assert later['occurrences'][0]['status'] == 'failed'


def test_native_business_and_trigger_locks_do_not_spawn(root):
    with release.batch_lock(root):
        result = schedule.scheduled_poll(root, at=at('2026-10-14T08:30'),
                                         launch=lambda *_: pytest.fail('overlapping run'))
        assert result['occurrences'][0]['reason'] == 'BUSINESS_LOCK'
    with schedule.trigger_lock(root):
        result = schedule.scheduled_poll(root, at=at('2026-10-14T08:30'))
        assert result['occurrences'][0]['reason'] == 'TRIGGER_LOCK'


def test_cross_mode_dedupe_and_previous_day_archived_receipt(tmp_path, monkeypatch):
    calls, fail = harness(tmp_path, monkeypatch)
    fail['enabled'] = False
    assert daily_run.execute_batch(tmp_path, '20261012', True) == 0
    first = release.published_batch(tmp_path, '20261012')
    assert daily_run.execute_batch(tmp_path, '20261012', False) == 0
    assert len(calls) == 6
    # A later published pointer must not erase evidence of the first publication.
    assert daily_run.execute_batch(tmp_path, '20261013', True) == 0
    assert release.published_batch(tmp_path, '20261012')['run_id'] == first['run_id']


def test_private_receipt_is_not_commit_evidence(tmp_path, monkeypatch):
    calls, fail = harness(tmp_path, monkeypatch)
    fail['enabled'] = False
    monkeypatch.setattr(release, 'publish_workspace', lambda *a: (_ for _ in ()).throw(OSError('commit failed')))
    assert daily_run.execute_batch(tmp_path, '20261012', True) == 1
    assert release.published_batch(tmp_path, '20261012') is None


def test_partial_never_switches_pointer_in_scheduled_run(tmp_path, monkeypatch):
    calls, fail = harness(tmp_path, monkeypatch)
    fail['enabled'] = False
    original = daily_run.validate_release
    monkeypatch.setattr(daily_run, 'validate_release', lambda d: {**original(d), 'status': 'PARTIAL'})
    assert daily_run.execute_batch(tmp_path, '20261012', True, require_complete=True) == 1
    assert (tmp_path / 'outputs/report.txt').read_text() == 'previous complete report'
    state = release.read_checkpoint(tmp_path, '20261012', True)
    assert state['status'] == 'failed'
    run_id = state['run_id']
    monkeypatch.setattr(daily_run, 'validate_release', original)
    assert daily_run.execute_batch(tmp_path, '20261012', True, require_complete=True) == 0
    assert len(calls) == 6  # Restore publication only, not a full new batch.
    assert release.published_batch(tmp_path, '20261012')['run_id'] == run_id


def test_corrupt_published_receipt_blocks_duplicate(tmp_path, monkeypatch):
    calls, fail = harness(tmp_path, monkeypatch)
    fail['enabled'] = False
    daily_run.execute_batch(tmp_path, '20261012', True)
    path = tmp_path / 'outputs/run_day/publication.json'
    receipt = json.loads(path.read_text())
    receipt['run_id'] = '0' * 32
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match='run_id'):
        daily_run.execute_batch(tmp_path, '20261012', False)
    assert len(calls) == 6


def test_spawn_uses_native_make_complete_offline_and_detaches(root, monkeypatch):
    captured = {}
    def popen(argv, **kwargs):
        captured.update(argv=argv, **kwargs)
        return type('Child', (), {'pid': 123})()
    monkeypatch.setattr(schedule.subprocess, 'Popen', popen)
    assert schedule.spawn_batch(root, '20261013', True, root / 'trigger.log') == 123
    assert captured['argv'] == ['make', 'run-day-offline', 'RUN_DATE=20261013', 'RUN_FLAGS=--require-complete']
    assert captured['start_new_session'] is True
    schedule.spawn_batch(root, '20261013', False, root / 'online.log')
    assert captured['argv'] == ['make', 'run-day', 'RUN_DATE=20261013', 'RUN_FLAGS=--require-complete']
    schedule.spawn_batch(root, '20261013', False, root / 'restart.log', restart=True)
    assert captured['argv'] == ['make', 'run-day', 'RUN_DATE=20261013', 'RUN_FLAGS=--require-complete --restart']


def test_manual_run_restarts_incompatible_checkpoint(root, monkeypatch):
    target = '20261013'
    checkpoint = release.new_checkpoint(root, target, False, [['step']])
    release.atomic_json(schedule.record_path(root, target), {
        'schema_version': 2, 'trade_date': target, 'status': 'failed', 'attempts': 1,
        'offline': False, 'source': 'scheduled', 'history': [],
    })
    monkeypatch.setattr(schedule.release, 'implementation_signature', lambda _root: 'new-signature')
    calls = []
    launch = lambda *args: calls.append(args) or 123
    restarted = schedule.manual_run(root, at=at('2026-10-14T09:00'), launch=launch)
    assert restarted['status'] == 'running'
    assert restarted['source'] == 'manual_restart'
    assert restarted['maintenance_restart'] is True
    assert restarted['history'][-1]['maintenance_restart'] is True
    assert calls and calls[0][1] == target


def test_manual_run_migrates_legacy_offline_failure_to_online(root):
    target = '20261013'
    release.new_checkpoint(root, target, True, [['step']])
    release.atomic_json(schedule.record_path(root, target), {
        'schema_version': 2, 'trade_date': target, 'status': 'blocked', 'reason': 'RESTART_REQUIRED',
        'attempts': 1, 'offline': True, 'history': [],
    })
    calls = []
    result = schedule.manual_run(root, at=at('2026-10-14T09:00'),
                                 launch=lambda *args: calls.append(args) or 123)
    assert result['status'] == 'running'
    assert result['offline'] is False
    assert calls[0][1:3] == (target, False)


def test_maintenance_restart_refuses_when_signature_matches(root):
    result = schedule.manual_run(root, at=at('2026-10-14T09:00'), restart=True,
                                 launch=lambda *_: pytest.fail('unnecessary maintenance restart'))
    assert result['status'] == 'blocked'
    assert result['reason'] == 'RESTART_NOT_REQUIRED'


def test_completed_fallback_publication_retains_warnings(tmp_path, monkeypatch):
    calls, fail = harness(tmp_path, monkeypatch)
    fail['enabled'] = False
    original = daily_run.validate_release
    def fallback(d):
        value = {**original(d), 'status': 'COMPLETE', 'warnings': ['FALLBACK_SOURCE', 'price_basis_verified=false'],
                 'data_complete': False}
        (daily_run.outputs_dir() / 'run_day' / f'final_check_{d}.json').write_text(json.dumps(value))
        return value
    monkeypatch.setattr(daily_run, 'validate_release', fallback)
    assert daily_run.execute_batch(tmp_path, '20261012', True, require_complete=True) == 0
    receipt = release.published_batch(tmp_path, '20261012')
    assert receipt['result']['warnings'] == ['FALLBACK_SOURCE', 'price_basis_verified=false']
    assert receipt['result']['data_complete'] is False


def test_corrupt_trigger_and_checkpoint_do_not_reset_budget(root):
    path = root / '.run_day/scheduler/20261013.json'
    path.parent.mkdir(parents=True)
    path.write_text('{broken')
    result = schedule.scheduled_poll(root, at=at('2026-10-14T08:30'),
                                     launch=lambda *a: pytest.fail('corrupt record reset'))
    assert result['occurrences'][0]['reason'] == 'TRIGGER_RECORD_INVALID'
    assert path.read_text() == '{broken'
    path.unlink()
    path = release.checkpoint_path(root, '20261013', False)
    path.parent.mkdir(parents=True)
    path.write_text('{broken')
    result = schedule.scheduled_poll(root, at=at('2026-10-14T08:30'),
                                     launch=lambda *a: pytest.fail('corrupt checkpoint reset'))
    assert result['occurrences'][0]['reason'] == 'CHECKPOINT_INVALID'


def test_scheduler_rejects_preexisting_partial_release(root, monkeypatch):
    calls, fail = harness(root, monkeypatch)
    fail['enabled'] = False
    original = daily_run.validate_release
    def partial(d):
        value = {**original(d), 'status': 'PARTIAL'}
        (daily_run.outputs_dir() / 'run_day' / f'final_check_{d}.json').write_text(json.dumps(value))
        return value
    monkeypatch.setattr(daily_run, 'validate_release', partial)
    assert daily_run.execute_batch(root, '20261012', True) == 0
    result = schedule.scheduled_poll(root, at=at('2026-10-13T08:30'),
                                     launch=lambda *a: pytest.fail('partial silently reissued'))
    assert result['occurrences'][0]['reason'] == 'PUBLICATION_NOT_COMPLETE'
    with pytest.raises(ValueError, match='完整性门控'):
        daily_run.execute_batch(root, '20261012', False, require_complete=True)


def test_retry_preserves_checkpoint_id_but_refreshes_online_acquisition(tmp_path, monkeypatch):
    calls, fail = harness(tmp_path, monkeypatch)
    assert daily_run.execute_batch(tmp_path, '20261012', False, require_complete=True) == 1
    run_id = release.read_checkpoint(tmp_path, '20261012', False)['run_id']
    fail['enabled'] = False
    assert daily_run.execute_batch(tmp_path, '20261012', False, require_complete=True) == 0
    assert calls == [1, 2, 3, 4, 5, 1, 2, 3, 4, 5, 6]
    assert release.published_batch(tmp_path, '20261012')['run_id'] == run_id


def test_calendar_close_boundary_and_missing_year_source(root):
    path = root / 'config/a_share_calendar.json'
    calendar = TradingCalendar(path)
    assert calendar.complete_day(at('2026-10-14T16:29')) == '20261013'
    assert calendar.complete_day(at('2026-10-14T16:30')) == '20261014'
    value = json.loads(path.read_text())
    value['sources'].pop()
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='全部年份'):
        TradingCalendar(path)


def test_online_schedule_waits_for_dated_primary_without_changing_manual_policy(tmp_path, monkeypatch):
    calls, fail = harness(tmp_path, monkeypatch)
    fail['enabled'] = False
    original = daily_run.validate_release
    monkeypatch.setattr(daily_run, 'validate_release', lambda d: {**original(d), 'status': 'COMPLETE'})
    assert daily_run.execute_batch(tmp_path, '20261012', False, require_complete=True) == 1
    assert '独立源交易日' in release.read_checkpoint(tmp_path, '20261012', False)['error']
    assert (tmp_path / 'outputs/report.txt').read_text() == 'previous complete report'
    run_id = release.read_checkpoint(tmp_path, '20261012', False)['run_id']
    monkeypatch.setattr(daily_run, 'validate_release', original)
    assert daily_run.execute_batch(tmp_path, '20261012', False, require_complete=True) == 0
    assert release.published_batch(tmp_path, '20261012')['run_id'] == run_id
