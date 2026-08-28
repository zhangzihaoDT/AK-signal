"""v0.6 共享账户模拟单元测试。"""

from __future__ import annotations

import pandas as pd
import pytest

from src.backtest.portfolio.engine import PortfolioAccount
from src.backtest.portfolio import simulate as sim


def _trades(weight: float = 1.0):
    return pd.DataFrame([
        {"trade_id": 1, "entity_code": "512480", "theme": "ai_infrastructure",
         "entry_status": "filled", "entry_fill_date": "20260701",
         "entry_fill_price": 1.0, "exit_fill_date": "20260710", "exit_fill_price": 1.1,
         "weight": weight, "strategy": "AI-20"},
        {"trade_id": 2, "entity_code": "159819", "theme": "ai_infrastructure",
         "entry_status": "filled", "entry_fill_date": "20260702",
         "entry_fill_price": 2.0, "exit_fill_date": "20260714", "exit_fill_price": 2.2,
         "weight": weight, "strategy": "AI-20"},
        {"trade_id": 3, "entity_code": "512480", "theme": "ai_infrastructure",
         "entry_status": "filled", "entry_fill_date": "20260715",
         "entry_fill_price": 1.1, "exit_fill_date": "20260720", "exit_fill_price": 1.0,
         "weight": weight, "strategy": "AI-20"},
    ])


def _closes(codes=("512480", "159819")):
    idx = pd.to_datetime(pd.bdate_range("2026-07-01", periods=15))
    out = {}
    for c in codes:
        out[c] = pd.Series(1.0, index=idx)
    return out


def _calendar(n: int = 20, start: str = "2026-07-01"):
    return [d.strftime("%Y%m%d") for d in pd.bdate_range(start, periods=n)]


class TestPortfolioAccount:
    def test_simple_buy_sell_equity(self):
        acc = PortfolioAccount(initial_capital=100_000, max_positions=5,
                               max_weight_per_asset=0.20, fee_pct=0.0, slippage_pct=0.0)
        acc.run(_trades(), _closes(), _calendar())
        nav = acc.nav_frame()
        assert not nav.empty
        # 两笔成交（512480 两段 + 159819 一段），第三笔为 512480 复开仓
        buys = [o for o in acc.orders if o["action"] == "buy" and o["status"] == "filled"]
        assert len(buys) == 3
        assert nav["equity"].iloc[-1] > 100_000  # 第一段盈利（1.0→1.1）
        # 完整日频：日历每个交易日都有记录，且含 cash/mv/gross_exposure
        assert len(nav) == len(_calendar())
        assert {"cash", "position_market_value", "equity", "gross_exposure",
                "position_count", "daily_return"} <= set(nav.columns)

    def test_max_positions_blocks_new_entry(self):
        trades = _trades()
        acc = PortfolioAccount(initial_capital=100_000, max_positions=1,
                               max_weight_per_asset=0.50, fee_pct=0.0, slippage_pct=0.0)
        acc.run(trades, _closes(), _calendar())
        buys = [o for o in acc.orders if o["action"] == "buy" and o["status"] == "filled"]
        rejected = [o for o in acc.orders if o["status"] == "rejected"]
        assert len(buys) <= 2  # max_positions=1 → 同一时段只能持 1 只
        assert any(o.get("reason") == "max_positions" for o in rejected)

    def test_nav_metrics(self):
        dates = [d.strftime("%Y%m%d") for d in pd.bdate_range("2024-01-02", periods=100)]
        nav = pd.DataFrame({"date": dates,
                            "equity": [1.0 * (1.01 ** i) for i in range(100)]})
        m = sim.nav_metrics(nav)
        assert m["total_return_pct"] is not None
        assert m["max_drawdown_pct"] is not None
        assert m["sharpe"] is not None
        assert m["annualization_method"] == "calendar_days_365.25"

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
        dates = [d.strftime("%Y%m%d") for d in pd.bdate_range("2024-01-02", periods=len(eq))]
        nav = pd.DataFrame({"date": dates, "equity": eq})
        m = sim.nav_metrics(nav)
        assert m["calmar"] is not None
        assert m["max_drawdown_pct"] < 0

    def test_calmar_undefined_no_drawdown(self):
        dates = [d.strftime("%Y%m%d") for d in pd.bdate_range("2024-01-02", periods=100)]
        nav = pd.DataFrame({"date": dates, "equity": [1.0 * (1.01 ** i) for i in range(100)]})
        m = sim.nav_metrics(nav)
        assert m["calmar"] is None  # 无回撤 → Calmar 未定义

    def test_relative_metrics(self):
        dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-06-01", periods=25)]
        nav = pd.DataFrame({"date": dates,
                            "equity": [100.0 * (1.01 ** i) for i in range(25)]})
        bench = pd.Series([1.0 + 0.01 * i for i in range(25)],
                          index=pd.to_datetime(dates))
        r = sim.relative_metrics(nav, bench)
        assert r["bench_total_pct"] is not None
        assert r["excess_pct"] is not None
        assert r["daily_outperformance_rate"] is not None
        assert r["rolling_20d_outperformance_rate"] is not None

    def test_theme_contribution(self):
        acc = PortfolioAccount(initial_capital=100_000, fee_pct=0.0, slippage_pct=0.0)
        trades = _trades()
        trades["theme"] = ["ai_infrastructure", "ai_infrastructure", "ai_infrastructure"]
        acc.run(trades, _closes(), _calendar())
        c = acc.contribution()
        assert "ai_infrastructure" in c
        assert c["ai_infrastructure"]["n_trades"] == 3
        assert c["ai_infrastructure"]["total_pnl"] > 0


class TestBenchmark:
    def test_requested_range(self):
        signals = pd.DataFrame({"trade_date": ["20240102", "20240301", "20260501"]})
        assert sim.requested_range(signals) == ("20240102", "20260501")

    def test_benchmark_series_sh000300_coverage(self, tmp_path, monkeypatch):
        import src.backtest.portfolio.nav as nav_mod
        import src.backtest.portfolio.simulate as sim_mod
        # 构造覆盖完整的 sh000300 缓存
        df = pd.DataFrame({"date": pd.bdate_range("2024-01-02", periods=50),
                           "close": range(50)})
        monkeypatch.setattr(nav_mod, "raw_dir", lambda: tmp_path)
        df.to_csv(tmp_path / "_benchmark_sh000300.csv", index=False, encoding="utf-8")
        cache = {"combined": pd.DataFrame()}
        close, meta = sim.benchmark_series(cache, "sh000300",
                                           start="20240102", end="20240229")
        assert meta["covers"] is True
        assert meta["fallback_used"] is False

    def test_benchmark_fallback_when_uncovered(self, tmp_path, monkeypatch):
        import src.backtest.portfolio.nav as nav_mod
        import src.backtest.portfolio.simulate as sim_mod
        df = pd.DataFrame({"date": pd.bdate_range("2026-06-01", periods=10),
                           "close": range(10)})
        monkeypatch.setattr(nav_mod, "raw_dir", lambda: tmp_path)
        df.to_csv(tmp_path / "_benchmark_sh000300.csv", index=False, encoding="utf-8")
        cache = {"combined": pd.DataFrame({"date": ["2026-06-01"], "fund_code": ["510300"],
                                           "open": [1.0], "close": [1.0]})}
        close, meta = sim.benchmark_series(cache, "sh000300", start="20240102", end="20260501")
        # 缓存未覆盖且允许 fallback → 用 510300
        assert meta["fallback_used"] is True
        assert meta["symbol"] == "510300"
        # 不允许 fallback → 仍返回原序列（覆盖检查失败由调用方处理）
        close2, meta2 = sim.benchmark_series(cache, "sh000300", start="20240102",
                                             end="20260501", fallback="", allow_fallback=False)
        assert meta2["fallback_used"] is False


class TestConstruction:
    def test_compute_position_value_equality_score_cash(self):
        from src.backtest.portfolio.allocation import compute_position_value
        eq = compute_position_value(1_000_000, 1.0, 5, 0.30)
        assert abs(eq - 200_000) < 1  # 20% 等权
        scored = compute_position_value(1_000_000, 1.0, 5, 0.30, score=90.0)
        assert abs(scored - 300_000) < 1  # 90/50 → 36% → cap 30%
        cash = compute_position_value(1_000_000, 1.0, 5, 0.30, deploy_ratio=0.6)
        assert abs(cash - 120_000) < 1  # 60% 资金动用

    def test_top_n_filters_entities(self):
        from src.backtest.portfolio.construction import _top_n_trades
        trades = pd.DataFrame([
            {"trade_id": 1, "entity_code": "A", "entry_status": "filled", "entry_score": 90.0},
            {"trade_id": 2, "entity_code": "A", "entry_status": "filled", "entry_score": 60.0},
            {"trade_id": 3, "entity_code": "B", "entry_status": "filled", "entry_score": 50.0},
            {"trade_id": 4, "entity_code": "C", "entry_status": "filled", "entry_score": 40.0},
        ])
        top2 = _top_n_trades(trades, 2)
        assert set(top2["entity_code"]) == {"A", "B"}


class TestSpecDrivenBehavior:
    """行为来自统一 Strategy Specification，不来自代码硬编码。"""

    def test_entry_threshold_from_config(self):
        from src.etf_signal.signal import compute_trend_state
        # 阈值来自 config/indicator_spec.yaml（strong=80 / watch=60）
        assert compute_trend_state(80.0, 50, 1.0, 2.0, True, True) == "BUY_CANDIDATE"
        assert compute_trend_state(80.0, 50, 1.0, 2.0, False, False) == "STRONG_WATCH"
        assert compute_trend_state(79.0, 50, 1.0, 2.0, False, False) == "WATCH"
        assert compute_trend_state(50.0, 50, 1.0, 2.0, False, False) == "OUT_OF_SCOPE"

    def test_trades_carry_provenance(self):
        from src.backtest.portfolio.simulate import load_strategies, strategy_trades
        cfg = load_strategies()["ai_20"]
        signals = pd.DataFrame([
            {"trade_date": "20240102", "layer": "1", "entity_type": "etf",
             "entity_code": "512480", "theme": "ai_infrastructure",
             "trend_state": "BUY_CANDIDATE", "rps15": 90.0},
            {"trade_date": "20240102", "layer": "2", "theme": "ai_infrastructure",
             "confirmation_status": "观察"},
        ])
        trades = strategy_trades(signals, cfg)
        assert "strategy_id" in trades.columns
        assert "universe_hash" in trades.columns

    def test_fee_from_execution_spec(self):
        from src.common.spec.loaders import load_execution_spec
        assert load_execution_spec().fee_pct == 0.05
        assert load_execution_spec().slippage_pct == 0.05

    def test_max_positions_from_portfolio_spec(self):
        from src.common.spec.loaders import load_portfolio_spec
        assert load_portfolio_spec().max_positions == 5
        assert load_portfolio_spec().deploy_ratio == 1.0
