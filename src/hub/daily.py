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


def public_log_line(value: str) -> str:
    return re.sub(r'(?<!\w)/(?:Users|private|tmp|var|home|opt|Volumes)/[^\s；，]+',
                  '[local log]', str(value))[:500]


def diagnostic_log_lines(root: Path, record: dict, *, max_lines: int = 20) -> list[str]:
    """Return a small, path-scrubbed tail from the registered batch log only."""
    raw_path = record.get('log')
    if not isinstance(raw_path, str) or not raw_path:
        return []
    root = root.resolve()
    allowed = (root / '.run_day' / 'scheduler').resolve()
    path = Path(raw_path)
    if not path.is_absolute():
        path = root / path
    try:
        path = path.resolve(strict=True)
        path.relative_to(allowed)
        if not path.is_file():
            return []
        lines = path.read_bytes()[-16_384:].decode('utf-8', errors='replace').splitlines()
    except (OSError, ValueError):
        return []
    clean = []
    for line in lines[-max_lines:]:
        value = public_log_line(line).replace('\x00', '')
        if value.strip():
            clean.append(value)
    return clean


def view(root: Path, at: datetime | None = None) -> dict:
    at = at or shanghai_now()
    result = {'lastRun': {'status': 'IDLE', 'startedAt': None, 'finishedAt': None},
              'details': {}, 'artifacts': []}
    detail = result['details']
    detail['timezone'] = 'Asia/Shanghai'
    target, state, record = None, {}, {}
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
                published = release.published_batch(root, item.stem)
                if (record.get('status') == 'published' and published
                        and published['result'].get('status') == 'PARTIAL'):
                    actionable = True
                if not actionable:
                    continue
                if not published or published['result'].get('status') not in ('CONFIRMED', 'COMPLETE', 'PARTIAL'):
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
        elif status == 'blocked' and record.get('reason') == 'RESTART_REQUIRED':
            result['lastRun']['status'] = 'FAILED'
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
        if result['lastRun']['status'] in ('FAILED', 'WAITING'):
            detail['error'] += '；点击手动运行 / 恢复：先联网更新数据，检查点不兼容时自动从头运行。'
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

        detail['logLines'] = diagnostic_log_lines(root, record)

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
            if not publication or publication != receipt or publication['result'].get('status') not in ('CONFIRMED', 'COMPLETE', 'PARTIAL'):
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
            industry_module = (facts.get('modules') or {}).get('industry_rotation') or {}
            if industry_module.get('status') == 'unavailable':
                detail['metrics'] = [{'label': '行业观察',
                                      'value': f"不可用（{industry_module.get('verified_count', 0)}/{industry_module.get('expected_count', 0)} 已核验）"}]
            else:
                primary = int(frame.source_status.eq('primary').sum()) if 'source_status' in frame else 0
                detail['metrics'] = [{'label': '行业主源覆盖', 'value': f"{primary}/{len(frame)}"}]
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
    # Independent research receipt: never changes formal Daily status or quality.
    try:
        from src.hub.baskets import comparison_view
        preview, artifact = comparison_view(root)
        if preview:
            detail['trendComparison'] = preview
            result['artifacts'].append(artifact)
    except (OSError, ValueError, KeyError, TypeError):
        detail['trendComparisonError'] = '研究走势快照未通过核验，请重新生成；不影响正式日报状态。'
    return result


def main():
    print(json.dumps(view(project_root()), ensure_ascii=False))


if __name__ == '__main__':
    main()
