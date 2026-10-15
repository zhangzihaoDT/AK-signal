from datetime import datetime
import os

import pytest

from src.common import sleep_guard as power
from src.common.trading_calendar import SHANGHAI


@pytest.mark.parametrize('clock,seconds', [
    ('07:59:59', 0), ('08:00:00', 1860), ('08:10:00', 1260),
    ('08:30:59', 1), ('08:31:00', 0), ('09:00:00', 0),
])
def test_waiting_window_is_bounded(clock, seconds):
    at = datetime.fromisoformat(f'2026-10-16T{clock}').replace(tzinfo=SHANGHAI)
    assert power.waiting_seconds(at) == seconds


def fake_process(monkeypatch):
    calls = []
    class Process:
        pid = 987
        returncode = None
        def poll(self): return self.returncode
        def terminate(self): self.returncode = -15
        def wait(self, **kwargs): return self.returncode
    def launch(args, **kwargs):
        p = Process()
        calls.append((args, p))
        return p
    monkeypatch.setattr(power.sys, 'platform', 'darwin')
    monkeypatch.setattr(power.subprocess, 'Popen', launch)
    return calls


def test_waiting_guard_reuses_assertion_and_releases(monkeypatch):
    calls = fake_process(monkeypatch)
    guard = power.SleepGuard()
    assert guard.start(1260)
    assert guard.start(1255)
    assert len(calls) == 1
    assert calls[0][0] == ['/usr/bin/caffeinate', '-i', '-s', '-w', str(os.getpid()), '-t', '1260']
    guard.stop()
    assert calls[0][1].returncode == -15
    assert guard.process is None


def test_batch_releases_on_failure_and_has_no_waiting_timeout(monkeypatch):
    calls = fake_process(monkeypatch)
    with pytest.raises(ValueError):
        with power.batch_sleep_guard():
            assert '-t' not in calls[0][0]
            raise ValueError('failed batch')
    assert calls[0][1].returncode == -15


def test_other_platform_does_not_spawn(monkeypatch):
    monkeypatch.setattr(power.sys, 'platform', 'linux')
    monkeypatch.setattr(power.subprocess, 'Popen', lambda *a, **k: pytest.fail('macOS only'))
    with power.batch_sleep_guard():
        pass
