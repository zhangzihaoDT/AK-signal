"""Derive industry change events from two accepted daily snapshots.

Events are facts about cross-date position and stage, not a new indicator:
first entry into RPS15 top 20, first entry that is also 已形成趋势, relapse into
退潮. Only accepted observation snapshots are compared, never re-ranked rows.

The previous snapshot comes from the explicit registry
``config/observation_snapshots.json``. It lists, per date, the accepted facts
file of a *published* batch. Build only writes a candidate inside its private
stage; validate checks that candidate against the batch's own facts; publication
commits it to the registry. Without a registered earlier snapshot the module says
the change is undeterminable; it never backfills from raw history and never
fabricates a previous date.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd

from src.common.paths import config_dir, project_root
from src.observation.contract import INDUSTRY_PREVIEW

EVENT_VERSION = 'industry_change_events_v1'
TOP_RANK = 20


OBSERVATION = Path('observation')


def _published(declared: Path):
    """Map a facts path to the canonical name inside the public observation folder."""
    if declared.parent.name == 'observation':
        return OBSERVATION/declared.name
    return None


def _resolve(declared, root: Path, sha256=None):
    """Resolve a registered facts file that a reader may trust.

    A build registers the file it produced inside the private stage; the stage is
    discarded on publication, so the registered absolute path dies with it. The
    canonical public copy (``root/outputs/observation``, or its publication
    symlink) is the accepted file, but only when it still carries the registered
    digest: a file left in the folder by an unpublished staging or a failed
    batch is not the registered snapshot and must not stand in for it. Files
    outside the public folder (a batch's own staging copy) are trusted as-is.
    """
    declared = Path(declared)
    canonical = _published(declared)
    if canonical is not None:
        public = root/'outputs'/canonical
        if public.exists() and sha256 and file_sha256(public) == sha256:
            return public
        return None
    path = declared if declared.is_absolute() else root/declared
    return path if path.exists() else None




def _relative(declared: Path, root: Path):
    declared = Path(declared)
    canonical = _published(declared)
    if canonical is not None:
        return canonical.as_posix()
    try:
        return declared.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(declared)


def snapshots(root: Path | None = None):
    """Registered accepted snapshots, newest first, plus registry problems.

    The registry lives beside the root that owns it, so a batch evaluated against
    another root never reads or writes this repository's history.
    """
    root = Path(os.path.abspath(str(root or project_root())))
    return _snapshots(root/'config', root)


def _snapshots(config: Path, root: Path):
    root = Path(os.path.abspath(str(root)))
    ledger = Path(config)/'observation_snapshots.json'
    problems, entries = [], []
    if ledger.exists():
        for entry in json.loads(ledger.read_text()).get('industry_rotation', []):
            path = _resolve(entry.get('path', ''), root, entry.get('sha256'))
            if path is None:
                problems.append(f"历史快照缺失：{entry['date']}（已排除）")
                continue
            entries.append((str(entry['date']), path))
    unique = {}
    for date, path in entries:
        unique.setdefault(date, path)
    return sorted(unique.items(), reverse=True), problems


def registry_problems(config: Path, current: str, root: Path) -> list[str]:
    """Problems that make the registry itself self-contradictory for ``current``.

    An entry for the trade date being published can only describe an earlier,
    unfinished attempt at that date: this batch has not registered anything yet,
    and publication rewrites the entry from the batch's own candidate. When such
    an entry no longer resolves, it is self-contradictory: not usable history,
    and not a reason to fail a batch whose own facts are reproducible. It is
    reported so that the downgrade of the change observation stays visible.
    Entries of earlier dates stay a hard failure: silently dropping history
    would change what "previous snapshot" means.
    """
    root = Path(os.path.abspath(str(root)))
    ledger = Path(config)/'observation_snapshots.json'
    if not ledger.exists():
        return []
    problems = []
    for entry in json.loads(ledger.read_text()).get('industry_rotation', []):
        date = str(entry.get('date'))
        path = _resolve(entry.get('path', ''), root, entry.get('sha256'))
        if date == str(current):
            if path is None:
                problems.append(f"登记表本交易日快照无法核对：{date}（{entry.get('path')}）；"
                                "只能来自未发布的旧批次，不作为历史对照，本批发布时按候选重写")
            continue
        if date > str(current) or path is None:
            problems.append(f"历史快照缺失：{date}（已排除）")
    return problems


def file_sha256(path: Path) -> str:
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def registered_entry(date: str, config: Path | None = None) -> dict | None:
    """This date's raw registry entry, before any resolution or digest check."""
    ledger = Path(config or config_dir())/'observation_snapshots.json'
    if not ledger.exists():
        return None
    return next((e for e in json.loads(ledger.read_text()).get('industry_rotation', [])
                 if str(e.get('date')) == str(date)), None)


def register_snapshot(date: str, path: Path, config: Path | None = None, root: Path | None = None) -> dict:
    """Record a trade date's accepted facts in the registry.

    Publication owns this write. Build writes a candidate instead, validate
    checks that candidate, and only ``commit_candidate`` / ``reconcile_published``
    reach this function -- so a batch that never went live can never leave an
    entry behind. This date's own entry is rewritten; every other date is
    history and must stay resolvable.
    """
    root = Path(os.path.abspath(str(root or project_root())))
    config = Path(config or root/'config')
    path = Path(path)
    ledger = config/'observation_snapshots.json'
    entries, problems = [], []
    if ledger.exists():
        existing = json.loads(ledger.read_text()).get('industry_rotation', [])
        entries = [e for e in existing if str(e.get('date')) != str(date)]
        if not any(str(e.get('date')) < str(date) for e in entries):
            # Nothing precedes this date: whatever follows it was written by a
            # run that took this date or a later one and can never also register
            # this batch, so it can only advertise a missing past.
            entries = [e for e in entries if str(e.get('date')) < str(date)]
        for entry in entries:
            if _resolve(entry.get('path', ''), root, entry.get('sha256')) is None and str(entry.get('date')) < str(date):
                problems.append(f"历史快照缺失：{entry['date']}（已排除）")
    relative = _relative(path, root)
    entries.append(dict(date=str(date), path=relative, sha256=file_sha256(path)))
    entries.sort(key=lambda e: str(e['date']))
    from src.common.batch_release import atomic_json
    atomic_json(ledger, {'version': 'observation-snapshots-v1', 'industry_rotation': entries})
    return dict(entry=entries[-1], problems=problems)


CANDIDATE = 'industry_snapshot.json'
CANDIDATE_VERSION = 'industry-snapshot-candidate-v1'


def canonical_path(date: str) -> str:
    """The registered name of a trade date's facts inside the public folder."""
    return f'{OBSERVATION.as_posix()}/industry_rotation_{date}.parquet'


def candidate_entry(date: str, path: Path) -> dict:
    """The registry entry this batch would publish once its reports are live."""
    path = Path(path)
    if path.parent.name != OBSERVATION.name:
        raise ValueError('行业快照候选必须位于 observation 目录')
    return dict(date=str(date), path=canonical_path(date), sha256=file_sha256(path))


def write_candidate(directory: Path, date: str, path: Path, run_id: str = '') -> dict:
    """Build output: this batch's candidate, never the official registry.

    The candidate travels inside the batch, so publication can commit it and a
    later run can reconcile a commit interrupted between the pointer swap and
    the registry write.
    """
    entry = candidate_entry(date, path)
    document = dict(version=CANDIDATE_VERSION, trade_date=str(date), entry=entry)
    if run_id:
        document['run_id'] = str(run_id)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory/CANDIDATE
    target.write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n')
    return dict(file=target.name, sha256=file_sha256(target), entry=entry)


def read_candidate(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def verify_candidate(document: dict, date: str, folder: Path) -> dict:
    """Validate output: the candidate names this batch's accepted facts, byte for byte."""
    if document.get('version') != CANDIDATE_VERSION:
        raise ValueError('行业快照候选版本未知')
    if str(document.get('trade_date')) != str(date):
        raise ValueError('行业快照候选日期未对齐')
    entry = dict(document.get('entry') or {})
    if entry.get('path') != canonical_path(date):
        raise ValueError('行业快照候选未按公布目录命名')
    facts = Path(folder)/f'industry_rotation_{date}.parquet'
    if not entry.get('sha256') or not facts.is_file() or file_sha256(facts) != entry['sha256']:
        raise ValueError('行业快照候选摘要与已验收事实不一致')
    return entry


def commit_candidate(document: dict, config: Path | None = None, root: Path | None = None) -> dict:
    """Publish output: only a live batch may write the official registry.

    The facts are read back from the published folder, never from the staging
    copy the build used: the registry describes what readers can actually see.
    """
    root = Path(os.path.abspath(str(root or project_root())))
    config = Path(config or root/'config')
    entry = dict(document.get('entry') or {})
    date = str(entry.get('date') or '')
    if not date or str(document.get('trade_date') or date) != date:
        raise ValueError('行业快照候选日期未对齐')
    if entry.get('path') != canonical_path(date):
        raise ValueError('行业快照候选未按公布目录命名')
    published = root/'outputs'/entry['path']
    if not published.is_file():
        raise ValueError('已发布批次缺少行业快照事实文件')
    if file_sha256(published) != entry.get('sha256'):
        raise ValueError('已发布行业快照摘要与候选不一致')
    if registered_entry(date, config) == entry:
        return dict(entry=entry, problems=[], changed=False)
    return dict(register_snapshot(date, published, config=config, root=root), changed=True)


def reconcile_published(root: Path | None = None, config: Path | None = None) -> dict | None:
    """Commit the candidate of the currently published batch, if not yet official.

    Publication is the commit point for the reports; the registry write follows
    it. A crash in between leaves a published batch without an entry, so the
    write is derived from the published batch itself and is idempotent.
    """
    root = Path(os.path.abspath(str(root or project_root())))
    folder = root/'outputs'/'run_day'
    publication = folder/'publication.json'
    candidate = folder/CANDIDATE
    if not publication.is_file() or not candidate.is_file():
        return None
    trade_date = str(json.loads(publication.read_text()).get('trade_date') or '')
    document = read_candidate(candidate)
    if not trade_date or str(document.get('trade_date')) != trade_date:
        return None
    return commit_candidate(document, config=config, root=root)


def snapshot_rows(frame: pd.DataFrame, date: str) -> pd.DataFrame:
    """Keep accepted RPS facts and re-derive the stage with the accepted classifier.

    Daily TDX facts carry no falling_out column, so the accepted rule is
    classify_trend_stage(row) with the production default. Reproduction is
    checked against the stored stage; a mismatch is reported, never hidden.
    """
    from src.sw_industry_rps.report import classify_trend_stage
    d = frame.copy()
    keep = [c for c in d.columns if c in ('industry_name', 'RPS5', 'RPS10', 'history_bars')]
    out = d[keep].copy()
    out['_rps15'] = pd.to_numeric(d.get('RPS15'), errors='coerce')
    out['_stage'] = d['trend_stage'].astype(str)
    out['_stage_reproduced'] = d.apply(classify_trend_stage, axis=1)
    out['_name'] = d['industry_name'].astype(str)
    out['_rank15'] = out['_rps15'].rank(ascending=False, method='min')
    out['_source_date'] = (pd.to_datetime(d['source_trade_date']).dt.strftime('%Y%m%d')
                           if 'source_trade_date' in d else date)
    return out.assign(_code=d['industry_code'].astype(str)).drop_duplicates('_code').set_index('_code')


def change_events(current: pd.DataFrame, date: str, root: Path | None = None) -> dict:
    """Compare with the latest registered snapshot of the same universe."""
    return compare_events(snapshot_rows(current, date), date, snapshots(root))


def compare_events(current_rows: pd.DataFrame, date: str, registered) -> dict:
    """Cross-date events from this batch's rows and the registered snapshots.

    ``registered`` is ``(histories, problems)`` as returned by ``snapshots``.
    A problem listed by the publishing batch for this date is not produced
    here: the running date is not history, and its own entry can only be
    checked against the facts this batch just wrote.
    """
    problems = []
    if not current_rows['_stage'].eq(current_rows['_stage_reproduced']).all():
        problems.append('本批行业阶段无法用现行分类规则复现，变化观察不可判定')
    histories, registry_problems = registered
    # A same-date entry cannot describe this batch: its facts file is the one
    # this batch holds, and the registry is rewritten from it after acceptance.
    registry_problems = [p for p in registry_problems if date not in p]
    problems.extend(registry_problems)
    previous = None
    for snap_date, path in histories:
        if snap_date >= date:
            continue
        frame = pd.read_parquet(path) if path.suffix == '.parquet' else pd.read_csv(path)
        rows = snapshot_rows(frame, snap_date)
        if current_rows['_source_date'].nunique() != 1 or rows['_source_date'].nunique() != 1:
            problems.append(f"源交易日不唯一（{snap_date} / {date}），未作变化对照")
            continue
        if set(rows.index) != set(current_rows.index):
            problems.append(f"行业样本不一致：{snap_date} 与 {date} 的行业代码集合不同，未作变化对照")
            continue
        if not rows['_stage'].eq(rows['_stage_reproduced']).all():
            problems.append(f"{snap_date} 行业阶段无法用现行分类规则复现，跳过该快照")
            continue
        previous = (snap_date, rows)
        break
    base = dict(version=EVENT_VERSION, trade_date=date, previous_date=None, comparable=False,
                previous_source_date=None, current_source_date=None,
                top_rank=TOP_RANK, events=[], first_entry=[], first_formed=[], relapse=[],
                remains_formed=0, changed=0, stage_changes=0, problems=problems)
    if previous is None or problems:
        if not problems:
            problems.append('无本交易日之前已登记且可对照的行业快照，无法判定跨日变化')
        base['problems'] = problems
        return base
    prev_date, prev = previous
    stage_now, stage_then = current_rows['_stage'], prev['_stage']
    rank_now, rank_then = current_rows['_rank15'], prev['_rank15']
    rps_now = current_rows['_rps15']
    events = []
    for code in current_rows.index:
        event = dict(code=code, name=current_rows.at[code, '_name'],
                     rps15=rps_now.get(code), rps5=_number(current_rows, code, 'RPS5'),
                     rank15=rank_now.get(code), previous_rank15=rank_then.get(code),
                     stage=stage_now.get(code), previous_stage=stage_then.get(code))
        if rank_now.get(code) <= TOP_RANK < rank_then.get(code):
            events.append(dict(event, kind='FIRST_TOP20'))
        if stage_now.get(code) == '已形成趋势' and stage_then.get(code) != '已形成趋势':
            events.append(dict(event, kind='FIRST_FORMED'))
        if stage_now.get(code) == '正在退潮' and stage_then.get(code) != '正在退潮':
            events.append(dict(event, kind='RELAPSE'))
    order = {'FIRST_TOP20': 0, 'FIRST_FORMED': 1, 'RELAPSE': 2}
    events.sort(key=lambda e: (order[e['kind']], -(e['rps15'] if pd.notna(e['rps15']) else -1)))
    delta = current_rows['RPS5'].astype(float) - prev['RPS5'].astype(float)
    base.update(previous_date=prev_date, comparable=True, events=events,
                previous_source_date=str(prev['_source_date'].iloc[0]),
                current_source_date=str(current_rows['_source_date'].iloc[0]),
                first_entry=[e for e in events if e['kind'] == 'FIRST_TOP20'],
                first_formed=[e for e in events if e['kind'] == 'FIRST_FORMED'],
                relapse=[e for e in events if e['kind'] == 'RELAPSE'],
                remains_formed=int(stage_now.eq('已形成趋势').sum()),
                changed=int(delta.abs().gt(1e-9).sum()),
                stage_changes=int((stage_now != stage_then).sum()),
                rps5_change={code: float(delta.loc[code]) for code in current_rows.index
                             if not pd.isna(delta.loc[code])})
    return base


def _number(frame, code, column):
    if column not in frame:
        return None
    value = frame.at[code, column]
    return None if pd.isna(value) else float(value)
