"""Read-only, hash-checked research comparison preview, separate from Daily facts."""
import hashlib
import json
from pathlib import Path


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pin_comparison(root: Path, output: Path):
    root, output = root.resolve(), output.resolve()
    # Reuse the Worker's existing report attachment scope; never widen file access.
    output.relative_to(root / '.run_day/runs')
    pointer = root / 'data/research/baskets/comparison_publication.json'
    pointer.parent.mkdir(parents=True, exist_ok=True)
    value = {'directory': str(output.relative_to(root)),
             'manifest_sha256': _sha(output / 'comparison_manifest.json'),
             'report_sha256': _sha(output / 'basket_compare.html')}
    temp = pointer.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False) + '\n')
    temp.replace(pointer)


def comparison_view(root: Path):
    import pandas as pd
    root = root.resolve()
    pointer = root / 'data/research/baskets/comparison_publication.json'
    if not pointer.exists():
        return None, None
    p = json.loads(pointer.read_text())
    folder = (root / p['directory']).resolve()
    folder.relative_to(root / '.run_day/runs')
    manifest_path, report = folder / 'comparison_manifest.json', folder / 'basket_compare.html'
    if _sha(manifest_path) != p['manifest_sha256'] or _sha(report) != p['report_sha256']:
        raise ValueError('research publication hash mismatch')
    manifest = json.loads(manifest_path.read_text())
    path = folder / 'comparison_nav.csv'
    if _sha(path) != manifest['data_sha256']:
        raise ValueError('research data hash mismatch')
    curves = pd.read_csv(path)
    if not 2 <= len(curves) <= 1000 or len(manifest['baskets']) > 12:
        raise ValueError('research preview out of bounds')
    if curves.isna().any().any() or curves.date.duplicated().any():
        raise ValueError('invalid research curves')
    dates = curves.date.astype(str).tolist()
    if dates[0] != manifest['start'] or dates[-1] != manifest['end']:
        raise ValueError('research date mismatch')
    series = [{'id': b['key'], 'label': b['label'], 'values': curves[b['key']].round(3).tolist()}
              for b in manifest['baskets']]
    series.append({'id': 'benchmark', 'label': '沪深300', 'benchmark': True,
                   'values': curves.hs300.round(3).tolist()})
    preview = {'title': '观察篮子 · 长期走势', 'startDate': manifest['start'],
               'endDate': manifest['end'], 'unit': '起点=100', 'dates': dates, 'series': series,
               'note': '独立研究快照 · 本币前复权价格 · 固定初始权重，晚上市资金先留作现金；非实际仓位收益，未验证含息总回报。',
               'artifactDigest': p['report_sha256']}
    artifact = {'file': str(report), 'name': report.name, 'extension': '.html',
                'mimeType': 'text/html', 'size': report.stat().st_size, 'digest': p['report_sha256'],
                'runId': 'research-' + p['manifest_sha256'][:16], 'targetDate': manifest['end'].replace('-', '')}
    return preview, artifact
