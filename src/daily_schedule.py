"""Bounded nonblocking Daily trigger; computations remain in the native runner."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta
import fcntl
import json
import os
from pathlib import Path
import subprocess

from src.common import batch_release as release
from src.common.trading_calendar import default_calendar, shanghai_now
from src.common.paths import project_root

MAX_ATTEMPTS = 3
BACKOFF_MINUTES = (30, 90)


@contextmanager
def trigger_lock(root: Path):
    folder = root / '.run_day' / 'scheduler'
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / 'trigger.lock').open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def spawn_batch(root: Path, target: str, offline: bool, log: Path) -> int:
    with log.open('ab') as stream:
        child = subprocess.Popen(
            ['make', 'run-day-offline' if offline else 'run-day',
             f'RUN_DATE={target}', 'RUN_FLAGS=--require-complete'],
            cwd=root, stdin=subprocess.DEVNULL, stdout=stream,
            stderr=subprocess.STDOUT, start_new_session=True)
    # This launcher is short-lived. The native Runner owns the business lock,
    # checkpoint and child cleanup; the Scheduler never waits for this batch.
    return child.pid


def tick(root: Path, *, offline: bool, at: datetime | None = None,
         launch=spawn_batch, alive=pid_alive) -> dict:
    at = at or shanghai_now()
    with trigger_lock(root) as acquired:
        if not acquired:
            return {'status': 'busy', 'reason': 'TRIGGER_LOCK'}
        folder = root / '.run_day' / 'scheduler'
        try:
            calendar = default_calendar(root)
            target, occurrence = calendar.scheduled_target(at)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            result = {'status': 'blocked', 'reason': 'CALENDAR_UNAVAILABLE',
                      'error': str(exc), 'checked_at': at.isoformat()}
            release.atomic_json(folder / 'latest.json', result)
            return result
        path = folder / f'{target}.json'  # Shared across online/offline modes.
        try:
            record = json.loads(path.read_text()) if path.exists() else {
                'schema_version': 1, 'trade_date': target, 'attempts': 0,
                'scheduled_at': occurrence.isoformat(), 'status': 'pending'}
            if (record.get('schema_version') != 1 or record.get('trade_date') != target
                    or not isinstance(record.get('attempts'), int) or record['attempts'] < 0):
                raise ValueError('触发记录格式不一致')
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            result = {'status': 'blocked', 'trade_date': target, 'reason': 'TRIGGER_RECORD_INVALID',
                      'error': str(exc), 'checked_at': at.isoformat()}
            release.atomic_json(folder / 'latest.json', result)
            return result  # Preserve corrupt evidence; never reset the retry budget.

        def save():
            record['checked_at'] = at.isoformat()
            release.atomic_json(path, record)
            release.atomic_json(folder / 'latest.json', record)
            return record

        try:
            publication = release.published_batch(root, target)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            record.update(status='blocked', reason='PUBLICATION_INVALID', error=str(exc))
            return save()
        if publication:
            if publication['result'].get('status') not in ('CONFIRMED', 'COMPLETE'):
                record.update(status='blocked', reason='PUBLICATION_NOT_COMPLETE', publication=publication)
                return save()
            record.update(status='published', publication=publication, reason='ALREADY_PUBLISHED')
            return save()
        # Any manual batch uses the same business lock. Keep the next attempt
        # available instead of counting contention as a data-source failure.
        if release.lock_active(root):
            record.update(reason='BUSINESS_LOCK')
            return save()
        if record.get('status') in ('starting', 'running'):
            if alive(record.get('pid')):
                record.update(reason='PROCESS_RUNNING')
                return save()
            try:
                checkpoint = release.read_checkpoint(root, target, record.get('offline', offline))
            except (OSError, ValueError) as exc:
                record.update(status='blocked', reason='CHECKPOINT_INVALID', error=str(exc))
                return save()
            record.update(status='waiting', pid=None, reason='BATCH_NOT_PUBLISHED',
                          error=(checkpoint or {}).get('error') or '进程结束但无正式发布回执',
                          next_attempt_at=(at + timedelta(minutes=BACKOFF_MINUTES[
                              min(max(record['attempts'] - 1, 0), 1)])).isoformat())
            return save()
        if record['attempts'] >= MAX_ATTEMPTS:
            record.update(status='exhausted', reason='RETRY_LIMIT')
            return save()
        if record.get('status') == 'blocked':
            # Invalid receipts need explicit repair; never silently create a new run.
            return save()
        if record.get('next_attempt_at') and at < datetime.fromisoformat(record['next_attempt_at']):
            return save()
        try:
            checkpoint = release.read_checkpoint(root, target, offline)
        except (OSError, ValueError) as exc:
            record.update(status='blocked', reason='CHECKPOINT_INVALID', error=str(exc))
            return save()
        if checkpoint and checkpoint.get('signature') != release.implementation_signature(root):
            record.update(status='blocked', reason='RESTART_REQUIRED', error='代码/config 已变化，需显式维护')
            return save()
        record.update(status='starting', attempts=record['attempts'] + 1,
                      offline=offline, triggered_at=at.isoformat(),
                      calendar_sha256=calendar.digest, reason='TRIGGERED', error='', pid=None)
        log = folder / f'{target}-attempt-{record["attempts"]}.log'
        record['log'] = str(log)
        save()  # Durable intent before spawn; interrupted spawn consumes one attempt.
        try:
            record.update(pid=launch(root, target, offline, log), status='running')
        except OSError as exc:
            record.update(status='waiting', reason='SPAWN_FAILED', error=str(exc),
                          next_attempt_at=(at + timedelta(minutes=30)).isoformat())
        return save()


def main():
    parser = argparse.ArgumentParser(description='08:30 Daily 非阻塞触发与有限恢复（不默认常驻）')
    parser.add_argument('--tick', action='store_true', help='由现有 Scheduler 调用一轮检查/触发')
    parser.add_argument('--offline', action='store_true', help='仅调用缓存业务批次')
    parser.add_argument('--plan', action='store_true', help='只读日期与调度规则，不触发也不写记录')
    args = parser.parse_args()
    root = project_root()
    if args.plan:
        target, occurrence = default_calendar(root).scheduled_target(shanghai_now())
        result = {'trade_date': target, 'scheduled_at': occurrence.isoformat(),
                  'timezone': 'Asia/Shanghai', 'max_attempts': MAX_ATTEMPTS,
                  'backoff_minutes': BACKOFF_MINUTES, 'offline': args.offline}
    elif args.tick:
        result = tick(root, offline=args.offline)
    else:
        parser.error('需显式 --plan 或 --tick')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
