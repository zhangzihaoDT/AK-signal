"""A dead make launcher must not keep Hub Run/Resume stuck at RUNNING."""
import subprocess
from types import SimpleNamespace

import pytest

from src import daily_schedule as schedule
from src.common import batch_release as release
from tests.daily_run.test_schedule import root, at


@pytest.mark.parametrize('pid', [None, 0, -1, '123'])
def test_invalid_pid_is_never_signalled(monkeypatch, pid):
    monkeypatch.setattr(schedule.os, 'kill', lambda *a: pytest.fail('invalid PID signalled'))
    assert schedule.pid_alive(pid) is False


@pytest.mark.parametrize('reaped,alive', [(123, False), (0, True)])
def test_own_child_is_checked_and_reaped_without_blocking(monkeypatch, reaped, alive):
    calls = []
    monkeypatch.setattr(schedule.os, 'waitpid', lambda *args: calls.append(args) or (reaped, 0))
    monkeypatch.setattr(schedule.os, 'kill', lambda *a: pytest.fail('own child needs no ps probe'))
    assert schedule.pid_alive(123) is alive
    assert calls == [(123, schedule.os.WNOHANG)]


def foreign_pid(monkeypatch):
    def not_child(*args):
        raise ChildProcessError()
    monkeypatch.setattr(schedule.os, 'waitpid', not_child)
    monkeypatch.setattr(schedule.os, 'kill', lambda *a: None)


@pytest.mark.parametrize('stat,code,alive', [('Z', 0, False), ('Z+', 0, False),
                                          ('S', 0, True), ('R+', 0, True),
                                          ('', 1, False), ('', 2, True)])
def test_foreign_process_state(monkeypatch, stat, code, alive):
    foreign_pid(monkeypatch)
    def ps(argv, **kw):
        assert argv == ['ps', '-p', '123', '-o', 'stat=']
        assert kw['timeout'] == 2
        return SimpleNamespace(returncode=code, stdout=stat)
    monkeypatch.setattr(schedule.subprocess, 'run', ps)
    assert schedule.pid_alive(123) is alive


@pytest.mark.parametrize('error,alive', [(ProcessLookupError(), False), (PermissionError(), True)])
def test_kill_probe_errors(monkeypatch, error, alive):
    foreign_pid(monkeypatch)
    def kill(*args):
        raise error
    monkeypatch.setattr(schedule.os, 'kill', kill)
    assert schedule.pid_alive(123) is alive


def test_ps_timeout_does_not_authorize_duplicate_run(monkeypatch):
    foreign_pid(monkeypatch)
    def timeout(*a, **kw):
        raise subprocess.TimeoutExpired('ps', 2)
    monkeypatch.setattr(schedule.subprocess, 'run', timeout)
    assert schedule.pid_alive(123) is True


@pytest.mark.parametrize('manual', [False, True])
def test_zombie_allows_manual_recovery_without_automatic_retry(root, monkeypatch, manual):
    foreign_pid(monkeypatch)
    monkeypatch.setattr(schedule.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0, stdout='Z'))
    target = '20261013'
    release.atomic_json(schedule.record_path(root, target), {
        'schema_version': 2, 'trade_date': target, 'status': 'running',
        'pid': 123, 'attempts': 1, 'history': [], 'reason': 'PROCESS_RUNNING'})
    calls = []
    launch = lambda *args: calls.append(args) or 456
    if manual:
        result = schedule.manual_run(root, trade_date=target, at=at('2026-10-14T09:00'), launch=launch)
        assert result['status'] == 'running' and result['pid'] == 456
        assert result['attempts'] == 2 and len(calls) == 1
    else:
        result = schedule.scheduled_occurrence(root, target, at('2026-10-14T08:30'),
                                               due=False, at=at('2026-10-14T09:00'), launch=launch)
        assert result['status'] == 'failed' and result['pid'] is None
        assert result['reason'] == 'BATCH_NOT_PUBLISHED' and not calls
