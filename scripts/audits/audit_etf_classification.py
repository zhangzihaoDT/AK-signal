"""只读输入，输出重新识别清单；不覆盖 Master、日报或 latest。

python -m scripts.audits.audit_etf_classification --input INPUT.csv --output NEW_DIR
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from src.etf_signal.classifier import CLASSIFICATION_COLUMNS, classify_all, load_config
from src.common.paths import config_dir


def audit(source: Path, output: Path) -> dict:
    before = (pd.read_csv(source, dtype={'fund_code': str}) if source.suffix == '.csv'
              else pd.read_parquet(source))
    if before.fund_code.duplicated().any():
        raise ValueError('Duplicate fund_code in classification input')
    after = classify_all(before)
    old_bucket = before.get('asset_bucket', pd.Series('', index=before.index)).fillna('')
    old_unclassified = old_bucket.str.strip().eq('')
    direction_known = after.investment_direction.ne('待核实')
    market_known = after.investment_market.ne('待核实')
    cfg = load_config()
    summary = {
        'source': str(source.resolve()), 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'config_sha256': hashlib.sha256((config_dir() / 'etf_classification.yaml').read_bytes()).hexdigest(),
        'classification_version': cfg['version'], 'total': len(after),
        'old_unclassified': int(old_unclassified.sum()),
        'old_unclassified_direction_identified': int((old_unclassified & direction_known).sum()),
        'old_unclassified_both_identified': int((old_unclassified & direction_known & market_known).sum()),
        'market_pending': int((~market_known).sum()), 'direction_pending': int((~direction_known).sum()),
        'both_identified': int((market_known & direction_known).sum()),
        'investment_market': after.investment_market.value_counts().to_dict(),
        'investment_direction': after.investment_direction.value_counts().to_dict(),
        'priority_groups': {},
        'note': '名称规则推断，均未核验成分股；仅候选审计，不更新正式发布或原始 Master。',
    }
    for keyword in ('现金流', 'A500', '纳指'):
        selected = after[after.fund_name.str.contains(keyword, na=False)]
        summary['priority_groups'][keyword] = {
            'count': len(selected), 'investment_market': selected.investment_market.value_counts().to_dict(),
            'investment_direction': selected.investment_direction.value_counts().to_dict(),
        }
    columns = ['fund_code', 'fund_name', *CLASSIFICATION_COLUMNS, 'asset_bucket']
    receipt = after[columns].copy()
    receipt.insert(2, 'old_asset_bucket', old_bucket)
    receipt.insert(3, 'old_unclassified', old_unclassified)
    output.mkdir(parents=True, exist_ok=False)
    receipt.to_csv(output / 'classification_candidates.csv', index=False)
    receipt[~(market_known & direction_known)].to_csv(output / 'pending.csv', index=False)
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.input, args.output), ensure_ascii=False, indent=2))
