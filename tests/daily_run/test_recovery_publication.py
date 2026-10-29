import json
from pathlib import Path

import pytest

from src import daily_run
from src.common import batch_release as release


def harness(tmp_path, monkeypatch, fail_at=5):
    root = tmp_path
    public = root / 'outputs'
    public.mkdir()
    (public / 'report.txt').write_text('previous complete report')
    commands = [['fake', str(i), '--date', '20261012'] for i in range(1, 7)]
    calls = []
    fail = {'enabled': True}
    monkeypatch.setattr(daily_run, 'steps', lambda *a: commands)
    monkeypatch.setattr(daily_run, 'check_step', lambda *a: None)
    monkeypatch.setattr(daily_run, 'step_artifacts', lambda command, td: [daily_run.outputs_dir() / f'{command[1]}.json'])
    def validate(td):
        receipt = {'ok': True, 'trade_date': td, 'status': 'CONFIRMED',
                   'scope': 'observation_only', 'warnings': [], 'errors': []}
        stage = daily_run.outputs_dir()
        (stage / 'run_day').mkdir(exist_ok=True)
        (stage / 'run_day' / f'final_check_{td}.json').write_text(json.dumps(receipt))
        (stage / 'observation').mkdir(exist_ok=True)
        for suffix in ('.html', '.md', '.json'):
            (stage / 'observation' / f'observation_{td}{suffix}').write_text(
                json.dumps({'trade_date': td}) if suffix == '.json' else 'facts')
        return receipt
    monkeypatch.setattr(daily_run, 'validate_release', validate)
    def run(command, env, log, title):
        number = int(command[1])
        calls.append(number)
        stage = Path(env['AKSIGNAL_OUTPUTS_DIR'])
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text('private verbose log')
        (stage / 'report.txt').write_text(f'unpublished step {number}')
        if number == fail_at and fail['enabled']:
            return 1
        (stage / f'{number}.json').write_text(json.dumps({'step': number}))
        return 0
    monkeypatch.setattr(daily_run, 'run_step', run)
    return calls, fail


def test_fifth_step_failure_resumes_fifth_and_keeps_public_report(tmp_path, monkeypatch, capsys):
    calls, fail = harness(tmp_path, monkeypatch)
    assert daily_run.execute_batch(tmp_path, '20261012', False) == 1
    assert calls == [1, 2, 3, 4, 5]
    assert (tmp_path / 'outputs/report.txt').read_text() == 'previous complete report'
    saved = release.read_checkpoint(tmp_path, '20261012', False)
    assert saved['current_step'] == 4
    assert saved['steps'][4]['status'] == 'failed'
    fail['enabled'] = False
    assert daily_run.execute_batch(tmp_path, '20261012', False) == 0
    assert calls == [1, 2, 3, 4, 5, 5, 6]
    assert '从第 5 步恢复，复用前 4 步结果' in capsys.readouterr().out
    assert (tmp_path / 'outputs/report.txt').read_text() == 'unpublished step 6'
    assert (tmp_path / 'outputs').is_symlink()
    receipt = json.loads((tmp_path / 'outputs/run_day/publication.json').read_text())
    assert receipt['trade_date'] == '20261012'
    assert release.read_checkpoint(tmp_path, '20261012', False)['status'] == 'published'


def test_changed_completed_fact_rewinds_to_earliest_invalid_step(tmp_path, monkeypatch):
    calls, fail = harness(tmp_path, monkeypatch)
    assert daily_run.execute_batch(tmp_path, '20261012', False) == 1
    state = release.read_checkpoint(tmp_path, '20261012', False)
    (release.run_dir(tmp_path, state) / 'outputs/3.json').write_text('changed input')
    fail['enabled'] = False
    assert daily_run.execute_batch(tmp_path, '20261012', False) == 0
    assert calls == [1, 2, 3, 4, 5, 3, 4, 5, 6]


def test_publish_failure_resumes_only_publication(tmp_path, monkeypatch):
    calls, fail = harness(tmp_path, monkeypatch)
    fail['enabled'] = False
    original = release.publish_workspace
    count = {'calls': 0}
    def publish(root, stage):
        count['calls'] += 1
        if count['calls'] == 1:
            raise OSError('publication interrupted')
        original(root, stage)
    monkeypatch.setattr(release, 'publish_workspace', publish)
    assert daily_run.execute_batch(tmp_path, '20261012', False) == 1
    assert (tmp_path / 'outputs/report.txt').read_text() == 'previous complete report'
    assert daily_run.execute_batch(tmp_path, '20261012', False) == 0
    assert calls == [1, 2, 3, 4, 5, 6]
    assert count['calls'] == 2


def test_code_or_config_change_requires_explicit_restart(tmp_path, monkeypatch):
    calls, fail = harness(tmp_path, monkeypatch)
    assert daily_run.execute_batch(tmp_path, '20261012', False) == 1
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config/strategy.yaml').write_text('changed: true')
    with pytest.raises(ValueError, match='--restart'):
        daily_run.execute_batch(tmp_path, '20261012', False)
    fail['enabled'] = False
    assert daily_run.execute_batch(tmp_path, '20261012', False, restart=True) == 0
    assert calls[-6:] == [1, 2, 3, 4, 5, 6]


def test_interruption_preserves_completed_steps(tmp_path, monkeypatch):
    calls, fail = harness(tmp_path, monkeypatch)
    original = daily_run.run_step
    def interrupt(command, *args):
        if command[1] == '5' and fail['enabled']:
            raise KeyboardInterrupt()
        return original(command, *args)
    monkeypatch.setattr(daily_run, 'run_step', interrupt)
    assert daily_run.execute_batch(tmp_path, '20261012', False) == 130
    assert release.read_checkpoint(tmp_path, '20261012', False)['status'] == 'interrupted'
    fail['enabled'] = False
    assert daily_run.execute_batch(tmp_path, '20261012', False) == 0
    assert calls == [1, 2, 3, 4, 5, 6]


def test_first_publication_rolls_back_directory_on_pointer_failure(tmp_path, monkeypatch):
    public = tmp_path / 'outputs'
    public.mkdir()
    (public / 'etf.html').write_text('old ETF')
    stage = tmp_path / 'stage'
    stage.mkdir()
    (stage / 'etf.html').write_text('new ETF')
    original = release.os.replace
    def replace(source, destination):
        if Path(source).is_symlink() and Path(destination) == public:
            raise OSError('pointer failure')
        return original(source, destination)
    monkeypatch.setattr(release.os, 'replace', replace)
    with pytest.raises(OSError):
        release.publish_workspace(tmp_path, stage)
    assert not public.is_symlink()
    assert (public / 'etf.html').read_text() == 'old ETF'


def test_subsequent_publication_switches_all_reports_and_latest_together(tmp_path):
    first = tmp_path / 'first'
    second = tmp_path / 'second'
    for folder, value in [(first, 'v1'), (second, 'v2')]:
        folder.mkdir()
        for name in ['etf.html', 'industry.html', 'industry_latest.html', 'summary.json']:
            (folder / name).write_text(value)
    release.publish_workspace(tmp_path, first)
    assert {(tmp_path / 'outputs' / n).read_text() for n in ['etf.html', 'industry_latest.html']} == {'v1'}
    release.publish_workspace(tmp_path, second)
    assert {(tmp_path / 'outputs' / n).read_text() for n in ['etf.html', 'industry_latest.html', 'summary.json']} == {'v2'}
    assert (first / 'etf.html').read_text() == 'v1'


def test_lock_rejects_concurrent_batches(tmp_path):
    with release.batch_lock(tmp_path):
        with pytest.raises(ValueError, match='已有 run-day'):
            with release.batch_lock(tmp_path):
                pytest.fail('concurrent lock was admitted')


def test_status_names_business_phase_and_resume_point(tmp_path, monkeypatch, capsys):
    calls, fail = harness(tmp_path, monkeypatch)
    daily_run.execute_batch(tmp_path, '20261012', False)
    daily_run.show_status(tmp_path)
    message = capsys.readouterr().out
    assert '失败，可恢复' in message
    assert '进度 5/7' in message


def test_verbose_child_output_goes_to_log_and_progress_is_visible(tmp_path, monkeypatch, capsys):
    class Process:
        count = 0
        def wait(self, timeout):
            self.count += 1
            if self.count == 1:
                raise daily_run.subprocess.TimeoutExpired('fake', timeout)
            return 0
    def popen(command, **kwargs):
        kwargs['stdout'].write('lots of source diagnostics')
        return Process()
    monkeypatch.setattr(daily_run.subprocess, 'Popen', popen)
    log = tmp_path / 'step.log'
    assert daily_run.run_step(['etf', 'update'], {}, log, '更新 ETF 行情') == 0
    assert log.read_text() == 'lots of source diagnostics'
    output = capsys.readouterr().out
    assert '更新 ETF 行情' in output
    assert 'source diagnostics' not in output


def test_incomplete_report_set_is_never_published(tmp_path):
    with release.output_workspace(tmp_path):
        with pytest.raises(ValueError, match='报告未完整生成'):
            daily_run.validate_release('20261012')


def test_seeded_old_html_does_not_count_as_this_attempts_report(tmp_path):
    path = tmp_path / 'observation/observation_20261012.html'
    path.parent.mkdir()
    path.write_text('previous complete report')
    for suffix in ('.md', '.json'):
        path.with_suffix(suffix).write_text('previous complete report')
    with release.output_workspace(tmp_path):
        with pytest.raises(ValueError, match='不能沿用旧文件'):
            daily_run.check_generated_reports(['observation', 'build', '--date', '20261012'],
                                              '20261012', max(p.stat().st_mtime_ns for p in path.parent.iterdir()) + 1)
        daily_run.check_generated_reports(['observation', 'build', '--date', '20261012'],
                                          '20261012', min(p.stat().st_mtime_ns for p in path.parent.iterdir()))


def test_position_algorithm_output_root_in_fresh_process(tmp_path):
    import os
    import subprocess
    import sys
    env = {**os.environ, 'AKSIGNAL_OUTPUTS_DIR': str(tmp_path)}
    process = subprocess.run([sys.executable, '-c',
        'from src.research.etf_bottom import STUDY_DIR; print(STUDY_DIR)'],
        env=env, text=True, capture_output=True, check=True)
    assert process.stdout.strip() == str(tmp_path / 'research/etf_bottom')


def test_explicit_restart_can_recover_a_corrupt_checkpoint(tmp_path, monkeypatch):
    calls, fail = harness(tmp_path, monkeypatch)
    path = release.checkpoint_path(tmp_path, '20261012', False)
    path.parent.mkdir(parents=True)
    path.write_text('broken json')
    fail['enabled'] = False
    assert daily_run.execute_batch(tmp_path, '20261012', False, restart=True) == 0


def test_committed_publication_survives_checkpoint_write_failure(tmp_path, monkeypatch, capsys):
    calls, fail = harness(tmp_path, monkeypatch)
    fail['enabled'] = False
    original = release.save_checkpoint
    def save(root, state):
        if state['status'] == 'published':
            raise OSError('checkpoint disk failure')
        original(root, state)
    monkeypatch.setattr(release, 'save_checkpoint', save)
    assert daily_run.execute_batch(tmp_path, '20261012', False) == 0
    assert '日报已发布，运行记录保存失败' in capsys.readouterr().out
    assert release.read_checkpoint(tmp_path, '20261012', False)['status'] == 'publishing'
    monkeypatch.setattr(release, 'save_checkpoint', original)
    assert daily_run.execute_batch(tmp_path, '20261012', False) == 0
    assert calls == [1, 2, 3, 4, 5, 6]
    assert release.read_checkpoint(tmp_path, '20261012', False)['status'] == 'published'


def test_exception_after_pointer_commit_does_not_report_unpublished(tmp_path, monkeypatch):
    calls, fail = harness(tmp_path, monkeypatch)
    fail['enabled'] = False
    original = release.publish_workspace
    def publish(root, stage):
        original(root, stage)
        raise OSError('post-commit bookkeeping failed')
    monkeypatch.setattr(release, 'publish_workspace', publish)
    assert daily_run.execute_batch(tmp_path, '20261012', False) == 0
    assert release.read_checkpoint(tmp_path, '20261012', False)['status'] == 'published'
    assert (tmp_path / 'outputs/report.txt').read_text() == 'unpublished step 6'


def test_validation_diagnostics_stay_in_log(tmp_path, monkeypatch, capsys):
    import warnings
    monkeypatch.setattr(daily_run, 'check_step', lambda *a: warnings.warn('internal CSV dtype detail'))
    log = tmp_path / 'step.log'
    daily_run.verify_step(['industry', 'calculate'], '20261012', log)
    assert 'internal CSV dtype detail' in log.read_text()
    assert not capsys.readouterr().err


def test_new_observation_batch_does_not_copy_old_decisions_or_research(tmp_path):
    public = tmp_path/'outputs'
    (public/'selection').mkdir(parents=True)
    (public/'selection/old.json').write_text('legacy recommendation')
    state = release.new_checkpoint(tmp_path,'20261012',True,[])
    stage = release.prepare_workspace(tmp_path,state)
    assert list(stage.iterdir()) == []
    assert (public/'selection/old.json').read_text() == 'legacy recommendation'


def test_build_candidate_is_only_committed_by_publication(tmp_path, monkeypatch):
    """Principle: build computes, validate checks, publish commits.

    The fake build writes a candidate inside the private stage; the ledger must
    not exist while the batch can still fail, and must exist once it published.
    """
    calls, fail = harness(tmp_path, monkeypatch)
    fail['enabled'] = False
    from src.observation import industry_events
    original = daily_run.run_step
    ledger = tmp_path / 'config' / 'observation_snapshots.json'

    def run(command, env, log, title):
        code = original(command, env, log, title)
        if command[0] == 'fake' and command[1] == '4':
            stage = Path(env['AKSIGNAL_OUTPUTS_DIR'])
            folder = stage / 'observation'
            folder.mkdir(parents=True, exist_ok=True)
            facts = folder / 'industry_rotation_20261012.parquet'
            facts.write_bytes(b'industry facts')
            industry_events.write_candidate(stage / 'run_day', '20261012', facts)
            assert not ledger.exists(), 'build must not write the official registry'
        return code

    monkeypatch.setattr(daily_run, 'run_step', run)
    assert daily_run.execute_batch(tmp_path, '20261012', False) == 0
    assert ledger.exists()
    assert json.loads(ledger.read_text())['industry_rotation'][0]['date'] == '20261012'


def test_reconcile_after_publication_repairs_a_missing_registry(tmp_path, capfd):
    """A commit interrupted after the pointer swap is repaired from the published batch."""
    from src.observation import industry_events
    facts = tmp_path / 'outputs' / 'observation' / 'industry_rotation_20261012.parquet'
    facts.parent.mkdir(parents=True)
    facts.write_bytes(b'industry facts')
    industry_events.write_candidate(tmp_path / 'outputs' / 'run_day', '20261012', facts)
    (tmp_path / 'outputs' / 'run_day' / 'publication.json').write_text(json.dumps(
        {'trade_date': '20261012', 'run_id': 'run'}))
    daily_run.commit_published_snapshot(tmp_path)
    assert '行业快照已登记' in capfd.readouterr().out
    ledger = tmp_path / 'config' / 'observation_snapshots.json'
    assert json.loads(ledger.read_text())['industry_rotation'][0]['date'] == '20261012'
    daily_run.commit_published_snapshot(tmp_path)
    assert '行业快照已登记' not in capfd.readouterr().out


def test_classification_publish_during_failure_keeps_pinned_input_on_resume(tmp_path, monkeypatch):
    from src.etf_signal import classification_facts as facts
    calls, fail = harness(tmp_path, monkeypatch)
    doc = {'schema_version': facts.SCHEMA, 'classification_version': 'v1',
           'published_at': '2026-10-11T00:00:00+00:00', 'as_of_date': '2026-10-11',
           'investment_markets': facts.MARKETS, 'investment_directions': facts.DIRECTIONS, 'records': []}
    release.atomic_json(tmp_path / facts.CURRENT, doc)
    observed = []
    original = daily_run.run_step
    def capture(command, env, log, title):
        loaded = facts.read_input(Path(env['AKSIGNAL_ETF_CLASSIFICATION_PATH']), env['AKSIGNAL_ETF_CLASSIFICATION_SHA256'])
        observed.append(loaded['document']['classification_version'])
        return original(command, env, log, title)
    monkeypatch.setattr(daily_run, 'run_step', capture)
    assert daily_run.execute_batch(tmp_path, '20261012', False) == 1
    doc['classification_version'] = 'v2'
    release.atomic_json(tmp_path / facts.CURRENT, doc)
    fail['enabled'] = False
    assert daily_run.execute_batch(tmp_path, '20261012', False) == 0
    assert set(observed) == {'v1'}
    receipt = json.loads((tmp_path / 'outputs/run_day/publication.json').read_text())
    assert receipt['etf_classification']['classification_version'] == 'v1'
    assert receipt['etf_classification']['sha256'] == facts.digest(Path(receipt['etf_classification']['file']).read_bytes())
