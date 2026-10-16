"""Observation-only stock registrations; research membership grants no strategy rights."""
from dataclasses import replace
from src.common.paths import project_root
from src.selection.universe import load_universe_items, UniverseItem, _infer_exchange
from src.trend_engine.asset import Asset
from src.trend_engine.inputs import stock_items
from src.research.baskets.config import load_baskets, expand_constituents


def observation_stock_items():
    items = stock_items(load_universe_items(project_root()/'config/selection_universe.yaml'))
    items = [replace(i, asset=replace(i.asset, symbol=i.asset.symbol.zfill(5 if i.asset.market=='HK' else 6))) for i in items]
    for key, basket in load_baskets().items():
        for member in expand_constituents({**basket, 'key':key}):
            market = member['market']
            symbol = member['symbol'].zfill(5 if market == 'HK' else 6)
            asset = Asset(symbol, member['name'], market,
                          exchange='HKEX' if market=='HK' else _infer_exchange(symbol),
                          currency='HKD' if market=='HK' else 'CNY', category='research_observation')
            items.append(UniverseItem(asset=asset, bucket='research', bucket_label='研究观察',
                theme=key, theme_label=basket['label'], tier=member['group'],
                tier_label=member['group_label'], participation='monitor_only'))
    return items
