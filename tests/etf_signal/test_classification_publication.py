import copy
import json
from pathlib import Path

import pandas as pd
import pytest

from src.common import batch_release
from src.etf_signal import classification_facts as facts, classifier, rotation
from scripts.maintenance.publish_etf_classification import publish


def fact(value, status='inferred'):
    return {'value': value, 'status': status, 'as_of': '2026-01-01', 'scope': 'mandate',
            'source_ids': ['s1'], 'reason': 'Test published input', 'valid_from': None, 'valid_to': None}


def document(version='v1'):
    return {'schema_version': facts.SCHEMA, 'classification_version': version, 'published_at': '2026-01-02T00:00:00+00:00',
            'as_of_date': '2026-01-01', 'investment_markets': facts.MARKETS, 'investment_directions': facts.DIRECTIONS,
            'records': [{'fund_code': '159001', 'fund_name': 'Original fund', 'investment_market': fact('美国'),
                         'investment_direction': fact('科技')}]}


def save(root, doc):
    batch_release.atomic_json(root / facts.CURRENT, doc)
    return root / facts.CURRENT


def test_attach_preserves_rows_and_ignores_names_old_buckets(tmp_path, monkeypatch):
    path = save(tmp_path, document())
    monkeypatch.setenv('AKSIGNAL_ETF_CLASSIFICATION_PATH', str(path))
    monkeypatch.setattr(classifier, 'classify_all', lambda *a, **k: pytest.fail('Daily name classification invoked'))
    source = pd.DataFrame({'fund_code': ['159001', '159002'], 'fund_name': ['现金流ETF', '纳指ETF'], 'asset_bucket': ['money_market']*2})
    result = facts.attach(source, '2026-10-28')
    assert result.investment_market.tolist() == ['美国', '待核实']
    assert result.investment_direction.tolist() == ['科技', '待核实']
    assert result.asset_bucket.tolist() == ['overseas_equity', '']
    assert result.classification_rank_eligible.tolist() == [True, False]
    assert source.asset_bucket.tolist() == ['money_market']*2


def test_missing_or_corrupt_config_is_unavailable_not_inferred(tmp_path):
    path = tmp_path / 'missing.json'
    for content in (None, '{invalid'):
        if content:
            path.write_text(content)
        result = facts.attach(pd.DataFrame({'fund_code': ['159001'], 'fund_name': ['纳指ETF']}), '20261028', facts.read_input(path))
        assert result.investment_direction.tolist() == ['待核实']
        assert result.classification_input_status.tolist() == ['unavailable']


@pytest.mark.parametrize('mutation', [lambda doc: [], lambda doc: {**doc, 'records': [None]},
    lambda doc: {**doc, 'records': [{**doc['records'][0], 'investment_market': None}]}])
def test_structurally_invalid_json_degrades_without_crashing(tmp_path, mutation):
    path = save(tmp_path, mutation(document()))
    assert facts.read_input(path)['document'] is None
    with pytest.raises(ValueError, match='Pinned ETF classification unavailable'):
        facts.read_input(path, facts.digest(path.read_bytes()))


def test_pin_survives_current_change_and_checkpoint_resume(tmp_path):
    save(tmp_path, document())
    folder = tmp_path / 'batch'
    pinned = facts.pin_input(tmp_path, folder)
    save(tmp_path, document('v2'))
    assert facts.pin_input(tmp_path, folder, pinned) == pinned
    assert facts.pin_input(tmp_path, folder)['classification_version'] == 'v1'
    assert facts.pin_input(tmp_path, tmp_path / 'next')['classification_version'] == 'v2'
    Path(pinned['file']).write_text('{}')
    with pytest.raises(ValueError, match='checksum'):
        facts.pin_input(tmp_path, folder, pinned)


def test_missing_input_remains_pinned_after_new_publication(tmp_path):
    pinned = facts.pin_input(tmp_path, tmp_path / 'batch')
    save(tmp_path, document())
    assert facts.pin_input(tmp_path, tmp_path / 'batch', pinned)['status'] == 'unavailable'


def test_published_config_is_input_not_implementation_signature(tmp_path):
    before = batch_release.implementation_signature(tmp_path)
    save(tmp_path, document())
    batch_release.atomic_json(tmp_path / facts.VERSIONS / 'v1.json', document())
    assert before == batch_release.implementation_signature(tmp_path)
    (tmp_path / 'config/etf_classification.yaml').write_text('taxonomy changed')
    assert before != batch_release.implementation_signature(tmp_path)


def test_validity_and_snapshot_date_are_not_silently_extended(tmp_path):
    doc = document()
    doc['records'][0]['investment_market'].update(valid_from='2026-10-01', valid_to='2026-10-28')
    doc['records'][0]['investment_direction'].update(scope='holdings_snapshot', as_of='2026-10-27')
    loaded = facts.read_input(save(tmp_path, doc))
    result = facts.attach(pd.DataFrame({'fund_code': ['159001']}), '2026-10-28', loaded)
    assert result.investment_market.iloc[0] == '待核实'
    assert result.investment_direction.iloc[0] == '待核实'


def test_future_knowledge_is_explicit_and_verified_not_downgraded(tmp_path):
    doc = document()
    doc['published_at'] = '2026-10-29T00:00:00+00:00'
    doc['records'][0]['investment_market']['status'] = 'verified'
    result = facts.attach(pd.DataFrame({'fund_code': ['159001']}), '2026-10-28', facts.read_input(save(tmp_path, doc)))
    assert result.market_classification_status.iloc[0] == 'verified'
    assert result.direction_classification_status.iloc[0] == 'inferred'
    assert result.classification_temporal_basis.iloc[0] == 'retrospective'


def test_all_unknown_and_all_defensive_never_fall_back_to_full_ranking(tmp_path, monkeypatch):
    dates = pd.bdate_range('2026-01-01', periods=70)
    combined = pd.DataFrame({'date': dates, 'fund_code': '159001', 'close': range(100, 170)})
    master = pd.DataFrame({'fund_code': ['159001'], 'fund_name': ['现金流ETF'], 'asset_bucket': ['industry']})
    monkeypatch.setattr(classifier, 'classify_all', lambda *a, **k: pytest.fail('Name classifier invoked'))
    for direction in ('待核实', '货币'):
        doc = document()
        doc['records'][0]['investment_direction'] = fact(direction, 'pending' if direction == '待核实' else 'inferred')
        path = save(tmp_path, doc)
        monkeypatch.setenv('AKSIGNAL_ETF_CLASSIFICATION_PATH', str(path))
        result = rotation.compute_rotation_metrics(combined, master)
        assert len(result) == 1
        assert result[['rps1', 'rps5', 'rps15']].isna().all().all()
        assert result.return_1d.notna().all()


@pytest.fixture
def candidate(tmp_path):
    # Publisher boundary tests inject a deterministic evidence validator. The installed
    # Skill has separate tests for official evidence/claims and is used for real publication.
    validator = tmp_path / 'validator.py'
    validator.write_text('def validate(path):\n    return []\n')
    path = tmp_path / 'data/candidate.json'
    fund = copy.deepcopy(document()['records'][0])
    for key in ('investment_market', 'investment_direction'):
        fund[key]['evidence'] = [{'source_id': 's1'}]
    doc = {'run': {'completion': 'complete', 'run_id': 'test', 'as_of_date': '2026-01-01', 'taxonomy_sha256': 'test'}, 'funds': [fund]}
    batch_release.atomic_json(path, doc)
    return path, validator


def test_publish_idempotent_and_partial_update_preserves_other_funds(tmp_path, candidate):
    path, validator = candidate
    first = publish(tmp_path, path, 'v1', validator)
    assert publish(tmp_path, path, 'v1', validator) == first
    old = (tmp_path / facts.VERSIONS / 'v1.json').read_bytes()
    doc = json.loads(path.read_bytes())
    doc['funds'][0]['fund_code'] = '159002'
    path2 = path.with_name('second.json')
    batch_release.atomic_json(path2, doc)
    with pytest.raises(ValueError, match='base version'):
        publish(tmp_path, path2, 'v2', validator)
    publish(tmp_path, path2, 'v2', validator, 'v1')
    current = facts.read_input(tmp_path / facts.CURRENT)['document']
    assert {r['fund_code'] for r in current['records']} == {'159001', '159002'}
    assert (tmp_path / facts.VERSIONS / 'v1.json').read_bytes() == old


def test_failed_publication_keeps_old_config_and_can_resume(tmp_path, candidate, monkeypatch):
    from scripts.maintenance import publish_etf_classification as publisher
    path, validator = candidate
    publish(tmp_path, path, 'v1', validator)
    before = (tmp_path / facts.CURRENT).read_bytes()
    original = publisher.atomic_json
    def fail_current(target, payload):
        if target == tmp_path / facts.CURRENT:
            raise OSError('simulated switch failure')
        return original(target, payload)
    monkeypatch.setattr(publisher, 'atomic_json', fail_current)
    with pytest.raises(OSError):
        publish(tmp_path, path, 'v2', validator, 'v1')
    assert (tmp_path / facts.CURRENT).read_bytes() == before
    monkeypatch.setattr(publisher, 'atomic_json', original)
    assert publish(tmp_path, path, 'v2', validator, 'v1')['status'] == 'published'


def test_rejected_evidence_does_not_publish(tmp_path, candidate):
    path, validator = candidate
    validator.write_text('def validate(path):\n    return ["missing evidence"]\n')
    with pytest.raises(ValueError, match='missing evidence'):
        publish(tmp_path, path, 'v1', validator)
    assert not (tmp_path / facts.CURRENT).exists()


def test_corrupt_previous_config_is_not_silently_replaced(tmp_path, candidate):
    path, validator = candidate
    previous = save(tmp_path, {})
    with pytest.raises(ValueError, match='schema'):
        publish(tmp_path, path, 'v1', validator)
    assert json.loads(previous.read_text()) == {}
