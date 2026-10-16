from src.market_facts.stock_universe import observation_stock_items


def test_research_pool_additions_are_observation_only():
    items=observation_stock_items()
    added=[i for i in items if i.asset.category=='research_observation']
    assert all(i.participation=='monitor_only' for i in added)
    assets={(i.asset.market,i.asset.symbol) for i in items}
    assert ('HK','01378') in assets
    assert ('CN','603009') in assets
    assert len(assets)==69
