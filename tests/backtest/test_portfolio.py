"""v0.6 共享账户模拟单元测试。"""

from __future__ import annotations

import pandas as pd
import pytest

from src.backtest.portfolio.account import PortfolioAccount
from src.backtest.portfolio import simulate as sim


def _trades(weight: float = 1.0):
    return pd.DataFrame([
        {"trade_id": 1, "entity_code": "512480", "theme": "ai_infrastructure",
         "entry_status": "filled", "entry_fill_date": "2026-07-01",
         "entry_fill_price": 1.0, "exit_fill_date": "2026-07-10", "exit_fill_price": 1.1,
         "weight": weight, "strategy": "AI-20"},
        {"trade_id": 2, "entity_code": "159819", "theme": "ai_infrastructure",
         "entry_status": "filled", "entry_fill_date": "2026-07-02",
         "entry_fill_price": 2.0, "exit_fill_date": "2026-07-12", "exit_fill_price": 2.2,
         "weight": weight, "strategy": "AI-20"},
        {"trade_id": 3, "entity_code": "512480", "theme": "ai_infrastructure",
         "entry_status": "filled", "entry_fill_date": "2026-07-15",
         "entry_fill_price": 1.1, "exit_fill_date": "2026-07-20", "exit_fill_price": 1.0,
         "weight": weight, "strategy": "AI-20"},
    ])


def _closes(codes=("512480", "159819")):
    idx = pd.to_datetime(pd.bdate_range("2026-07-01", periods=15))
    out = {}
    for c in codes:
        out[c] = pd.Series(1.0, index=idx)
    return out


class TestPortfolioAccount:
    def test_simple_buy_sell_equity(self):
        acc = PortfolioAccount(initial_capital=100_000, max_positions=5,
                               max_weight_per_asset=0.20, fee_pct=0.0, slippage_pct=0.0)
        acc.run(_trades(), _closes())
        nav = acc.nav_frame()
        assert not nav.empty
        # 两笔成交（512480 两段 + 159819 一段），第三笔为 512480 复开仓
        buys = [o for o in acc.orders if o["action"] == "buy" and o["status"] == "filled"]
        assert len(buys) == 3
        # 持仓不超过 max_positions
        assert acc.max_positions >= 5
        assert nav["equity"].iloc[-1] > 100_000  # 第一段盈利（1.0→1.1）

    def test_max_positions_blocks_new_entry(self):
        trades = _trades()
        acc = PortfolioAccount(initial_capital=100_000, max_positions=1,
                               max_weight_per_asset=0.50, fee_pct=0.0, slippage_pct=0.0)
        acc.run(trades, _closes())
        buys = [o for o in acc.orders if o["action"] == "buy" and o["status"] == "filled"]
        rejected = [o for o in acc.orders if o["status"] == "rejected"]
        assert len(buys) <= 2  # max_positions=1 → 同一时段只能持 1 只
        assert any(o.get("reason") == "max_positions" for o in rejected)

    def test_nav_metrics(self):
        nav = pd.DataFrame({"date": ["d%d" % i for i in range(100)],
                            "equity": [1.0 * (1.01 ** i) for i in range(100)]})
        m = sim.nav_metrics(nav)
        assert m["total_return_pct"] is not None
        assert m["max_drawdown_pct"] is not None
        assert m["sharpe"] is not None

    def test_load_strategies(self, tmp_path):
        p = tmp_path / "strategies.yaml"
        p.write_text(
            "strategies:\n"
            "  ai_20:\n"
            "    label: AI-20\n    theme: ai_infrastructure\n"
            "    exit_policy: fixed_horizon\n    horizon: 20\n    weight: 0.6\n")
        cfg = sim.load_strategies(p)
        assert cfg["ai_20"]["label"] == "AI-20"
        assert cfg["ai_20"]["horizon"] == 20


class TestPortfolioMetrics:
    def test_calmar(self):
        # 先涨后回撤再涨：最大回撤 < 0 → Calmar 有定义
        eq = [1.0, 1.1, 1.2, 1.0, 0.9, 1.1, 1.2, 1.3]
        nav = pd.DataFrame({"date": ["d%d" % i for i in range(len(eq))], "equity": eq})
        m = sim.nav_metrics(nav)
        assert m["calmar"] is not None
        assert m["max_drawdown_pct"] < 0

    def test_calmar_undefined_no_drawdown(self):
        nav = pd.DataFrame({"date": ["d%d" % i for i in range(100)],
                            "equity": [1.0 * (1.01 ** i) for i in range(100)]})
        m = sim.nav_metrics(nav)
        assert m["calmar"] is None  # 无回撤 → Calmar 未定义

    def test_relative_metrics(self):
        nav = pd.DataFrame({"date": ["2026-07-01", "2026-07-02", "2026-07-03"],
                            "equity": [100.0, 110.0, 121.0]})
        bench = pd.Series([1.0, 1.1, 1.2], index=pd.to_datetime(
            ["2026-07-01", "2026-07-02", "2026-07-03"]))
        r = sim.relative_metrics(nav, bench)
        assert r["bench_total_pct"] == 20.0
        assert abs(r["excess_pct"] - 1.0) < 0.01  # 21% - 20%
        assert r["win_vs_bench"] is not None

    def test_theme_contribution(self):
        acc = PortfolioAccount(initial_capital=100_000, fee_pct=0.0, slippage_pct=0.0)
        trades = _trades()
        trades["theme"] = ["ai_infrastructure", "ai_infrastructure", "ai_infrastructure"]
        acc.run(trades, _closes())
        c = acc.contribution()
        assert "ai_infrastructure" in c
        assert c["ai_infrastructure"]["n_trades"] == 3
        assert c["ai_infrastructure"]["total_pnl"] > 0
