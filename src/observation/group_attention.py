"""Deterministic reading guidance over accepted V2 facts; never produces signals.

The ordered branches and their field references are documented in
docs/DAILY_GROUP_ATTENTION.md. No prices, thresholds or state histories are rebuilt.
"""
from math import isfinite

DISPLAY_VERSION = 'group_attention_v2'
ACTIVE = {'WATCH', 'EMERGING', 'CONFIRMED'}
PRIORITIES = ('优先研究', '重点观察', '观察持续性', '持续跟踪', '暂不优先', '常规观察', '待数据完整')
# Editorial aliases only. Identity, membership and full labels remain in the facts.
SHORT_LABELS = {
    'china_auto_global_ex_oem': '汽车全球化（非 OEM）',
    'china_auto_global': '汽车全球化（OEM）',
    'ai_capex': 'AI Capex', 'memory_and_storage': '半导体存储',
    'auto_tier1_ai_infra': '汽车热管理', 'auto_tier1_embodied': 'Physical AI',
}
METRICS = ('nav', 'ma5', 'ma20', 'return_5d', 'return_20d', 'rs_20d_pct',
           'participation_5d', 'participation_10d', 'participation_20d')
CONDITIONS = ('price_turn', 'structure', 'support', 'rs_early', 'participation_early', 'sudden')


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def explain_group(row):
    """Return a separate view model; input facts are never modified."""
    m, c = row.get('metrics') or {}, row.get('conditions') or {}
    status = row.get('status', 'UNAVAILABLE')
    complete = (status != 'UNAVAILABLE' and row.get('data_complete') is True
                and all(_finite(m.get(k)) for k in METRICS)
                and all(isinstance(c.get(k), bool) for k in CONDITIONS))
    if not complete:
        phase, reason, priority = '无法判定', '数据或展示依据不完整，未作趋势阶段判定', '待数据完整'
        phase_fields = ['status', 'data_complete', 'reason']
        priority_fields = phase_fields
    else:
        above20 = m['nav'] > m['ma20']
        short_up = m['nav'] > m['ma5'] and m['return_5d'] > 0
        if above20 and c['price_turn'] and c['support'] and m['rs_20d_pct'] >= 0 and m['return_20d'] > 0:
            phase, reason = '上行延续候选', '价格高于MA20、20日收益与RS20Δ为正，参与度及RS初步支持成立'
            phase_fields = ['metrics.nav', 'metrics.ma20', 'metrics.return_20d', 'metrics.rs_20d_pct', 'conditions.price_turn', 'conditions.support']
        elif c['structure'] and c['support']:
            phase, reason = '修复转强', '价格转强结构与参与度及RS初步支持同时成立'
            phase_fields = ['conditions.structure', 'conditions.support']
        elif above20 and c['price_turn'] and (c['rs_early'] or c['participation_early']):
            phase, reason = '转强观察', '价格高于MA20且止跌条件成立，RS或参与度出现早期改善'
            phase_fields = ['metrics.nav', 'metrics.ma20', 'conditions.price_turn', 'conditions.rs_early', 'conditions.participation_early']
        elif c['price_turn']:
            phase = '上方修复' if above20 else '反弹修复'
            reason = '价格止跌条件成立，' + ('仍高于MA20' if above20 else '仍低于或等于MA20')
            phase_fields = ['conditions.price_turn', 'metrics.nav', 'metrics.ma20']
        elif short_up and (c['rs_early'] or c['participation_early']):
            phase, reason = '初步企稳', '价格高于MA5且5日收益为正，RS或参与度改善；完整止跌条件尚未成立'
            phase_fields = ['metrics.nav', 'metrics.ma5', 'metrics.return_5d', 'conditions.rs_early', 'conditions.participation_early', 'conditions.price_turn']
        elif m['return_5d'] <= 0:
            phase = ('短期走弱' if above20 else '弱势异动' if c['sudden']
                     else '弱势整理' if m['rs_20d_pct'] >= 0 else '弱势')
            reason = ('价格仍高于MA20' if above20 else '价格低于或等于MA20') + '，5日收益非正'
            if phase == '弱势异动':
                reason += '；存在集体单日异动，但价格止跌条件未成立'
            phase_fields = ['metrics.nav', 'metrics.ma20', 'metrics.return_5d', 'metrics.rs_20d_pct', 'conditions.sudden']
        else:
            phase, reason = '整理待观察', '短期价格与RS、参与度尚未形成一致的修复描述'
            phase_fields = [f'metrics.{k}' for k in METRICS] + [f'conditions.{k}' for k in CONDITIONS]

        if status not in ACTIVE:
            priority, priority_fields = '常规观察', ['status']
        elif ((c['structure'] and c['support']) or
              (c['price_turn'] and c['rs_early'] and c['participation_early'])):
            priority = '优先研究'
            priority_fields = ['status', 'conditions.structure', 'conditions.support', 'conditions.price_turn', 'conditions.rs_early', 'conditions.participation_early']
        elif short_up and c['rs_early'] and c['participation_early']:
            priority = '重点观察'
            priority_fields = ['status', 'metrics.nav', 'metrics.ma5', 'metrics.return_5d', 'conditions.rs_early', 'conditions.participation_early']
        elif c['price_turn'] and (c['rs_early'] or c['participation_early']):
            priority = '观察持续性'
            priority_fields = ['status', 'conditions.price_turn', 'conditions.rs_early', 'conditions.participation_early']
        elif c['structure'] or c['price_turn']:
            priority = '持续跟踪'
            priority_fields = ['status', 'conditions.structure', 'conditions.price_turn']
        else:
            priority, priority_fields = '暂不优先', ['status', 'conditions.structure', 'conditions.price_turn']

    previous = row.get('previous_observation_date')
    if status == 'UNAVAILABLE':
        event = '未作今日判定'
    elif status == 'WATCH' and row.get('first_trigger'):
        event = '首次 WATCH'
    elif previous and row.get('previous_status') != status:
        event = f"{row.get('previous_status')} → {status}"
    elif previous and status in ACTIVE:
        event = f'持续 {status}'
    elif status == 'NO_SETUP':
        event = 'NO_SETUP · 无启动候选'
    else:
        event = f'{status} · 无前次有效对照'
    if not complete:
        opportunity = '待判定'
    elif status == 'WATCH' and row.get('first_trigger'):
        opportunity = '新增机会'
    elif status in ACTIVE:
        opportunity = '已有机会'
    else:
        opportunity = '常规观察'
    priority_label = priority
    if opportunity == '已有机会':
        priority_label = {'优先研究': '优先跟踪', '重点观察': '重点跟踪'}.get(priority, priority)

    changes = row.get('changes') or {}
    change_fields = ['status', 'first_trigger', 'previous_status', 'previous_observation_date']
    candidates = []
    if complete and previous:
        # Only hide values which render as zero at the existing two-decimal
        # precision. This is formatting, not a significance or signal threshold.
        for field, label, multiplier, up, down in [
            ('participation_10d', '10日上涨范围', 100, '扩大', '收窄'),
            ('participation_5d', '5日上涨范围', 100, '扩大', '收窄'),
            ('rs_20d_pct', 'RS20Δ', 1, '改善', '回落'),
        ]:
            change_fields.append(f'changes.{field}')
            value = changes.get(field)
            if not _finite(value) or f'{abs(value * multiplier):.2f}' == '0.00':
                continue
            candidates.append(dict(field=field, value=value,
                text=f"{label}{up if value > 0 else down} {value * multiplier:+.2f}pp"))
    # Evidence losses first, then 10-day breadth, 5-day breadth, relative strength.
    # Keep at most two developments; all numbers remain in the detailed evidence.
    candidates.sort(key=lambda item: item['value'] >= 0)
    developments = candidates[:2]
    event_changed = (status == 'WATCH' and row.get('first_trigger')) or (
        previous and row.get('previous_status') != status)
    parts = [event] if event_changed or status == 'UNAVAILABLE' else []
    parts.extend(item['text'] for item in developments)
    new_leaders = sum(r.get('first_clue') is True for r in row.get('leader_clues', []))
    if new_leaders:
        parts.append(f'{new_leaders} 条新增个股线索（非组级启动）')
    change_fields.append('leader_clues')
    if not parts:
        parts = ['展示依据不齐，未作变化判定' if not complete else
                 '无上一有效观察日对照' if not previous else
                 '变化数据不齐' if not all(_finite(changes.get(k)) for k in
                     ('rs_20d_pct', 'participation_10d', 'participation_5d')) else
                 '暂无新增变化（按显示精度）']
    today = '；'.join(parts)
    if previous and (developments or event_changed):
        today += f'（较 {previous}）'

    # Plain-language conclusion; exact definitions and indicators stay in detail.
    observation_reason = {
        '无法判定': '资料尚不完整，暂不作判断',
        '上行延续候选': '价格回升获得更多成分支持，继续观察能否延续',
        '修复转强': '价格与上涨范围相互支持，继续观察能否站稳',
        '转强观察': '价格正在回升，关注上涨能否站稳',
        '上方修复': '价格正在修复，仍需观察持续性',
        '反弹修复': '价格开始回暖，中期仍待修复',
        '初步企稳': '价格出现企稳迹象，回升仍待稳固',
        '短期走弱': '短期价格回落，留意后续能否止跌',
        '弱势异动': '出现单日异动，价格尚未止跌',
        '弱势整理': '价格仍偏弱，暂作常规观察',
        '弱势': '价格和相对大盘表现仍偏弱',
        '整理待观察': '各项表现尚不一致，继续观察',
    }[phase]
    observation_fields = phase_fields + priority_fields
    if complete and previous:
        rs_change, breadth_change = changes.get('rs_20d_pct'), changes.get('participation_10d')
        short_change = changes.get('participation_5d')
        shown = {r['field'] for r in candidates}
        if 'participation_10d' in shown and breadth_change > 0:
            observation_reason = '近10日上涨的成分增多'
            if 'rs_20d_pct' in shown and rs_change < 0:
                observation_reason += ('，但相对大盘的领先幅度收窄' if m['rs_20d_pct'] > 0
                                       else '，但相对大盘表现回落')
            elif 'participation_5d' in shown and short_change < 0:
                observation_reason += '，但短线仍有分化'
            elif c['price_turn']:
                observation_reason = '价格回暖，' + observation_reason
        elif 'rs_20d_pct' in shown and rs_change > 0:
            observation_reason = '相对大盘表现改善'
            if 'participation_5d' in shown and short_change < 0:
                observation_reason += '，但近5日上涨的成分减少'
        if m['nav'] <= m['ma20'] and c['price_turn']:
            observation_reason += '；中期仍待修复'
        observation_fields += ['changes.rs_20d_pct', 'changes.participation_10d',
            'changes.participation_5d', 'previous_observation_date', 'metrics.rs_20d_pct',
            'metrics.nav', 'metrics.ma20', 'conditions.price_turn']
    fields = sorted(set(phase_fields + priority_fields + change_fields + observation_fields))
    def value(path):
        result = row
        for key in path.split('.'):
            result = result.get(key) if isinstance(result, dict) else None
        return result
    return dict(key=row['key'], label=SHORT_LABELS.get(row['key'], row['label']),
                full_label=row['label'], status=status, phase=phase, phase_reason=reason,
                priority=priority, priority_label=priority_label, opportunity=opportunity, observation_reason=observation_reason, today=today,
                event=event, developments=developments, previous_observation_date=previous,
                phase_fields=phase_fields, priority_fields=priority_fields,
                observation_fields=sorted(set(observation_fields)),
                trace={field: value(field) for field in fields})


def attention_view(activity):
    rows = [explain_group(r) for r in activity.get('groups', [])]
    # Same tier is a tie. Original registered order is retained within a tier.
    rows.sort(key=lambda r: PRIORITIES.index(r['priority']))
    selected = []
    for priority in PRIORITIES[:2]:
        peers = [r for r in rows if r['priority'] == priority]
        if len(selected) + len(peers) > 3:
            break  # Never break a tie just to invent a top three.
        selected.extend(peers)
    summary_sections = []
    for kind in ('新增机会', '已有机会'):
        peers = [r for r in selected if r['opportunity'] == kind]
        if peers:
            summary_sections.append(dict(kind=kind, items=[dict(key=r['key'], label=r['label'],
                reason=r['observation_reason']) for r in peers]))
    if selected:
        headline = '；'.join(section['kind'] + ('先看' if section['kind'] == '新增机会' else '继续跟踪') +
                             '、'.join(r['label'] for r in section['items']) for section in summary_sections) + '。'
    elif any(r['priority'] in PRIORITIES[:2] for r in rows):
        headline = '多个分组处于同一优先观察档，无法可靠缩减至1–3组；请并列查看速览，不强行选出第一名。'
    elif not rows or all(r['priority'] == '待数据完整' for r in rows):
        headline = '今日数据不足，无法安排优先观察分组；这不代表没有机会。'
    else:
        headline = '今日没有证据足够一致的优先观察分组，按速览跟踪变化。'
    return dict(display_version=DISPLAY_VERSION, headline=headline, summary_sections=summary_sections,
                highlighted_keys=[r['key'] for r in selected], groups=rows)
