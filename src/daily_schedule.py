"""AKsignal Daily trigger APIs; scheduled misses never retry automatically."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, time
import fcntl
import json
import os
from pathlib import Path
import subprocess

from src.common import batch_release as release
from src.common.trading_calendar import default_calendar, shanghai_now
from src.common.paths import project_root

SCHEDULE_TIME = time(8, 30)
SCHEDULE_POLL_SECONDS = 5


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


def spawn_batch(root: Path, target: str, offline: bool, log: Path, *, restart: bool = False) -> int:
    flags = '--require-complete --restart' if restart else '--require-complete'
    with log.open('ab') as stream:
        child = subprocess.Popen(
            ['make', 'run-day-offline' if offline else 'run-day',
             f'RUN_DATE={target}', f'RUN_FLAGS={flags}'],
            cwd=root, stdin=subprocess.DEVNULL, stdout=stream,
            stderr=subprocess.STDOUT, start_new_session=True)
    # This launcher is short-lived. The native Runner owns the business lock,
    # checkpoint and child cleanup; the Scheduler never waits for this batch.
    return child.pid


def record_path(root: Path, target: str) -> Path:
    return root / '.run_day' / 'scheduler' / f'{target}.json'


def _save(root: Path, target: str, record: dict, at: datetime) -> dict:
    record['checked_at'] = at.isoformat()
    release.atomic_json(record_path(root, target), record)
    release.atomic_json(root / '.run_day' / 'scheduler' / 'latest.json', record)
    return record


def _load(root: Path, target: str) -> dict:
    path = record_path(root, target)
    if not path.exists():
        return {'schema_version': 2, 'trade_date': target, 'attempts': 0,
                'history': [], 'status': 'pending'}
    record = json.loads(path.read_text(encoding='utf-8'))
    if (record.get('schema_version') not in (1, 2) or record.get('trade_date') != target
            or not isinstance(record.get('attempts'), int) or record['attempts'] < 0):
        raise ValueError('触发记录格式不一致')
    record.setdefault('history', [])
    return record


def _publication(root: Path, target: str, record: dict, at: datetime) -> dict | None:
    try:
        publication = release.published_batch(root, target)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        record.update(status='blocked', reason='PUBLICATION_INVALID', error=str(exc))
        return _save(root, target, record, at)
    if not publication:
        return None
    if publication['result'].get('status') not in ('CONFIRMED', 'COMPLETE'):
        if record.get('status') == 'blocked' and record.get('reason') == 'PUBLICATION_NOT_COMPLETE' \
                and record.get('publication') == publication:
            return record
        record.update(status='blocked', reason='PUBLICATION_NOT_COMPLETE', publication=publication,
                      error='已有非完整正式结果，等待人工检查')
    else:
        if record.get('status') == 'published' and record.get('publication') == publication:
            return record
        record.update(status='published', publication=publication, reason='ALREADY_PUBLISHED', error='')
    return _save(root, target, record, at)


def _existing_process(root: Path, target: str, record: dict, at: datetime,
                      alive, *, manual: bool) -> dict | None:
    if record.get('status') not in ('starting', 'running'):
        return None
    if alive(record.get('pid')):
        if record.get('reason') == 'PROCESS_RUNNING':
            return record
        record.update(reason='PROCESS_RUNNING')
        return _save(root, target, record, at)
    record.update(status='failed', pid=None, reason='BATCH_NOT_PUBLISHED',
                  error='调度进程已退出但日报未发布；等待面板手动恢复')
    saved = _save(root, target, record, at)
    return None if manual else saved


def _launch(root: Path, target: str, record: dict, *, offline: bool,
            source: str, at: datetime, launch, restart: bool = False) -> dict:
    record.update(schema_version=2, attempts=int(record.get('attempts', 0)) + 1,
                  offline=offline, source=source, triggered_at=at.isoformat(),
                  calendar_sha256=default_calendar(root).digest, reason='TRIGGERED',
                  error='', pid=None, status='starting',
                  maintenance_restart=restart)
    record.setdefault('history', []).append({
        'attempt': record['attempts'], 'source': source,
        'triggered_at': at.isoformat(), 'status': 'starting',
        'maintenance_restart': restart,
    })
    log = root / '.run_day' / 'scheduler' / f'{target}-attempt-{record["attempts"]}.log'
    record['log'] = str(log)
    _save(root, target, record, at)
    try:
        if restart and launch is spawn_batch:
            pid = launch(root, target, offline, log, restart=True)
        else:
            pid = launch(root, target, offline, log)
    except OSError as exc:
        record.update(status='failed', reason='SPAWN_FAILED', error=str(exc))
        record['history'][-1].update(status='failed', error=str(exc))
    else:
        record.update(pid=pid, status='running')
        record['history'][-1].update(status='running', pid=pid)
    return _save(root, target, record, at)


def scheduled_occurrence(root: Path, target: str, occurrence: datetime, *, due: bool,
                         at: datetime, launch=spawn_batch, alive=pid_alive) -> dict:
    """Run only on the scheduled minute; record missed/failed attempts for manual action."""
    with trigger_lock(root) as acquired:
        if not acquired:
            return {'status': 'busy', 'reason': 'TRIGGER_LOCK', 'trade_date': target}
        try:
            record = _load(root, target)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            return {'status': 'blocked', 'trade_date': target,
                    'reason': 'TRIGGER_RECORD_INVALID', 'error': str(exc)}
        record.setdefault('scheduled_at', occurrence.isoformat())
        publication = _publication(root, target, record, at)
        if publication:
            return publication
        active = _existing_process(root, target, record, at, alive, manual=False)
        if active:
            return active
        if record.get('status') in ('waiting', 'exhausted'):
            record.update(status='failed', reason='MANUAL_RECOVERY_REQUIRED',
                          error='旧版调度等待状态已停止自动重试；等待面板手动恢复')
            return _save(root, target, record, at)
        if record.get('status') in ('missed', 'failed', 'blocked', 'published'):
            return record
        if not due:
            record.update(schema_version=2, status='missed', source='scheduled',
                          reason='MISSED_SCHEDULE',
                          error='错过计划时点；等待面板手动运行')
            return _save(root, target, record, at)
        if release.lock_active(root):
            record.update(status='missed', source='scheduled', reason='BUSINESS_LOCK',
                          error='已有 Daily Run 正在运行；等待面板检查')
            return _save(root, target, record, at)
        try:
            checkpoint = release.read_checkpoint(root, target, False)
        except (OSError, ValueError) as exc:
            record.update(status='blocked', reason='CHECKPOINT_INVALID', error=str(exc))
            return _save(root, target, record, at)
        if checkpoint and checkpoint.get('signature') != release.implementation_signature(root):
            record.update(status='blocked', reason='RESTART_REQUIRED',
                          error='代码/config 已变化，需面板维护恢复')
            return _save(root, target, record, at)
        return _launch(root, target, record, offline=False, source='scheduled', at=at, launch=launch)


def actionable_target(root: Path, at: datetime | None = None) -> str:
    """Prefer the newest unresolved target so the panel's no-arg Run acts on it."""
    at = at or shanghai_now()
    calendar = default_calendar(root)
    current, _ = calendar.scheduled_target(at)
    folder = root / '.run_day' / 'scheduler'
    unresolved = []
    for path in folder.glob('????????.json'):
        if not path.stem.isdigit():
            continue
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
            target = path.stem
            actionable = record.get('status') in ('missed', 'failed', 'waiting', 'exhausted') or (
                record.get('status') == 'blocked' and record.get('reason') == 'RESTART_REQUIRED')
            if record.get('trade_date') != target or target > calendar.complete_day(at) or not actionable:
                continue
            publication = release.published_batch(root, target)
            if publication and publication['result'].get('status') in ('CONFIRMED', 'COMPLETE'):
                continue
            unresolved.append((record.get('scheduled_at', ''), target))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return max(unresolved)[1] if unresolved else current


def scheduled_poll(root: Path, *, at: datetime | None = None,
                   launch=spawn_batch, alive=pid_alive) -> dict:
    """Reconcile elapsed occurrences as MISSED; never catch them up automatically."""
    at = at or shanghai_now()
    from src.common.trading_calendar import SHANGHAI
    local = at.astimezone(SHANGHAI)
    state_path = root / '.run_day' / 'scheduler' / 'agent_state.json'
    try:
        calendar = default_calendar(root)
        if not calendar.start <= local.date() <= calendar.end:
            raise ValueError('交易日历缺失覆盖或已过期')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result = {'status': 'blocked', 'reason': 'CALENDAR_UNAVAILABLE',
                  'error': str(exc), 'checked_at': local.isoformat()}
        release.atomic_json(state_path, {'schema_version': 1, 'pid': os.getpid(),
                                         'status': 'blocked', 'last_checked_at': local.isoformat(),
                                         'error': str(exc)[:1000]})
        return result
    try:
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        previous = datetime.fromisoformat(state['last_checked_at']) if state.get('last_checked_at') else None
    except (OSError, ValueError, KeyError, TypeError):
        state, previous = {}, None
    first_date = previous.astimezone(SHANGHAI).date() if previous else local.date()
    results = []
    current = first_date
    while current <= local.date():
        occurrence = datetime.combine(current, SCHEDULE_TIME, SHANGHAI)
        crossed = previous is None or previous.astimezone(SHANGHAI) < occurrence
        if crossed and occurrence <= local:
            target = calendar.complete_day(occurrence)
            due = current == local.date() and local.hour == 8 and local.minute == 30
            results.append(scheduled_occurrence(root, target, occurrence, due=due,
                                                at=local, launch=launch, alive=alive))
        current += timedelta(days=1)
    latest_target, latest_occurrence = calendar.scheduled_target(local)
    if (latest_occurrence.date() == local.date() and latest_occurrence <= local and not any(
            item.get('trade_date') == latest_target and
            item.get('scheduled_at') == latest_occurrence.isoformat()
            for item in results)):
        due = local.hour == 8 and local.minute == 30 and latest_occurrence.date() == local.date()
        results.append(scheduled_occurrence(root, latest_target, latest_occurrence,
                                            due=due, at=local, launch=launch, alive=alive))
    previous_status = state.get('status')
    state.update(schema_version=1, pid=os.getpid(), status='running',
                 started_at=state.get('started_at') or local.isoformat())
    state_changed = previous_status != 'running' or previous is None
    if results:
        last = results[-1]
        occurrence_at = last.get('scheduled_at') or last.get('triggered_at')
        state_changed = state_changed or (
            state.get('last_occurrence') != occurrence_at
            or state.get('last_occurrence_status') != last.get('status')
            or state.get('last_occurrence_trade_date') != last.get('trade_date'))
        state.update(last_occurrence=occurrence_at,
                     last_occurrence_status=last.get('status'),
                     last_occurrence_trade_date=last.get('trade_date'))
    heartbeat_due = previous is None or (local - previous.astimezone(SHANGHAI)) >= timedelta(seconds=30)
    if state_changed or heartbeat_due:
        state['last_checked_at'] = local.isoformat()
        release.atomic_json(state_path, state)
    return {'status': 'checked', 'at': local.isoformat(), 'occurrences': results}


def manual_run(root: Path, *, offline: bool = False, at: datetime | None = None,
               trade_date: str | None = None, restart: bool = False,
               launch=spawn_batch, alive=pid_alive) -> dict:
    """Explicit user-triggered run/resume, independent of the schedule clock."""
    at = at or shanghai_now()
    try:
        target = trade_date or actionable_target(root, at)
        datetime.strptime(target, '%Y%m%d')
        calendar = default_calendar(root)
        if target not in {d.strftime('%Y%m%d') for d in calendar.days}:
            raise ValueError('目标日期不在已核验交易日历中')
        if target > calendar.complete_day(at):
            raise ValueError('目标日期尚未成为完整交易日')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {'status': 'blocked', 'reason': 'CALENDAR_UNAVAILABLE',
                'error': str(exc), 'checked_at': at.isoformat()}
    with trigger_lock(root) as acquired:
        if not acquired:
            return {'status': 'busy', 'reason': 'TRIGGER_LOCK', 'trade_date': target}
        try:
            record = _load(root, target)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            return {'status': 'blocked', 'trade_date': target,
                    'reason': 'TRIGGER_RECORD_INVALID', 'error': str(exc)}
        publication = _publication(root, target, record, at)
        if publication:
            return publication
        if record.get('status') == 'published':
            record.update(status='blocked', reason='PUBLISHED_RECEIPT_MISSING',
                          error='调度记录称已发布，但正式回执缺失；请人工检查')
            return _save(root, target, record, at)
        active = _existing_process(root, target, record, at, alive, manual=True)
        if active:
            return active
        if record.get('status') == 'blocked' and record.get('reason') != 'RESTART_REQUIRED':
            return _save(root, target, record, at)
        if release.lock_active(root):
            record.update(status='running', reason='BUSINESS_LOCK', error='已有 Daily Run 正在运行')
            return _save(root, target, record, at)
        # Manual production runs use the requested mode, not a failed legacy
        # occurrence's cache-only mode. Offline remains an explicit CLI option.
        selected_offline = offline
        try:
            checkpoint = release.read_checkpoint(root, target, selected_offline)
        except (OSError, ValueError) as exc:
            record.update(status='blocked', reason='CHECKPOINT_INVALID', error=str(exc))
            return _save(root, target, record, at)
        signature_changed = bool(checkpoint and checkpoint.get('signature') != release.implementation_signature(root))
        if restart and not signature_changed:
            record.update(status='blocked', reason='RESTART_NOT_REQUIRED',
                          error='当前没有代码/config 签名变化；普通手动恢复即可')
            return _save(root, target, record, at)
        restart = restart or signature_changed
        return _launch(root, target, record, offline=selected_offline,
                       source='manual_restart' if restart else 'manual', at=at,
                       launch=launch, restart=restart)


def main():
    parser = argparse.ArgumentParser(description='AKsignal Daily scheduled check and manual run')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--scheduled-check', action='store_true', help='定时单轮检查；错过时只记 MISSED')
    mode.add_argument('--manual-run', action='store_true', help='人工运行或恢复当前目标交易日')
    mode.add_argument('--plan', action='store_true', help='只读调度计划，不触发也不写记录')
    parser.add_argument('--date', help='人工运行指定 trade_date YYYYMMDD')
    parser.add_argument('--offline', action='store_true', help='仅调用缓存业务批次')
    parser.add_argument('--restart', action='store_true', help='显式维护重启；仅签名变化时可用')
    args = parser.parse_args()
    if (args.date or args.offline or args.restart) and not args.manual_run:
        parser.error('--date/--offline/--restart 仅用于 --manual-run')
    root = project_root()
    if args.plan:
        at = shanghai_now()
        target, occurrence = default_calendar(root).scheduled_target(at)
        if occurrence <= at:
            occurrence += timedelta(days=1)
            target = default_calendar(root).complete_day(occurrence)
        result = {'trade_date': target, 'scheduled_at': occurrence.isoformat(),
                  'timezone': 'Asia/Shanghai', 'automatic_retry': False,
                  'missed_policy': 'manual_only'}
    elif args.scheduled_check:
        result = scheduled_poll(root)
    else:
        result = manual_run(root, offline=args.offline, trade_date=args.date, restart=args.restart)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
