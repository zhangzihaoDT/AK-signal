"""Final Validation 单元测试：纯判定逻辑 evaluate + run 警告收集器。"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from src.common import warnings as run_warnings
from src.final_validation.cli import evaluate, _three_lane_lane1_lag_days


@pytest.fixture(autouse=True)
def _clean_warnings_buffer():
    run_warnings.drain()
    yield
    run_warnings.drain()


class TestEvaluateStatus:
    def _layers(self, etf="confirmed", sw="confirmed"):
        return {
            "etf": {"trade_date": "20260803", "data_status": etf},
            "sw_industry": {"trade_date": "20260803", "data_status": sw},
        }

    def test_all_confirmed(self):
        r = evaluate(trade_date="20260803", layers=self._layers(),
                     alignment={"alignment_status": "aligned"},
                     action_level="WAIT", warnings=[])
        assert r["ok"] is True
        assert r["status"] == "CONFIRMED"
        assert r["action"] == "WAIT"
        assert r["errors"] == []

    def test_provisional_sw(self):
        r = evaluate(trade_date="20260803", layers=self._layers(sw="provisional"),
                     alignment={}, action_level="BUY", warnings=[])
        assert r["status"] == "PROVISIONAL"
        assert r["ok"] is True

    def test_missing_layers_unknown(self):
        r = evaluate(trade_date="20260803", layers=None, alignment={},
                     action_level="WAIT", warnings=[])
        assert r["status"] == "UNKNOWN"
        assert any("data_status" in w for w in r["warnings"])

    def test_inconsistent_status(self):
        r = evaluate(trade_date="20260803",
                     layers=self._layers(etf="confirmed", sw="unknown"),
                     alignment={}, action_level="BUY", warnings=[])
        assert r["status"] == "UNKNOWN"


class TestEvaluateWarnings:
    def test_alignment_warning(self):
        r = evaluate(trade_date="20260803",
                     layers={"etf": {"data_status": "confirmed"},
                             "sw_industry": {"data_status": "confirmed"}},
                     alignment={"alignment_status": "stale_industry", "industry_lag_days": 1},
                     action_level="BUY", warnings=[])
        assert any("stale_industry" in w for w in r["warnings"])

    def test_degraded_warning(self):
        r = evaluate(trade_date="20260803",
                     layers={"degraded": "config_issues",
                             "etf": {"data_status": "confirmed"},
                             "sw_industry": {"data_status": "confirmed"}},
                     alignment={"alignment_status": "aligned"},
                     action_level="BUY", warnings=[])
        assert any("配置降级" in w for w in r["warnings"])

    def test_run_warnings_passthrough(self):
        r = evaluate(trade_date="20260803",
                     layers={"etf": {"data_status": "confirmed"},
                             "sw_industry": {"data_status": "confirmed"}},
                     alignment={"alignment_status": "aligned"},
                     action_level="WAIT",
                     warnings=[{"category": "drilldown", "message": "2 stock drilldowns failed"}])
        assert "2 stock drilldowns failed" in r["warnings"]

    def test_coverage_degraded(self):
        r = evaluate(trade_date="20260803",
                     layers={"etf": {"data_status": "confirmed"},
                             "sw_industry": {"data_status": "confirmed"}},
                     alignment={"alignment_status": "aligned"},
                     action_level="BUY",
                     warnings=[],
                     coverage={"selection_coverage": "14/33",
                               "selection_coverage_pct": 42.4,
                               "degraded_assets": ["600900", "601728", "600050"]})
        assert r["ok"] is True
        assert any("selection 输入降级" in w for w in r["warnings"])
        assert any("覆盖率" in w for w in r["warnings"])


class TestEvaluateErrors:
    def test_missing_selection(self):
        r = evaluate(trade_date="20260803", layers=None, alignment=None,
                     action_level=None, warnings=[], selection_exists=False)
        assert r["ok"] is False
        assert any("selection candidates 缺失" in e for e in r["errors"])

    def test_missing_rotation(self):
        r = evaluate(trade_date="20260803", layers=None, alignment=None,
                     action_level=None, warnings=[], rotation_exists=False)
        assert r["ok"] is False
        assert any("ETF rotation 缺失" in e for e in r["errors"])

    def test_missing_confirmation(self):
        r = evaluate(trade_date="20260803", layers=None, alignment=None,
                     action_level=None, warnings=[], confirmation_exists=False)
        assert r["ok"] is False
        assert any("SW confirmation 缺失" in e for e in r["errors"])

    def test_missing_transition_state_mandatory(self):
        """Lane 3 transition_state 是正式每日能力：缺失 = run-day 不完整。"""
        r = evaluate(trade_date="20260803", layers=None, alignment=None,
                     action_level=None, warnings=[], transition_state_exists=False)
        assert r["ok"] is False
        assert any("Lane 3 transition_state 缺失" in e for e in r["errors"])

    def test_transition_state_present_no_error(self):
        r = evaluate(trade_date="20260803", layers=None, alignment=None,
                     action_level=None, warnings=[], transition_state_exists=True)
        assert not any("Lane 3" in e for e in r["errors"])

    def test_errors_dominate_warnings(self):
        r = evaluate(trade_date="20260803", layers=None, alignment=None,
                     action_level=None, warnings=[], selection_exists=False,
                     rotation_exists=False, confirmation_exists=False)
        assert r["ok"] is False
        assert len(r["errors"]) == 3

    def test_lane1_aligned_no_warning(self):
        """_watchlist_date == trade_date：无滞后，不标 warning，不阻塞。"""
        r = evaluate(trade_date="20260803", layers=None, alignment=None,
                     action_level=None, warnings=[], lane1_lag_days=0)
        assert r["ok"] is True
        assert not any("Lane 1 watchlist 滞后" in w for w in r["warnings"])

    def test_lane1_lagged_must_flag(self):
        """允许上一交易日 fallback 时，必须显式标记 Lane 1 lagged，防止把时间差当状态先后。"""
        r = evaluate(trade_date="20260803", layers=None, alignment=None,
                     action_level=None, warnings=[], lane1_lag_days=1)
        assert r["ok"] is True
        assert any("Lane 1 watchlist 滞后 1 交易日" in w for w in r["warnings"])
        assert any("FIRST_EXIT × Lane 1" in w for w in r["warnings"])

    def test_lane1_lag_none_no_warning(self):
        """产物/列缺失时 lane1_lag_days=None：不误报滞后。"""
        r = evaluate(trade_date="20260803", layers=None, alignment=None,
                     action_level=None, warnings=[], lane1_lag_days=None)
        assert not any("Lane 1 watchlist 滞后" in w for w in r["warnings"])


class TestThreeLaneLane1LagDays:
    def _write_three_lane(self, tmp_path, watchlist_date):
        out = tmp_path / "etf_signal"
        out.mkdir(parents=True, exist_ok=True)
        p = out / "three_lane_20260803.parquet"
        if watchlist_date is None:
            df = pd.DataFrame({"fund_code": ["A"]})
        else:
            df = pd.DataFrame({"fund_code": ["A"], "_watchlist_date": [watchlist_date]})
        df.to_parquet(p, index=False)
        return p

    def test_aligned(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.final_validation.cli.outputs_dir", lambda: tmp_path)
        self._write_three_lane(tmp_path, pd.Timestamp("2026-08-03"))
        assert _three_lane_lane1_lag_days("20260803") == 0

    def test_lagged(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.final_validation.cli.outputs_dir", lambda: tmp_path)
        self._write_three_lane(tmp_path, pd.Timestamp("2026-08-02"))
        assert _three_lane_lane1_lag_days("20260803") == 1

    def test_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.final_validation.cli.outputs_dir", lambda: tmp_path)
        assert _three_lane_lane1_lag_days("20260803") is None

    def test_missing_column(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.final_validation.cli.outputs_dir", lambda: tmp_path)
        self._write_three_lane(tmp_path, None)
        assert _three_lane_lane1_lag_days("20260803") is None


class TestWarningsCollector:
    def test_save_load_and_dedupe(self, tmp_path, monkeypatch):
        monkeypatch.setattr(run_warnings, "outputs_dir", lambda: tmp_path)
        run_warnings.record("drilldown", "2 stock drilldowns failed")
        run_warnings.record("drilldown", "3 stock drilldowns failed")
        run_warnings.record("drilldown", "2 stock drilldowns failed")
        added = run_warnings.save_warnings("20260803")
        assert added == 2
        data = run_warnings.load_warnings("20260803")
        msgs = [w["message"] for w in data]
        assert msgs.count("2 stock drilldowns failed") == 1
        assert msgs.count("3 stock drilldowns failed") == 1

    def test_save_merges_across_processes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(run_warnings, "outputs_dir", lambda: tmp_path)
        run_warnings.record("drilldown", "2 stock drilldowns failed")
        run_warnings.save_warnings("20260803")
        # 模拟下一次进程：缓冲区清空，但文件保留
        run_warnings.record("other", "another warning")
        run_warnings.save_warnings("20260803")
        data = run_warnings.load_warnings("20260803")
        assert len(data) == 2

    def test_category_filter(self, tmp_path, monkeypatch):
        monkeypatch.setattr(run_warnings, "outputs_dir", lambda: tmp_path)
        run_warnings.record("drilldown", "2 stock drilldowns failed")
        run_warnings.record("other", "skip me")
        run_warnings.save_warnings("20260803", categories={"drilldown"})
        data = run_warnings.load_warnings("20260803")
        assert [w["category"] for w in data] == ["drilldown"]

    def test_empty_buffer_noop(self, tmp_path, monkeypatch):
        monkeypatch.setattr(run_warnings, "outputs_dir", lambda: tmp_path)
        assert run_warnings.save_warnings("20260803") == 0
        assert not (tmp_path / "run_warnings_20260803.json").exists()
