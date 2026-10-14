import plistlib
from pathlib import Path

from src import daily_scheduler as agent


def test_launchagent_declares_ak_signal_daemon_and_stable_paths(tmp_path):
    payload = plistlib.loads(agent.render_agent(tmp_path, home=tmp_path / 'home',
                                                python='/venv/bin/python'))
    assert payload['Label'] == agent.LABEL
    assert payload['ProgramArguments'] == [
        '/venv/bin/python', '-m', 'src.daily_scheduler', '--daemon']
    assert payload['WorkingDirectory'] == str(tmp_path)
    assert payload['RunAtLoad'] is True
    assert payload['KeepAlive'] is True
    assert payload['StandardOutPath'] == str(tmp_path / '.run_day/scheduler/agent.log')


def test_start_bootstraps_unloaded_agent(tmp_path, monkeypatch):
    path = agent.agent_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text('plist')
    calls = []
    monkeypatch.setattr(agent, '_loaded', lambda _uid=None: (False, ''))
    monkeypatch.setattr(agent, '_launchctl', lambda args, **kwargs: calls.append(args))
    assert agent.start_agent(home=tmp_path, uid=501)['status'] == 'started'
    assert calls == [['bootstrap', 'gui/501', str(path)]]


def test_start_is_idempotent_and_stop_boots_agent_out(tmp_path, monkeypatch):
    path = agent.agent_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text('plist')
    calls = []
    monkeypatch.setattr(agent, '_loaded', lambda _uid=None: (True, 'state = running\n pid = 2345\n'))
    monkeypatch.setattr(agent, '_launchctl', lambda args, **kwargs: calls.append(args))
    assert agent.start_agent(home=tmp_path, uid=501) == {
        'status': 'already_running', 'label': agent.LABEL, 'pid': 2345}
    assert agent.stop_agent(home=tmp_path, uid=501)['status'] == 'stopped'
    assert calls == [['bootout', 'gui/501', str(path)]]


def test_status_exposes_only_launchagent_state(tmp_path, monkeypatch):
    path = agent.agent_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text('plist')
    monkeypatch.setattr(agent, '_loaded', lambda _uid=None: (
        True, 'state = running\n pid = 1234\n'))
    assert agent.agent_status(home=tmp_path) == {
        'label': agent.LABEL, 'installed': True, 'loaded': True,
        'state': 'running', 'pid': 1234,
    }
