"""AKsignal-owned macOS LaunchAgent and resident schedule poller."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import re
import signal
import subprocess
import sys
import time

from src.common.paths import project_root
from src.common.trading_calendar import shanghai_now
from src.daily_schedule import SCHEDULE_POLL_SECONDS, scheduled_poll
from src.common.sleep_guard import SleepGuard, waiting_seconds

LABEL = 'com.aksignal.daily-scheduler'
AGENT_FILE = f'{LABEL}.plist'
_STOP = False


def agent_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / 'Library' / 'LaunchAgents' / AGENT_FILE


def render_agent(root: Path, *, home: Path | None = None,
                 python: str | None = None) -> bytes:
    home = home or Path.home()
    python = python or str(root / '.venv' / 'bin' / 'python')
    log = root / '.run_day' / 'scheduler' / 'agent.log'
    payload = {
        'Label': LABEL,
        'ProgramArguments': [python, '-m', 'src.daily_scheduler', '--daemon'],
        'WorkingDirectory': str(root),
        'RunAtLoad': True,
        'KeepAlive': True,
        'ThrottleInterval': 10,
        'StandardOutPath': str(log),
        'StandardErrorPath': str(log),
        'EnvironmentVariables': {
            'PATH': f'{home}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin',
            'PYTHONUNBUFFERED': '1',
        },
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=False)


def _launchctl(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(['launchctl', *args], text=True, capture_output=True,
                          check=check)


def _domain(uid: int | None = None) -> str:
    return f'gui/{os.getuid() if uid is None else uid}'


def _loaded(uid: int | None = None) -> tuple[bool, str]:
    result = _launchctl(['print', f'{_domain(uid)}/{LABEL}'], check=False)
    output = (result.stdout or '') + (result.stderr or '')
    return result.returncode == 0, output


def start_agent(*, home: Path | None = None, uid: int | None = None) -> dict:
    plist = agent_path(home)
    if not plist.is_file():
        raise FileNotFoundError(f'LaunchAgent 未安装：{plist}')
    loaded, output = _loaded(uid)
    domain = _domain(uid)
    if loaded:
        pid_match = re.search(r'^\s*pid = (\d+)', output, re.MULTILINE)
        if pid_match:
            return {'status': 'already_running', 'label': LABEL,
                    'pid': int(pid_match.group(1))}
        _launchctl(['kickstart', f'{domain}/{LABEL}'])
        action = 'started'
    else:
        _launchctl(['bootstrap', domain, str(plist)])
        action = 'started'
    return {'status': action, 'label': LABEL}


def _mark_stopped(root: Path | None) -> None:
    if root is None:
        return
    from src.common import batch_release as release
    path = root / '.run_day' / 'scheduler' / 'agent_state.json'
    try:
        payload = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError, TypeError):
        payload = {}
    payload.update(schema_version=1, status='stopped', pid=None,
                   stopped_at=shanghai_now().isoformat())
    release.atomic_json(path, payload)


def stop_agent(*, home: Path | None = None, uid: int | None = None,
               root: Path | None = None) -> dict:
    plist = agent_path(home)
    loaded, _ = _loaded(uid)
    if loaded:
        _launchctl(['bootout', _domain(uid), str(plist)])
        _mark_stopped(root)
        return {'status': 'stopped', 'label': LABEL}
    _mark_stopped(root)
    return {'status': 'already_stopped', 'label': LABEL}


def install_agent(root: Path, *, home: Path | None = None, uid: int | None = None,
                  python: str | None = None) -> dict:
    if sys.platform != 'darwin':
        raise OSError('AKsignal Scheduler LaunchAgent 仅支持 macOS')
    plist = agent_path(home)
    plist.parent.mkdir(parents=True, exist_ok=True)
    log_dir = root / '.run_day' / 'scheduler'
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    log_dir.chmod(0o700)
    temp = plist.with_suffix('.plist.tmp')
    temp.write_bytes(render_agent(root, home=home, python=python))
    temp.chmod(0o600)
    os.replace(temp, plist)
    if _loaded(uid)[0]:
        _launchctl(['bootout', _domain(uid), str(plist)], check=False)
    _launchctl(['bootstrap', _domain(uid), str(plist)])
    return {'status': 'installed', 'label': LABEL, 'plist': str(plist)}


def uninstall_agent(*, home: Path | None = None, uid: int | None = None,
                    root: Path | None = None) -> dict:
    stop_agent(home=home, uid=uid, root=root)
    path = agent_path(home)
    path.unlink(missing_ok=True)
    return {'status': 'uninstalled', 'label': LABEL}


def agent_status(*, home: Path | None = None, uid: int | None = None) -> dict:
    loaded, output = _loaded(uid)
    pid_match = re.search(r'^\s*pid = (\d+)', output, re.MULTILINE)
    state_match = re.search(r'^\s*state = ([^\r\n]+)', output, re.MULTILINE)
    return {
        'label': LABEL,
        'installed': agent_path(home).is_file(),
        'loaded': loaded,
        'state': state_match.group(1) if state_match else ('not_loaded' if not loaded else 'loaded'),
        'pid': int(pid_match.group(1)) if pid_match else None,
    }


def daemon(root: Path, *, poll_seconds: int = SCHEDULE_POLL_SECONDS) -> None:
    global _STOP

    def request_stop(_signum, _frame):
        global _STOP
        _STOP = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    from src.common import batch_release as release
    state_path = root / '.run_day' / 'scheduler' / 'agent_state.json'
    try:
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
    except (OSError, ValueError, TypeError):
        state = {}
    state.update(schema_version=1, pid=os.getpid(), status='running',
                 started_at=shanghai_now().isoformat())
    release.atomic_json(state_path, state)
    last_logged = None
    sleep_guard = SleepGuard()
    while not _STOP:
        try:
            at = shanghai_now()
            remaining = waiting_seconds(at)
            protected = sleep_guard.start(remaining) if remaining else False
            if not remaining:
                sleep_guard.stop()
            result = scheduled_poll(root, at=at)
            current_state = json.loads(state_path.read_text())
            current_state['sleepProtection'] = {
                'active': protected, 'waitingWindow': '08:00–08:31 Asia/Shanghai',
                'pid': sleep_guard.process.pid if protected else None,
            }
            release.atomic_json(state_path, current_state)
            occurrences = result.get('occurrences', [])
            if occurrences:
                latest = occurrences[-1]
                log_key = (latest.get('trade_date'), latest.get('status'), latest.get('reason'))
                if log_key != last_logged:
                    print(json.dumps(result, ensure_ascii=False), flush=True)
                last_logged = log_key
            elif result.get('status') != 'checked':
                print(json.dumps(result, ensure_ascii=False), flush=True)
        except Exception as exc:  # Keep launchd supervision alive; state is fail-closed.
            try:
                current_state = json.loads(state_path.read_text()) if state_path.exists() else state
            except (OSError, ValueError, TypeError):
                current_state = state
            checked_at = shanghai_now().isoformat()
            payload = {**current_state, 'schema_version': 1, 'pid': os.getpid(), 'status': 'error',
                       'checked_at': checked_at, 'last_checked_at': checked_at,
                       'error': str(exc)[:1000]}
            release.atomic_json(state_path, payload)
            print(json.dumps(payload, ensure_ascii=False), file=sys.stderr, flush=True)
        for _ in range(poll_seconds * 10):
            if _STOP:
                break
            time.sleep(0.1)
    sleep_guard.stop()
    try:
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
    except (OSError, ValueError, TypeError):
        state = {}
    state.update(schema_version=1, pid=None, status='stopped',
                 stopped_at=shanghai_now().isoformat())
    release.atomic_json(state_path, state)


def main() -> None:
    parser = argparse.ArgumentParser(description='Manage the AKsignal Daily Scheduler LaunchAgent')
    parser.add_argument('action', choices=('install', 'start', 'stop', 'restart', 'uninstall', 'status'), nargs='?')
    parser.add_argument('--daemon', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    root = project_root()
    if args.daemon:
        daemon(root)
        return
    if not args.action:
        parser.error('需指定管理动作或 --daemon')
    actions = {
        'install': lambda: install_agent(root),
        'start': start_agent,
        'stop': lambda: stop_agent(root=root),
        'restart': lambda: (stop_agent(root=root), start_agent())[-1],
        'uninstall': lambda: uninstall_agent(root=root),
        'status': agent_status,
    }
    result = actions[args.action]()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
