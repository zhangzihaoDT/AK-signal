#!/usr/bin/env python3
"""Ad-hoc exploration page: IC 中证500股指期货 over the full registered history.

Reads only immutable, already-accepted inputs:
  * data/sources/cffex   CFFEX dated official files (registered partitions)
  * data/sources/longbridge/raw/core_indices   csi500 spot snapshots (bounded history)
and re-derives every figure through the production `consume` gate, so the page
cannot show a number the facts layer would not accept.

Not a published daily report. Output is a single lightweight HTML that links a
pinned plotly.js from the CDN; pass --inline for a fully offline (much larger) copy.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from collections import OrderedDict, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from plotly.offline import get_plotlyjs  # noqa: E402
from plotly.offline.offline import get_plotlyjs_version  # noqa: E402

from src.common.paths import project_root  # noqa: E402
from src.market_facts import derivatives as drv  # noqa: E402

BLUE, GOLD, CYAN = '#174A7C', '#C2842A', '#7ECDEB'
MUTED, INK = '#6B7C8F', '#1F2D3D'
SPOT_NA = 'SPOT_UNAVAILABLE'


def load_days(root: Path):
    """Every registered CFFEX session, re-derived through the production gate."""
    item, signature = drv.definitions(root)
    empty = {'usable': False, 'status': SPOT_NA, 'key': 'csi500', 'code': '000905',
             'provider': None, 'source_trade_date': None, 'close': None, 'snapshot_id': None}
    frames = OrderedDict()
    for pointer in sorted((root / 'data/sources/cffex/raw/daily/partitions').glob('*.json')):
        day = pointer.stem
        rows = drv.consume(root, day, item, signature, json.loads(pointer.read_text()), empty)
        if rows[0]['status'] == 'AVAILABLE':
            frames[day] = rows
    return frames


def load_spot(root: Path) -> dict[str, float]:
    """csi500 closes from accepted core-index snapshots (longest available window)."""
    closes: dict[str, float] = {}
    for path in glob.glob(str(root / 'data/sources/longbridge/raw/core_indices/runs/*/*/csi500.json')):
        for bar in json.loads(Path(path).read_text())['bars']:
            # Later snapshots win, but values are identical where they overlap.
            closes[bar['source_trade_date']] = bar['close']
    return closes


def main_series(frames):
    """Daily near-month (max open interest) contract: date -> row."""
    return [(d, next(r for r in rows if r['is_main_open_interest'])) for d, rows in frames.items()]


SLOTS = ('当月', '次月', '当季', '下季')


def slot_series(frames):
    """Four listed-expiry slots, each carrying that contract's own quoted price.

    Sorted by expiry month, not contract code: on a roll the slot simply becomes a
    different contract, so every plotted point is a real quoted close and the line
    never invents a synthetic multi-contract level. A slot goes empty once the
    exchange lists fewer than four contracts (e.g. after the front contract expires
    and before the next month is listed), which is shown as a gap, not interpolated.
    """
    ordered = sorted(frames.items())
    dates = [d for d, _ in ordered]
    series = {s: {'close': [], 'contract': []} for s in SLOTS}
    for _, rows in ordered:
        ranked = sorted(rows, key=lambda r: r['contract'][-4:])
        for i, slot in enumerate(SLOTS):
            if i < len(ranked):
                series[slot]['close'].append(ranked[i]['close'])
                series[slot]['contract'].append(ranked[i]['contract'])
            else:
                series[slot]['close'].append(None)
                series[slot]['contract'].append(None)
    # which slot currently holds the largest open interest, for context labels
    return dates, series


def build(args):
    root = getattr(args, 'root', None) or project_root()
    frames = load_days(root)
    spot = load_spot(root)
    days = list(frames)
    if not days:
        raise SystemExit('no registered CFFEX sessions')

    main = main_series(frames)
    main_dates_c = [d for d, _ in main]
    main_close = [r['close'] for _, r in main]

    # Four real-contract slot series (the authoritative price view; no stitching).
    slot_dates_c, slot_data = slot_series(frames)

    # ---- spot-aligned layer (only days where a same-day spot close exists) ----
    # `days` are compact YYYYMMDD (partition names); spot keys are ISO. Normalise both
    # to ISO so the rest of the page speaks one date vocabulary.
    compact = {d: f'{d[:4]}-{d[4:6]}-{d[6:]}' for d in days}
    iso = {v: k for k, v in compact.items()}
    main_dates = [compact[d] for d in main_dates_c]
    slot_dates = [compact[d] for d in slot_dates_c]
    slot_series_iso = {s: {'close': v['close'], 'contract': v['contract']}
                       for s, v in slot_data.items()}
    spot_days = [compact[d] for d in days if compact[d] in spot]
    spot_series = [(d, spot[d]) for d in spot_days]

    # basis for every contract on spot-covered days
    basis_rows = []
    for d in spot_days:
        for row in frames[iso[d]]:
            if row['return_status'] == 'OK':
                basis = row['close'] - spot[d]
                basis_rows.append({'date': d, 'contract': row['contract'], 'close': row['close'],
                                   'spot': spot[d], 'basis': basis,
                                   'rate': basis / (spot[d] or 1),
                                   'main': row['is_main_open_interest'],
                                   'oi': row['open_interest']})

    first, last = compact[days[0]], compact[days[-1]]
    first_main, last_main = main[0][1], main[-1][1]

    # ---- headline statistics over the full futures history ----
    # Extremes across EVERY listed contract (the cards say "任一挂牌合约"), not just
    # the near-month series: a far contract can print the true tail.
    all_quotes = [(row['close'], compact[d], row['contract']) for d, rows in frames.items() for row in rows]
    hi_q = max(all_quotes)
    lo_q = min(all_quotes)
    year_blocks = defaultdict(list)
    for d, c in zip(main_dates, main_close):
        year_blocks[d[:4]].append(c)

    years = []
    prev_close = None
    for y in sorted(year_blocks):
        block = year_blocks[y]
        years.append({'year': y, 'close': block[-1],
                      'chg': None if prev_close is None else block[-1] / prev_close - 1,
                      'high': max(block), 'low': min(block), 'n': len(block)})
        prev_close = block[-1]

    # ---- monthly table across the whole range ----
    months = OrderedDict()
    for d, c in zip(main_dates, main_close):
        months.setdefault(d[:7], []).append((d, c))
    month_rows, prev = [], None
    for ym, vals in months.items():
        close = vals[-1][1]
        month_rows.append({'month': ym, 'close': close,
                           'chg': None if prev is None else close / prev - 1,
                           'high': max(v for _, v in vals), 'low': min(v for _, v in vals),
                           'n': len(vals)})
        prev = close

    # ---- latest four-contract cross-section with real basis ----
    cross = []
    if last in spot_days:
        for row in frames[iso[last]]:
            sp = spot[last]
            cross.append({**row, 'basis_points': row['close'] - sp,
                          'basis_rate': row['close'] / sp - 1})
    notes = ('近月', '主力', '季月', '远月')
    for i, row in enumerate(cross):
        row['note'] = notes[i] if i < len(notes) else ''

    # ---- the spot-covered window in detail (all listed contracts per day) ----
    window_days = spot_days[-args.detail_days:] if spot_days else []
    recent_detail = []
    for d in window_days:
        rows = frames[iso[d]]
        entry = {'date': d, 'spot': spot.get(d)}
        for row in rows:
            entry[row['contract']] = {'close': row['close'], 'settle': row['settle'],
                                      'ret': row['return_1d'], 'vol': row['volume'],
                                      'oi': row['open_interest'],
                                      'main': row['is_main_open_interest']}
        recent_detail.append(entry)
    recent_contracts = sorted({r['contract'] for d in window_days for r in frames[iso[d]]})

    # ---- colour-legend labels describing exactly what each line is ----
    span_days = (len(days))
    spot_span = (spot_series[0][0], spot_series[-1][0], len(spot_series)) if spot_series else None

    payload = {
        'main': {'dates': main_dates, 'close': main_close,
                 'contracts': [r['contract'] for _, r in main]},
        'slots': {'order': list(SLOTS), 'dates': slot_dates,
                  'series': slot_series_iso},
        'spot': {'dates': [d for d, _ in spot_series], 'close': [c for _, c in spot_series]},
        'basis': basis_rows,
        'years': years,
        'months': month_rows,
        'cross': [{'contract': r['contract'], 'close': r['close'], 'settle': r['settle'],
                   'volume': r['volume'], 'open_interest': r['open_interest'],
                   'basis_points': r['basis_points'], 'basis_rate': r['basis_rate'],
                   'note': r['note']} for r in cross],
        'recent': {'contracts': recent_contracts, 'rows': recent_detail},
        'meta': {'first': first, 'last': last, 'sessions': span_days,
                 'first_main': {'contract': first_main['contract'], 'close': first_main['close']},
                 'last_main': {'contract': last_main['contract'], 'close': last_main['close']},
                 'high': {'date': hi_q[1], 'close': hi_q[0], 'contract': hi_q[2]},
                 'low': {'date': lo_q[1], 'close': lo_q[0], 'contract': lo_q[2]},
                 'spot_span': spot_span,
                 'spot_na': 'SPOT_UNAVAILABLE'},
    }
    write_html(payload, args.output, root, inline=getattr(args, 'inline', False))
    return payload


def write_html(payload, output, root, inline=False):
    data = json.dumps(payload, ensure_ascii=False).replace('<', '\\u003c')
    meta = payload['meta']
    template = (Path(__file__).parent / 'ic500_full_template.html').read_text()
    if inline:
        # Fully offline copy: embeds the whole bundle (≈4.7 MB) in one file.
        plotly_tag = '<script>__PLOTLY__</script>'.replace('__PLOTLY__', get_plotlyjs())
    else:
        # Pin the exact bundle version matching the installed plotly, so the page
        # renders identically and never silently picks up a newer release.
        plotly_tag = ('<script charset="utf-8" src="https://cdn.plot.ly/plotly-'
                      f'{get_plotlyjs_version()}.min.js"></script>')
    html = (template
            .replace('__PLOTLY_TAG__', plotly_tag)
            .replace('__DATA__', data))
    for key, value in {
        '__FIRST__': meta['first'], '__LAST__': meta['last'],
        '__SESSIONS__': f"{meta['sessions']:,}",
        '__SPOT_START__': meta['spot_span'][0] if meta['spot_span'] else '—',
        '__SPOT_END__': meta['spot_span'][1] if meta['spot_span'] else '—',
        '__SPOT_N__': str(meta['spot_span'][2] if meta['spot_span'] else 0),
    }.items():
        html = html.replace(key, value)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html)
    print(f'wrote {output} ({output.stat().st_size/1024:.0f} KB'
          f'{", inline plotly" if inline else ", CDN plotly " + get_plotlyjs_version()})')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--detail-days', type=int, default=90,
                        help='trading days of contract-level detail to emit (spot-covered tail)')
    parser.add_argument('--inline', action='store_true',
                        help='embed plotly.js instead of linking the CDN (large, offline)')
    parser.add_argument('--output', type=Path,
                        default=project_root() / 'outputs/exploration/ic500_trend_full_20261119.html')
    args = parser.parse_args()
    result = build(args)
    print(json.dumps(result['meta'], ensure_ascii=False, indent=2))
