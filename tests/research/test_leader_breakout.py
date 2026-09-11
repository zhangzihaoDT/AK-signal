"""Leader Breakout Event V1 核心逻辑测试（只读，确定性）。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.research.leader_breakout import spec as lb_spec
from src.research.leader_breakout.events import (
    _drawdown_bucket,
    build_events,
    build_price_panel,
    build_stock_history,
    theme_confirmation_series,
)
from src.research.leader_breakout.study import (
    _curve_metrics,
    _date_block_bootstrap,
    _single_stock_share,
    _stats,
)


def test_spec_frozen():
    s = lb_spec.load_spec()
    assert s.rule_id == "LEADER_BREAKOUT_V1"
    assert s.status == "FROZEN_RESEARCH_HYPOTHESIS"
    assert s.event.score_threshold == 70
    assert s.event.rank_trend == 1
    assert s.event.cooldown_trading_days == 20
    assert s.returns.primary_horizon == 20
    assert set(s.event.themes) == {"ai_infrastructure", "china_auto_global", "high_cashflow"}


def test_drawdown_bucket_boundaries():
    b = (0.10, 0.20, 0.30)
    assert _drawdown_bucket(0.05, b) == "<10%"
    assert _drawdown_bucket(0.15, b) == "<20%"
    assert _drawdown_bucket(0.25, b) == "<30%"
    assert _drawdown_bucket(0.40, b) == ">=30%"
    assert _drawdown_bucket(None, b) == "na"


def test_stats_basic():
    assert _stats([0.1, -0.1, 0.2])["n"] == 3
    assert _stats([0.1, -0.1, 0.2])["median"] == 0.1
    assert _stats([None, np.nan])["n"] == 0
    assert _stats([0.1, 0.2])["win_rate"] == 1.0


def test_single_stock_share():
    ev = pd.DataFrame({"symbol": ["a", "a", "b"], "exc_20": [0.05, 0.05, 0.10]})
    # b 的 0.10 是全部正超额 0.20 的一半
    share = _single_stock_share(ev, "exc_20")
    assert share is not None and abs(share - 0.5) < 1e-9
    assert _single_stock_share(pd.DataFrame({"symbol": [], "exc_20": []}), "exc_20") is None


def test_date_block_bootstrap_deterministic_and_sign():
    ev = pd.DataFrame({
        "event_date": pd.to_datetime(["2024-01-02"] * 3 + ["2024-01-03"] * 3),
        "exc_20": [0.02, 0.03, 0.01, 0.02, 0.01, 0.03],
    })
    a = _date_block_bootstrap(ev, "exc_20", 200, 42, 0.95)
    b = _date_block_bootstrap(ev, "exc_20", 200, 42, 0.95)
    assert a == b
    assert a["pass"] is True and a["lo"] > 0
    neg = ev.assign(exc_20=-ev["exc_20"])
    assert _date_block_bootstrap(neg, "exc_20", 200, 42, 0.95)["pass"] is False


def test_curve_metrics_positive_drift():
    s = pd.Series([0.01] * 30)
    m = _curve_metrics(s)
    assert m["total"] > 0 and m["max_drawdown"] <= 0
    assert m["sharpe"] is None or m["sharpe"] > 0


def test_build_events_invariants():
    s = lb_spec.load_spec()
    from src.common.paths import selection_universe_path
    from src.selection.universe import load_universe_items
    from src.trend_engine import inputs as trend_inputs

    items = load_universe_items(selection_universe_path())
    history = build_stock_history(trend_inputs.stock_items(items))
    conf = theme_confirmation_series(s)
    members = {sym for sym in history}
    panel = build_price_panel(sorted(members))
    ev, meta = build_events(s, history=history, conf=conf, panel=panel,
                            start_date="2024-01-01", end_date="2024-12-31")
    if ev.empty:
        return
    assert (ev["score_trend"] >= s.event.score_threshold).all()
    assert (ev["prev_score"] < s.event.score_threshold).all()
    assert (ev["rank_trend"] == 1).all()
    assert (ev["rs20"] > 0).all()
    assert set(ev["theme"]).issubset(set(s.event.themes))
    # 去重：同一 (symbol,event_date) 唯一
    assert not ev.duplicated(subset=["symbol", "event_date"]).any()
    for col in ("above_ma60", "above_ma120", "new_high_20d", "theme_breadth", "drawdown_bucket"):
        assert col in ev.columns
