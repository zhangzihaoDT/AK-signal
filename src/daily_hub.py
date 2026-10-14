"""Read-only Hub view and pinned formal-report manifest. No pipeline execution."""
from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import re

from src.common import batch_release as release
from src.common.paths import project_root
from src.common.trading_calendar import default_calendar, shanghai_now


def public_error(value: str) -> str:
    return re.sub(r'/[^\s；，]+', '[local log]', str(value))[:2000]


def view(root: Path, at: datetime | None = None) -> dict:
    at = at or shanghai_now()
    result = {'lastRun': {'status': 'IDLE', 'startedAt': None, 'finishedAt': None},
              'details': {}, 'artifacts': []}
    detail = result['details']
    detail['timezone'] = 'Asia/Shanghai'
    target, state = None, {}
    try:
        calendar = default_calendar(root)
        scheduled_target, occurrence = calendar.scheduled_target(at)
        from src.daily_schedule import actionable_target
        target = actionable_target(root, at)
        detail['targetDate'] = target
        detail['scheduledTargetDate'] = scheduled_target
        next_occurrence = occurrence if occurrence > at else occurrence + timedelta(days=1)
        scheduler_dir = root / '.run_day/scheduler'
        try:
            agent = json.loads((scheduler_dir / 'agent_state.json').read_text())
        except (OSError, ValueError, TypeError):
            agent = {}
        last_checked = agent.get('last_checked_at')
        heartbeat_age = None
        if last_checked:
            try:
                heartbeat_age = max(0, int((at - datetime.fromisoformat(last_checked)).total_seconds()))
            except (TypeError, ValueError):
                pass
        recent = heartbeat_age is not None and heartbeat_age <= 90 and agent.get('status') == 'running'
        heartbeat_fresh = heartbeat_age is not None and heartbeat_age <= 90
        missed_count = 0
        for item in scheduler_dir.glob('????????.json'):
            if not item.stem.isdigit():
                continue
            try:
                record = json.loads(item.read_text())
                actionable = record.get('status') in ('missed', 'failed', 'waiting', 'exhausted') or (
                    record.get('status') == 'blocked' and record.get('reason') == 'RESTART_REQUIRED')
                if not actionable:
                    continue
                published = release.published_batch(root, item.stem)
                if not published or published['result'].get('status') not in ('CONFIRMED', 'COMPLETE'):
                    missed_count += 1
            except (OSError, ValueError, TypeError):
                continue
        detail['schedule'] = {
            'enabled': recent,
            'agentStatus': ('RUNNING' if recent else 'BLOCKED' if heartbeat_fresh and agent.get('status') == 'blocked'
                            else 'ERROR' if heartbeat_fresh and agent.get('status') == 'error' else 'OFFLINE'),
            'lastCheckedAt': last_checked,
            'heartbeatAgeSeconds': heartbeat_age,
            'nextScheduledAt': next_occurrence.isoformat(),
            'lastOccurrenceAt': agent.get('last_occurrence'),
            'lastOccurrenceStatus': agent.get('last_occurrence_status'),
            'lastOccurrenceTradeDate': agent.get('last_occurrence_trade_date'),
            'manualPendingCount': missed_count,
            'missedPolicy': 'manual_only',
        }
        record_path = root / '.run_day/scheduler' / f'{target}.json'
        record = json.loads(record_path.read_text()) if record_path.exists() else {}
        states = [release.read_checkpoint(root, target, mode) for mode in (False, True)]
        state = max((s for s in states if s), key=lambda s: s.get('updated_at', ''), default={})
        status = record.get('status', '')
        if state.get('status') in ('preparing', 'running', 'publishing') and release.lock_active(root):
            result['lastRun']['status'] = 'RUNNING'
        elif status == 'blocked':
            result['lastRun']['status'] = 'BLOCKED'
        elif status == 'missed':
            result['lastRun']['status'] = 'MISSED'
        elif status in ('failed', 'exhausted'):
            result['lastRun']['status'] = 'FAILED'
        elif state.get('status') in ('preparing', 'running', 'publishing'):
            result['lastRun']['status'] = 'RUNNING' if release.lock_active(root) else 'WAITING'
        elif state.get('status') in ('failed', 'interrupted') or status == 'waiting':
            result['lastRun']['status'] = 'WAITING'
        elif status in ('starting', 'running'):
            result['lastRun']['status'] = 'RUNNING' if release.lock_active(root) else 'WAITING'
        detail.update(runId=state.get('run_id'),
                      error=public_error(state.get('error') or
                                         (record.get('error') if status in ('blocked', 'waiting', 'exhausted', 'failed', 'missed') else '') or ''),
                      reason=record.get('reason'), triggerSource=record.get('source'),
                      retryAt=record.get('next_attempt_at'))
        if result['lastRun']['status'] == 'WAITING' and not detail['error']:
            detail['error'] = '批次未发布，等待原生调度检查或恢复'
        start = state.get('started_at') or record.get('triggered_at')
        finish = state.get('finished_at') if state.get('status') == 'published' else None
        result['lastRun'].update(startedAt=start, finishedAt=finish)
        if start:
            end = finish or (state.get('updated_at') if state.get('status') in ('failed', 'interrupted') else None)
            detail['elapsedSeconds'] = max(0, int(((datetime.fromisoformat(end) if end else at)
                                                  - datetime.fromisoformat(start)).total_seconds()))
        steps = state.get('steps', [])
        if steps:
            from src.daily_run import phase
            index = state.get('current_step', 0)
            detail.update(phase=phase(steps[index]['command']) if index < len(steps) else '统一发布日报',
                          completedSteps=sum(s['status'] == 'completed' for s in steps)
                          + int(state.get('status') == 'published'), totalSteps=len(steps) + 1)

    except (OSError, ValueError, KeyError, TypeError) as exc:
        result['lastRun']['status'] = 'BLOCKED'
        detail['error'] = public_error(exc)

    try:
        # Delivery is independent of a failed current batch/calendar. Preserve
        # access to the latest valid formal release, explicitly dated as such.
        # Resolve the single publication pointer once; no filename inference.
        public = root / 'outputs'
        if public.is_symlink():
            stage = public.resolve()
            receipt = json.loads((stage / 'run_day/publication.json').read_text())
            publication = release.published_batch(root, receipt['trade_date'])
            if not publication or publication != receipt or publication['result'].get('status') not in ('CONFIRMED', 'COMPLETE'):
                raise ValueError('正式发布回执不满足完整性要求')
            facts = publication['result']
            detail['publication'] = {k: publication[k] for k in ('run_id', 'trade_date', 'published_at')}
            detail['quality'] = {k: facts[k] for k in ('status', 'data_complete', 'warnings', 'errors')}
            try:
                from src.market_snapshot import snapshot
                detail['marketSnapshot'] = snapshot(root, stage, publication)
            except (OSError, ValueError, KeyError, TypeError):
                detail['marketSnapshot'] = {'tradeDate': publication['trade_date'], 'runId': publication['run_id'], 'sections': [{'title': '市场状态', 'rows': ['摘要事实不齐，无法可靠概括；请查看正式日报。']}]}

            import pandas as pd
            frame = pd.read_parquet(stage / 'observation' / f"industry_rotation_{publication['trade_date']}.parquet")
            detail['metrics'] = [{'label': '行业主源覆盖',
                                  'value': f"{int(frame.source_status.eq('primary').sum())}/{len(frame)}"}]
            if publication['trade_date'] == target and (not state or state.get('run_id') == publication['run_id']
                                                        or state.get('status') == 'published'):
                result['lastRun']['status'] = 'PUBLISHED'
                result['lastRun']['finishedAt'] = publication['published_at']
                detail['runId'] = publication['run_id']
                detail.update(error='', reason='PUBLISHED', retryAt=None)
            elif state.get('status') == 'published':
                raise ValueError('checkpoint 已发布但目标回执不一致')
            for suffix, mime in (('.html', 'text/html'), ('.md', 'text/markdown')):
                path = stage / 'observation' / f"observation_{publication['trade_date']}{suffix}"
                result['artifacts'].append({'file': str(path), 'name': path.name, 'extension': suffix,
                    'mimeType': mime, 'size': path.stat().st_size, 'digest': release.fingerprint(path),
                    'runId': publication['run_id'], 'targetDate': publication['trade_date']})
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result['lastRun']['status'] = 'BLOCKED'
        detail['error'] = public_error(exc)
        result['artifacts'] = []
    return result


def main():
    print(json.dumps(view(project_root()), ensure_ascii=False))


if __name__ == '__main__':
    main()
