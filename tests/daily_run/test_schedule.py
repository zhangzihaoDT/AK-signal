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
    result = schedule.tick(root, offline=True, at=when,
                           launch=lambda *a: pytest.fail('spawn despite invalid calendar'))
    assert result['reason'] == 'CALENDAR_UNAVAILABLE'


def test_nonblocking_persistent_trigger_survives_scheduler_restart(root):
    calls = []
    def launch(*args):
        calls.append(args)
        return 12345
    result = schedule.tick(root, offline=True, at=at('2026-10-14T11:00'), launch=launch)
    assert result['status'] == 'running'
    assert result['attempts'] == 1
    assert calls[0][1:3] == ('20261013', True)
    # A new tick invocation reads disk; it has no in-memory scheduler state.
    for mode in (False, True):
        result = schedule.tick(root, offline=mode, at=at('2026-10-14T12:00'), launch=launch, alive=lambda p: True)
        assert result['reason'] == 'PROCESS_RUNNING'
    assert len(calls) == 1
    assert json.loads((root / '.run_day/scheduler/20261013.json').read_text())['attempts'] == 1


def test_latest_only_catchup_no_historical_loop(root):
    calls = []
    schedule.tick(root, offline=True, at=at('2026-10-19T13:00'),
                  launch=lambda *a: calls.append(a) or 11)
    assert [c[1] for c in calls] == ['20261016']


def test_native_business_and_trigger_lock_no_attempt_consumed(root):
    with release.batch_lock(root):
        result = schedule.tick(root, offline=True, at=at('2026-10-14T08:30'),
                               launch=lambda *a: pytest.fail('manual run overlapped'))
        assert result['reason'] == 'BUSINESS_LOCK'
        assert result['attempts'] == 0
    with schedule.trigger_lock(root):
        assert schedule.tick(root, offline=True)['reason'] == 'TRIGGER_LOCK'


def test_wait_backoff_and_retry_cap_across_modes(root):
    calls = []
    launch = lambda *a: calls.append(a) or 12
    def tick(instant, offline=True):
        return schedule.tick(root, offline=offline, at=at(instant), launch=launch, alive=lambda p: False)
    tick('2026-10-14T08:30')
    assert tick('2026-10-14T08:31')['status'] == 'waiting'
    tick('2026-10-14T09:00')
    assert len(calls) == 1
    assert tick('2026-10-14T09:01', False)['attempts'] == 2
    assert tick('2026-10-14T09:02')['status'] == 'waiting'
    tick('2026-10-14T10:31')
    assert len(calls) == 2
    assert tick('2026-10-14T10:32')['attempts'] == 3
    tick('2026-10-14T10:33')
    assert tick('2026-10-14T10:34')['status'] == 'exhausted'
    tick('2026-10-15T08:29')
    assert len(calls) == 3  # No reset at midnight/weekend or mode change.
    assert tick('2026-10-15T08:30')['attempts'] == 1  # New target has its own budget.


def test_spawn_failure_and_crash_before_pid_persist(root):
    def fail(*args):
        raise OSError('spawn error')
    result = schedule.tick(root, offline=True, at=at('2026-10-14T08:30'), launch=fail)
    assert result['status'] == 'waiting'
    assert result['attempts'] == 1
    path = root / '.run_day/scheduler/20261013.json'
    record = json.loads(path.read_text())
    record.update(status='starting', pid=None)
    path.write_text(json.dumps(record))
    assert schedule.tick(root, offline=True, at=at('2026-10-14T08:31'),
                         launch=lambda *a: pytest.fail('uncertain launch immediately repeated'))['status'] == 'waiting'


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
    result = schedule.tick(root, offline=True, at=at('2026-10-14T08:30'),
                           launch=lambda *a: pytest.fail('corrupt record reset'))
    assert result['reason'] == 'TRIGGER_RECORD_INVALID'
    assert path.read_text() == '{broken'
    path.unlink()
    path = release.checkpoint_path(root, '20261013', True)
    path.parent.mkdir(parents=True)
    path.write_text('{broken')
    result = schedule.tick(root, offline=True, at=at('2026-10-14T08:30'),
                           launch=lambda *a: pytest.fail('corrupt checkpoint reset'))
    assert result['reason'] == 'CHECKPOINT_INVALID'


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
    result = schedule.tick(root, offline=False, at=at('2026-10-13T08:30'),
                           launch=lambda *a: pytest.fail('partial silently reissued'))
    assert result['reason'] == 'PUBLICATION_NOT_COMPLETE'
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
