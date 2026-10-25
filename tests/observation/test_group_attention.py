"""Presentation regressions, using a compact extract of the accepted 2026-10-23 facts."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from src.observation.group_attention import attention_view, explain_group
from src.observation.report import activity_summary, activity_detail, activity_risks


@pytest.fixture
def facts():
    return json.loads((Path(__file__).parent / 'fixtures/group_activity_20261023_v2.json').read_text())


def test_accepted_nine_groups_are_explained_without_mutation(facts):
    original = deepcopy(facts)
    view = attention_view(facts)
    assert facts == original
    assert len(view['groups']) == 9
    assert set(view['highlighted_keys']) == {'scania_china_watch', 'auto_tier1_ai_infra', 'auto_tier1_embodied'}
    expected = {
        'hk_resources_shipping': ('弱势异动', '暂不优先'),
        'scania_china_watch': ('转强观察', '优先研究'),
        'china_auto_global_ex_oem': ('初步企稳', '重点观察'),
        'ai_capex': ('弱势', '常规观察'),
        'china_auto_global': ('弱势整理', '常规观察'),
        'memory_and_storage': ('反弹修复', '观察持续性'),
        'auto_tier1_ai_infra': ('反弹修复', '优先研究'),
        'auto_tier1_embodied': ('上行延续候选', '优先研究'),
        'high_cashflow': ('短期走弱', '常规观察'),
    }
    assert {r['key']: (r['phase'], r['priority']) for r in view['groups']} == expected
    by_key = {r['key']: r for r in view['groups']}
    assert by_key['auto_tier1_ai_infra']['event'] == '首次 WATCH'
    assert by_key['auto_tier1_embodied']['event'] == '持续 WATCH'
    assert view['headline'] == '新增机会先看汽车热管理；已有机会继续跟踪商用车产业链、Physical AI。'
    assert [s['kind'] for s in view['summary_sections']] == ['新增机会', '已有机会']
    assert by_key['auto_tier1_ai_infra']['opportunity'] == '新增机会'
    assert by_key['auto_tier1_embodied']['priority_label'] == '优先跟踪'
    assert '中期仍待修复' in by_key['auto_tier1_ai_infra']['observation_reason']
    assert '领先幅度收窄' in by_key['auto_tier1_embodied']['observation_reason']
    for section in view['summary_sections']:
        for item in section['items']:
            assert not any(term in item['reason'] for term in ('MA20', 'RS20', 'pp', '条件成立', '支持成立'))
    for row in view['groups']:
        source = next(r for r in facts['groups'] if r['key'] == row['key'])
        for path, value in row['trace'].items():
            actual = source
            for part in path.split('.'):
                actual = actual.get(part) if isinstance(actual, dict) else None
            assert actual == value
        assert set(row['observation_fields']) <= row['trace'].keys()


def test_two_clocks_are_not_conflated(facts):
    physical = next(r for r in facts['groups'] if r['key'] == 'auto_tier1_embodied')
    view = explain_group(physical)
    assert 'RS20Δ回落 -3.69pp' in view['today']
    assert '较 2026-10-22' in view['today']
    assert 'RS20Δ 较5日前变化 +9.77pp' in activity_detail(physical)
    assert '10日参与度 +50.00pp' in activity_detail(physical)


def test_status_is_separate_from_phase_and_priority(facts):
    row = next(r for r in facts['groups'] if r['key'] == 'auto_tier1_embodied')
    phase = explain_group(row)['phase']
    row.update(status='NO_SETUP', first_trigger=False)
    result = explain_group(row)
    assert result['phase'] == phase  # NO_SETUP does not imply weak prices.
    assert result['priority'] == '常规观察'
    row.update(status='UNAVAILABLE', reason='MARKET_CLOSED')
    result = explain_group(row)
    assert (result['phase'], result['priority']) == ('无法判定', '待数据完整')
    assert attention_view(dict(groups=[row]))['highlighted_keys'] == []
    assert '不代表没有机会' in attention_view(dict(groups=[row]))['headline']


@pytest.mark.parametrize('missing', ['metrics', 'conditions', 'data_complete'])
def test_missing_explanation_inputs_fail_closed(facts, missing):
    row = deepcopy(facts['groups'][1])
    row.pop(missing)
    assert explain_group(row)['priority'] == '待数据完整'


@pytest.mark.parametrize('value', [None, float('nan'), float('inf')])
def test_invalid_metrics_never_become_a_positive_explanation(facts, value):
    row = deepcopy(facts['groups'][1])
    row['metrics']['nav'] = value
    assert explain_group(row)['phase'] == '无法判定'


def test_ties_do_not_invent_a_winner_and_returns_do_not_rank(facts):
    selected = attention_view(facts)['highlighted_keys']
    for row in facts['groups']:
        row['first_trigger'] = not row['first_trigger']
        row['metrics']['nav_return_1d'] = 10
    assert attention_view(facts)['highlighted_keys'] == selected
    peer = deepcopy(facts['groups'][1])
    peer['key'] = 'another_peer'
    facts['groups'].append(peer)
    view = attention_view(facts)
    assert not view['highlighted_keys']
    assert '同一优先观察档' in view['headline']
    assert len(view['groups']) == 10


def test_no_priority_and_no_previous_day_are_explicit(facts):
    facts['groups'] = [deepcopy(facts['groups'][3])]
    row = facts['groups'][0]
    row['previous_observation_date'] = None
    view = attention_view(facts)
    assert '没有证据足够一致' in view['headline']
    assert '较 2026' not in view['groups'][0]['today']
    row.update(status='WATCH', first_trigger=False)
    assert '无前次有效对照' in explain_group(row)['event']


@pytest.mark.parametrize('status,transition', [('EMERGING', 'UPGRADE'), ('WATCH', 'DOWNGRADE'), ('NO_SETUP', 'EXPIRED')])
def test_changes_remain_visible(facts, status, transition):
    row = deepcopy(facts['groups'][1])
    row.update(status=status, previous_status='CONFIRMED', transition=transition)
    assert f'CONFIRMED → {status}' in explain_group(row)['today']


def test_common_limits_once_and_unique_limits_remain_local(facts):
    html, _ = activity_summary('20261023', facts)
    for limit in facts['groups'][0]['limitations']:
        assert html.count(limit) == 1
    assert 'validation_status=UNVALIDATED' in html
    assert 'price_basis_verified=false' in html
    assert 'QR结构指数' not in html  # Full numerical evidence stays in group detail.
    assert html.count('href="#stock-group-') == 9
    row = facts['groups'][0]
    assert 'HK:00316 历史限制' in activity_risks(row, facts)
    assert 'HK:00316 历史限制' not in activity_risks(facts['groups'][1], facts)
    row['limitations'].append('仅此组的异常 <需复核>')
    assert '仅此组的异常 &lt;需复核&gt;' in activity_risks(row, facts)
    row['issues'].append({'reason': '<script>invalid</script>'})
    assert '<script>' not in activity_risks(row, facts)


@pytest.mark.parametrize('metrics,conditions,phase', [
    ({'nav': 100, 'ma20': 100}, {'structure': True, 'support': True}, '修复转强'),
    ({'nav': 101, 'ma20': 100}, {'price_turn': True}, '上方修复'),
    ({'nav': 100, 'ma20': 100}, {'price_turn': True}, '反弹修复'),
    ({'nav': 100, 'ma20': 100, 'ma5': 100, 'return_5d': .01}, {}, '整理待观察'),
])
def test_phase_fallbacks_and_equality_boundaries(facts, metrics, conditions, phase):
    row = deepcopy(facts['groups'][3])
    row['conditions'] = {key: False for key in row['conditions']}
    row['metrics'].update(metrics)
    row['conditions'].update(conditions)
    assert explain_group(row)['phase'] == phase


def test_zero_changes_are_hidden_but_evidence_is_kept(facts):
    row = deepcopy(facts['groups'][1])
    view = explain_group(row)
    assert '+0.00pp' not in view['today']
    assert '10日上涨范围' not in view['today']
    assert '5日上涨范围收窄 -12.50pp' in view['today']
    assert 'RS20Δ改善 +2.12pp' in view['today']
    assert '10日参与度 +0.00pp' in activity_detail(row)
    assert view['trace']['changes.participation_10d'] == 0
    assert len(view['developments']) == 2


@pytest.mark.parametrize('tiny', [0, -0.0, .000001, -.000001])
def test_rounding_zero_is_not_a_significance_threshold(facts, tiny):
    row = deepcopy(facts['groups'][1])
    row['leader_clues'] = []
    row['changes'] = dict(rs_20d_pct=tiny, participation_10d=tiny, participation_5d=tiny)
    assert explain_group(row)['today'] == '暂无新增变化（按显示精度）'
    row['changes']['rs_20d_pct'] = .01
    assert 'RS20Δ改善 +0.01pp' in explain_group(row)['today']
    row['changes']['rs_20d_pct'] = None
    assert explain_group(row)['today'] == '变化数据不齐'


def test_first_event_state_changes_and_new_leaders_survive_zero_deltas(facts):
    row = deepcopy(facts['groups'][6])
    row['changes'] = dict(rs_20d_pct=0, participation_10d=0, participation_5d=0)
    assert explain_group(row)['today'].startswith('首次 WATCH')
    row.update(first_trigger=False, status='EMERGING', previous_status='WATCH')
    assert explain_group(row)['today'].startswith('WATCH → EMERGING')
    assert explain_group(row)['opportunity'] == '已有机会'
    row.update(status='NO_SETUP', previous_status='NO_SETUP')
    row['leader_clues'] = [dict(first_clue=True), dict(first_clue=False)]
    view = explain_group(row)
    assert view['opportunity'] == '常规观察'
    assert view['today'] == '1 条新增个股线索（非组级启动）'


def test_new_weak_watch_does_not_outrank_existing_stronger_evidence(facts):
    view = attention_view(facts)
    positions = {r['key']: i for i, r in enumerate(view['groups'])}
    assert positions['auto_tier1_embodied'] < positions['hk_resources_shipping']
    assert next(r for r in view['groups'] if r['key'] == 'hk_resources_shipping')['opportunity'] == '新增机会'
