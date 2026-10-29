import json

import pandas as pd
import pytest

from src.observation import industry_events
from src.observation.cli import verify_industry_events


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Registry lives in a temp config dir; ledger paths are root relative."""
    config = tmp_path/'config'
    config.mkdir()
    monkeypatch.setattr(industry_events, 'config_dir', lambda: config)
    return tmp_path


def register(tmp_path, date, rows):
    folder = tmp_path/'outputs'/'observation'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder/f'industry_rotation_{date}.csv'
    pd.DataFrame(rows).to_csv(path, index=False)
    return industry_events.register_snapshot(date, path, root=tmp_path)


def row(code, name, rps15, rps5, stage, source_date='2026-10-26'):
    return dict(industry_code=code, industry_name=name, RPS15=rps15, RPS5=rps5, trend_stage=stage,
                source_trade_date=source_date, trade_date=source_date)


def universe():
    """36 industries: five movers above the fillers, so the top-20 boundary is observable."""
    rows = [
        row('881001', '油气开采', 100.0, 96.0, '已形成趋势'),
        row('881002', '电信服务', 95.0, 88.0, '已形成趋势'),
        row('881003', '影视院线', 90.0, 70.0, '已形成趋势'),
        row('881004', '银行', 60.0, 85.0, '正在启动'),
        row('881005', '新兴', 55.0, 85.0, '正在启动'),
    ]
    for index, rps15 in enumerate(range(85, 49, -1), start=6):
        low = rps15 < 80
        rows.append(row(f'88{1100 + index:04d}', f'填充{index:02d}', float(rps15), 40.0,
                        '—' if low else '正在退潮'))
    return rows


PREVIOUS = universe()


def test_registry_records_root_relative_facts_with_digest(isolated):
    result = register(isolated, '20261026', PREVIOUS)
    # Facts are registered by canonical public name, not by staging path.
    assert result['entry']['path'] == 'observation/industry_rotation_20261026.csv'
    assert len(result['entry']['sha256']) == 64
    ledger = json.loads((isolated/'config'/'observation_snapshots.json').read_text())
    assert ledger['industry_rotation'][0]['date'] == '20261026'


def test_first_entry_first_formed_and_relapse_are_derived_from_two_snapshots(isolated):
    register(isolated, '20261026', PREVIOUS)
    today = [dict(r, source_trade_date='2026-10-27') for r in PREVIOUS]
    today[4].update(RPS15=93.0, trend_stage='已形成趋势', RPS5=90.0)   # 新兴 moves into the top 20
    today[3].update(trend_stage='已形成趋势', RPS15=85.0, RPS5=85.0)  # 银行 正在启动 -> 已形成趋势
    today[2].update(RPS5=40.0, trend_stage='正在退潮')   # 影视院线 cools down
    events = industry_events.change_events(pd.DataFrame(today), '20261027', root=isolated)
    assert events['comparable'] and events['previous_date'] == '20261026'
    assert events['current_source_date'] == '20261027' and events['previous_source_date'] == '20261026'
    assert [e['name'] for e in events['first_entry']] == ['新兴', '银行']   # RPS15 order
    assert events['first_entry'][0]['rank15'] <= 20 < events['first_entry'][0]['previous_rank15']
    assert [e['name'] for e in events['first_formed']] == ['新兴', '银行']
    assert [e['name'] for e in events['relapse']] == ['影视院线']
    entry = events['first_entry'][0]
    assert entry['rank15'] == 3 and entry['previous_rank15'] == 35
    verify_industry_events(events)


def test_no_registered_snapshot_is_undeterminable_not_empty(isolated):
    events = industry_events.change_events(pd.DataFrame(PREVIOUS), '20261027', root=isolated)
    assert events['comparable'] is False and events['events'] == []
    assert any('无法判定跨日变化' in p for p in events['problems'])
    verify_industry_events(events)


def test_universe_mismatch_blocks_events(isolated):
    register(isolated, '20261026', PREVIOUS[:-1])
    events = industry_events.change_events(pd.DataFrame(PREVIOUS), '20261027', root=isolated)
    assert events['comparable'] is False and any('行业样本不一致' in p for p in events['problems'])


def test_unreproducible_stage_is_not_used_for_comparison(isolated):
    tampered = [dict(r) for r in PREVIOUS]
    tampered[0]['trend_stage'] = '正在退潮'   # RPS5=96 is not 退潮 under the accepted rule
    register(isolated, '20261026', tampered)
    events = industry_events.change_events(pd.DataFrame(PREVIOUS), '20261027', root=isolated)
    assert events['comparable'] is False and any('无法用现行分类规则复现' in p for p in events['problems'])


def test_mixed_source_dates_are_not_compared(isolated):
    different = [dict(r, source_trade_date='2026-10-23') for r in PREVIOUS]
    register(isolated, '20261023', different)
    # The current batch mixes two native source dates, so no cross-day change is claimed.
    mixed = [dict(r, source_trade_date='2026-10-27') for r in PREVIOUS]
    mixed[0]['source_trade_date'] = '2026-10-26'
    events = industry_events.change_events(pd.DataFrame(mixed), '20261027', root=isolated)
    assert events['comparable'] is False and any('源交易日不唯一' in p for p in events['problems'])


def test_missing_registered_snapshot_is_reported(isolated):
    (isolated/'config').mkdir(exist_ok=True)
    (isolated/'config'/'observation_snapshots.json').write_text(json.dumps(
        {'industry_rotation': [dict(date='20261026', path='outputs/observation/gone.csv')]}))
    events = industry_events.change_events(pd.DataFrame(PREVIOUS), '20261027', root=isolated)
    assert events['comparable'] is False and any('历史快照缺失' in p for p in events['problems'])


def test_receipt_verification_rejects_inconsistent_events():
    event = dict(kind='FIRST_TOP20', rank15=5, previous_rank15=30, rps15=90.0, name='A',
                 stage='已形成趋势', previous_stage='已形成趋势')
    valid = dict(version='industry_change_events_v1', trade_date='20261027', previous_date='20261026',
                 comparable=True, previous_source_date='20261026', current_source_date='20261027', top_rank=20,
                 problems=[], events=[event], first_entry=[event], first_formed=[], relapse=[])
    verify_industry_events(valid)
    broken = json.loads(json.dumps(valid))
    broken['events'][0]['rank15'] = 40
    with pytest.raises(ValueError, match='首次进入前20'):
        verify_industry_events(broken)
    broken = json.loads(json.dumps(valid))
    broken['relapse'] = [dict(kind='RELAPSE')]
    with pytest.raises(ValueError, match='计数'):
        verify_industry_events(broken)
    with pytest.raises(ValueError, match='未说明原因'):
        verify_industry_events(dict(valid, comparable=False, events=[], first_entry=[], first_formed=[],
                                    relapse=[], problems=[]))


def test_registration_survives_publication_of_the_staging_directory(isolated):
    """The build registers inside a private stage; publication moves it to outputs/.

    The stage is discarded, so a registered stage path would die with it. The
    canonical public name must resolve after publication and make the next batch
    comparable -- this is the "无法判定" that v0.7.3 showed by construction.
    """
    stage = isolated/'.stage'/'run1'/'outputs'
    (stage/'observation').mkdir(parents=True)
    path = stage/'observation'/'industry_rotation_20261026.csv'
    pd.DataFrame(PREVIOUS).to_csv(path, index=False)
    result = industry_events.register_snapshot('20261026', path, root=isolated)
    assert result['entry']['path'] == 'observation/industry_rotation_20261026.csv'

    public = isolated/'outputs'/'observation'
    public.mkdir(parents=True)
    (public/'industry_rotation_20261026.csv').write_bytes(path.read_bytes())
    (isolated/'.stage').rename(isolated/'discarded')

    resolved, problems = industry_events.snapshots(root=isolated)
    assert problems == [] and resolved[0][0] == '20261026'
    assert resolved[0][1] == public/'industry_rotation_20261026.csv'
    today = [dict(r, source_trade_date='2026-10-27') for r in PREVIOUS]
    assert industry_events.change_events(pd.DataFrame(today), '20261027', root=isolated)['comparable']


def write_ledger(root, entries):
    (root/'config').mkdir(exist_ok=True)
    (root/'config'/'observation_snapshots.json').write_text(json.dumps(
        {'version': 'observation-snapshots-v1', 'industry_rotation': entries}))


def test_same_date_entry_with_lost_staging_file_is_not_missing_history(isolated):
    """2026-10-29 failure: a build of 20261028 registered inside its private stage,
    the batch never published, the stage died with it. The surviving entry is the
    current date itself, so it is not history and must not read as missing history.
    """
    write_ledger(isolated, [dict(date='20261028', path='observation/industry_rotation_20261028.csv',
                                 sha256='0'*64)])
    problems = industry_events.registry_problems(isolated/'config', '20261028', isolated)
    assert len(problems) == 1 and '本交易日' in problems[0] and '历史快照缺失' not in problems[0]
    events = industry_events.change_events(pd.DataFrame(PREVIOUS), '20261028', root=isolated)
    # The comparison is undeterminable (no earlier snapshot), but nothing claims missing history.
    assert events['comparable'] is False and not any('历史快照缺失' in p for p in events['problems'])
    verify_industry_events(events)


def test_earlier_unresolvable_snapshot_stays_a_hard_registry_problem(isolated):
    write_ledger(isolated, [dict(date='20261027', path='observation/industry_rotation_20261027.csv',
                                 sha256='0'*64)])
    problems = industry_events.registry_problems(isolated/'config', '20261028', isolated)
    assert problems == ['历史快照缺失：20261027（已排除）']


def test_registry_is_clean_for_the_date_it_registers(isolated):
    register(isolated, '20261026', PREVIOUS)
    assert industry_events.registry_problems(isolated/'config', '20261026', isolated) == []


def test_same_date_public_file_with_other_digest_is_not_the_registered_snapshot(isolated):
    """A file left in the public folder by an unpublished staging or failed batch
    does not carry the registered digest, so it must not stand in for the snapshot."""
    path = isolated/'outputs'/'observation'/'industry_rotation_20261028.csv'
    path.parent.mkdir(parents=True)
    pd.DataFrame(PREVIOUS).to_csv(path, index=False)
    write_ledger(isolated, [dict(date='20261028', path='observation/industry_rotation_20261028.csv',
                                 sha256='0'*64)])
    assert industry_events.snapshots(root=isolated)[0] == []
    problems = industry_events.registry_problems(isolated/'config', '20261028', isolated)
    assert len(problems) == 1 and problems[0].startswith('登记表本交易日快照无法核对：20261028')


def published_batch(root, date, rows):
    """A live batch: facts in the public folder plus the candidate build wrote."""
    facts = root/'outputs'/'observation'/f'industry_rotation_{date}.parquet'
    facts.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(facts, index=False)
    return industry_events.write_candidate(root/'outputs'/'run_day', date, facts)


def test_build_candidate_is_not_a_registry_write(isolated):
    stage = isolated/'.stage'/'run'/'outputs'/'observation'
    stage.mkdir(parents=True)
    facts = stage/'industry_rotation_20261028.parquet'
    pd.DataFrame(PREVIOUS).to_parquet(facts, index=False)
    receipt = industry_events.write_candidate(stage.parent/'run_day', '20261028', facts)
    # Build produces a candidate inside the batch; the official ledger is untouched.
    assert not (isolated/'config'/'observation_snapshots.json').exists()
    assert receipt['entry']['path'] == 'observation/industry_rotation_20261028.parquet'
    document = industry_events.read_candidate(stage.parent/'run_day'/industry_events.CANDIDATE)
    assert industry_events.verify_candidate(document, '20261028', stage) == receipt['entry']


def test_verify_candidate_requires_the_batchs_own_bytes(isolated):
    receipt = published_batch(isolated, '20261028', PREVIOUS)
    folder = isolated/'outputs'/'observation'
    document = industry_events.read_candidate(isolated/'outputs'/'run_day'/industry_events.CANDIDATE)
    assert industry_events.verify_candidate(document, '20261028', folder) == receipt['entry']
    pd.DataFrame([dict(r, RPS5=1.0) for r in PREVIOUS]).to_parquet(
        folder/'industry_rotation_20261028.parquet', index=False)
    with pytest.raises(ValueError, match='摘要'):
        industry_events.verify_candidate(document, '20261028', folder)
    with pytest.raises(ValueError, match='日期'):
        industry_events.verify_candidate(document, '20261029', folder)


def test_publication_commits_the_candidate_to_the_registry(isolated):
    receipt = published_batch(isolated, '20261028', PREVIOUS)
    document = industry_events.read_candidate(isolated/'outputs'/'run_day'/industry_events.CANDIDATE)
    result = industry_events.commit_candidate(document, root=isolated)
    assert result['changed'] is True and result['entry'] == receipt['entry']
    assert industry_events.registered_entry('20261028') == receipt['entry']
    assert dict(industry_events.snapshots(root=isolated)[0])['20261028'] ==         isolated/'outputs'/'observation'/'industry_rotation_20261028.parquet'


def test_commit_refuses_facts_that_are_not_the_published_file(isolated):
    """Only what readers can actually see may be registered."""
    stage = isolated/'.stage'/'run'/'outputs'/'observation'
    stage.mkdir(parents=True)
    facts = stage/'industry_rotation_20261028.parquet'
    pd.DataFrame(PREVIOUS).to_parquet(facts, index=False)
    industry_events.write_candidate(stage.parent/'run_day', '20261028', facts)
    document = industry_events.read_candidate(stage.parent/'run_day'/industry_events.CANDIDATE)
    with pytest.raises(ValueError, match='已发布批次缺少'):
        industry_events.commit_candidate(document, root=isolated)

    public = isolated/'outputs'/'observation'
    public.mkdir(parents=True)
    (public/'industry_rotation_20261028.parquet').write_bytes(b'not the batch facts')
    with pytest.raises(ValueError, match='摘要'):
        industry_events.commit_candidate(document, root=isolated)
    assert not (isolated/'config'/'observation_snapshots.json').exists()


def test_commit_is_idempotent_and_replaces_a_stale_same_date_entry(isolated):
    receipt = published_batch(isolated, '20261028', PREVIOUS)
    write_ledger(isolated, [dict(date='20261028', path='observation/industry_rotation_20261028.parquet',
                                 sha256='0'*64)])
    document = industry_events.read_candidate(isolated/'outputs'/'run_day'/industry_events.CANDIDATE)
    first = industry_events.commit_candidate(document, root=isolated)
    second = industry_events.commit_candidate(document, root=isolated)
    assert first['changed'] is True and second['changed'] is False
    assert industry_events.registered_entry('20261028') == receipt['entry']
    assert len(json.loads((isolated/'config'/'observation_snapshots.json').read_text())['industry_rotation']) == 1


def test_reconcile_repairs_a_commit_interrupted_after_publication(isolated):
    receipt = published_batch(isolated, '20261028', PREVIOUS)
    (isolated/'outputs'/'run_day'/'publication.json').write_text(json.dumps(
        {'trade_date': '20261028', 'run_id': 'run'}))
    first = industry_events.reconcile_published(root=isolated)
    assert first['changed'] is True
    assert industry_events.registered_entry('20261028') == receipt['entry']
    assert industry_events.reconcile_published(root=isolated)['changed'] is False


def test_reconcile_ignores_a_candidate_from_another_date(isolated):
    published_batch(isolated, '20261027', PREVIOUS)
    (isolated/'outputs'/'run_day'/'publication.json').write_text(json.dumps(
        {'trade_date': '20261028', 'run_id': 'run'}))
    assert industry_events.reconcile_published(root=isolated) is None
    assert not (isolated/'config'/'observation_snapshots.json').exists()


def test_comparable_day_keeps_the_current_batch_out_of_history():
    """The current batch's own file is compared as the previous day, not re-read
    as a same-date entry; the day never becomes permanently undeterminable."""
    histories = [('20261027', 'prev.csv')]
    events = industry_events.compare_events(industry_events.snapshot_rows(
        pd.DataFrame([dict(r, source_trade_date='2026-10-27') for r in PREVIOUS]), '20261027'),
        '20261027', (histories, []))
    assert events['comparable'] is False
    assert events['problems'] == ['无本交易日之前已登记且可对照的行业快照，无法判定跨日变化']


def test_registering_a_missing_previous_day_does_not_keep_a_future_entry(isolated):
    p = isolated/'outputs'/'observation'/'industry_rotation_20261027.csv'
    p.parent.mkdir(parents=True)
    pd.DataFrame(PREVIOUS).to_csv(p, index=False)
    write_ledger(isolated, [dict(date='20261028', path='observation/industry_rotation_20261028.csv',
                                 sha256='0'*64)])
    # 20261028 can never also register 20261027, so it must not block it.
    result = industry_events.register_snapshot('20261027', p, root=isolated)
    assert result['problems'] == []
    assert [e['date'] for e in json.loads((isolated/'config'/'observation_snapshots.json').read_text())
            ['industry_rotation']] == ['20261027']
