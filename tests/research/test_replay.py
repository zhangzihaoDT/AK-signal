"""Replay（v0.5）单元测试：schema、parity 逻辑、单日期重放集成。"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from src.research.replay import engine
from src.research.replay import range as replay_range
from src.research.signals import schema
from src.research.validation import parity


class TestSchema:
    def test_new_row_fills_defaults(self):
        row = schema.new_row(trade_date="20260803", entity_type="etf", entity_code="512480")
        assert row["layer"] == ""
        assert row["trend_state"] == ""
        assert row["trade_date"] == "20260803"

    def test_config_hash_stable(self):
        a = schema.config_hash()
        b = schema.config_hash()
        assert a == b
        assert len(a) == 16

    def test_rule_version(self):
        assert schema.RULE_VERSION == "v0.12.0"


class TestParityFloat:
    def test_within_tolerance(self):
        assert parity._compare_float(99.9, 99.9217, atol=0.02) is False
        assert parity._compare_float(99.92, 99.9217, atol=0.02) is True

    def test_nan_handling(self):
        assert parity._compare_float(None, None) is True
        assert parity._compare_float(None, 1.0) is False
        assert parity._compare_float(float("nan"), float("nan")) is True


class TestSelectionEntityMap:
    def test_extracts_stocks_and_etfs(self):
        selection = {
            "layer3": {
                "action": {"level": "BUY"},
                "buckets": [{
                    "themes": [{
                        "stock_watchlist": {
                            "leaders": [{"code": "600900", "selection_status": "RECOMMENDED"}],
                            "high_beta": [], "equipment": [],
                        },
                        "core_etf": [{"code": "512480", "state": "RECOMMENDED"}],
                        "sub_industry_etf": [],
                    }],
                }],
            },
        }
        m = parity._selection_entity_map(selection)
        assert m["stock:600900"]["selection_status"] == "RECOMMENDED"
        assert m["stock:600900"]["recommended_action"] == "BUY"
        assert m["etf:512480"]["selection_status"] == "RECOMMENDED"

    def test_extracts_recommendation_structure(self):
        """v0.5.0 推荐结构：recommendation.etf/stocks + watchlist.etf/stocks + monitoring。"""
        selection = {
            "layer3": {
                "action": {"level": "BUY"},
                "buckets": [{
                    "themes": [{
                        "recommendation": {
                            "etf": [{"code": "561560", "state": "RECOMMENDED"}],
                            "stocks": [{"code": "600941", "selection_status": "RECOMMENDED"}],
                        },
                        "watchlist": {
                            "etf": [{"code": "159625", "state": "WATCH"}],
                            "stocks": [{"code": "600900", "selection_status": "WATCH"}],
                        },
                        "monitoring": {
                            "leaders": [{"code": "600018", "selection_status": "RECOMMENDED"}],
                            "high_beta": [], "equipment": [],
                        },
                    }],
                }],
            },
        }
        m = parity._selection_entity_map(selection)
        assert m["etf:561560"]["selection_status"] == "RECOMMENDED"
        assert m["etf:159625"]["selection_status"] == "WATCH"
        assert m["stock:600941"]["selection_status"] == "RECOMMENDED"
        assert m["stock:600900"]["selection_status"] == "WATCH"
        assert m["stock:600018"]["selection_status"] == "RECOMMENDED"


class TestCheckParity:
    def _replayed(self):
        return pd.DataFrame([
            {"layer": "1", "entity_type": "etf", "entity_code": "512480",
             "rps15": 90.0, "trend_state": "BUY_CANDIDATE", "selection_status": "", "recommended_action": "",
             "rule_version": "v0.6.1", "config_hash": "abc"},
            {"layer": "2", "entity_type": "industry", "entity_code": "801161.SI", "theme": "high_cashflow",
             "rps15": 61.0, "confirmation_status": "中性", "selection_status": "", "recommended_action": "",
             "rule_version": "v0.6.1", "config_hash": "abc"},
            {"layer": "3", "entity_type": "stock", "entity_code": "600900",
             "selection_status": "RECOMMENDED", "recommended_action": "BUY",
             "rule_version": "v0.6.1", "config_hash": "abc"},
        ])

    def _formal(self):
        return {
            "rotation": pd.DataFrame([{"fund_code": "512480", "rps15": 90.005}]),
            "account_candidates": pd.DataFrame([{"fund_code": "512480", "trend_state": "BUY_CANDIDATE"}]),
            "confirmation": pd.DataFrame([{"industry_code": "801161.SI", "theme": "high_cashflow",
                                           "RPS15": 61.01, "strength_level": "中性"}]),
            "selection": {
                "layer3": {
                    "action": {"level": "BUY"},
                    "buckets": [{"themes": [{
                        "stock_watchlist": {
                            "leaders": [{"code": "600900", "selection_status": "RECOMMENDED"}],
                            "high_beta": [], "equipment": [],
                        },
                        "core_etf": [], "sub_industry_etf": [],
                    }]}],
                },
            },
        }

    def test_all_layers_pass(self, monkeypatch):
        monkeypatch.setattr(parity, "_load_formal_products", lambda d: self._formal())
        r = parity.check_parity("20260803", self._replayed())
        assert r["ok"] is True
        assert r["layers"]["layer1"]["matched"] == 1
        assert r["layers"]["layer2"]["matched"] == 1
        assert r["layers"]["layer3"]["matched"] == 1

    def test_missing_formal_layer_not_checked(self, monkeypatch):
        formal = self._formal()
        formal.pop("selection")
        monkeypatch.setattr(parity, "_load_formal_products", lambda d: formal)
        r = parity.check_parity("20260803", self._replayed())
        assert r["ok"] is True
        assert r["layers"]["layer3"]["checked"] is False

    def test_state_mismatch_fails(self, monkeypatch):
        formal = self._formal()
        formal["account_candidates"] = pd.DataFrame(
            [{"fund_code": "512480", "trend_state": "STRONG_WATCH"}])
        monkeypatch.setattr(parity, "_load_formal_products", lambda d: formal)
        r = parity.check_parity("20260803", self._replayed())
        assert r["ok"] is False
        assert r["layers"]["layer1"]["mismatched"] == 1


@pytest.mark.skipif(
    not (Path("data/etf_signal/daily/rotation_20260803.parquet").exists()
         and Path("data/processed/sw_industry/confirmation_20260803.parquet").exists()),
    reason="formal products for 20260803 not present",
)
class TestSingleDateReplayIntegration:
    def test_replay_parity_20260803(self, tmp_path):
        """已有正式产物日期：纯离线重放，parity 必须 PASS。"""
        df = engine.replay_single_date("20260803", out_dir=None)
        assert not df.empty
        assert df["signal_origin"].iloc[0] == "replayed"
        assert df["rule_version"].iloc[0] == "v0.12.0"
        r = parity.check_parity("20260803", df)
        assert r["ok"] is True, r
        assert r["layers"]["layer1"]["matched"] > 1000
        assert r["layers"]["layer2"]["matched"] >= 10


class TestReplayCalendar:
    def _cache(self):
        return {"combined": pd.DataFrame({
            "date": pd.to_datetime(["2026-07-31", "2026-08-03", "2026-08-04", "2026-08-05"]),
            "fund_code": ["512480"] * 4,
            "close": [1.0] * 4,
        })}

    def test_filters_range(self):
        cal = engine.replay_calendar(self._cache(), "20260801", "20260805")
        assert cal == ["20260803", "20260804", "20260805"]

    def test_empty_cache(self):
        assert engine.replay_calendar({"combined": pd.DataFrame()}, "20260801", "20260805") == []


class TestCoverageTable:
    def test_counts_and_rate(self):
        replayed = pd.DataFrame([
            {"trade_date": "20260803", "layer": "1", "entity_code": "512480"},
            {"trade_date": "20260803", "layer": "1", "entity_code": "159819"},
            {"trade_date": "20260803", "layer": "2", "entity_code": "801161.SI"},
        ])
        cache = {"combined": pd.DataFrame({"fund_code": ["512480", "159819", "561560"]})}
        cov = replay_range._coverage_table(cache, replayed)
        assert len(cov) == 1
        assert cov[0]["priced_etf_count"] == 2
        assert cov[0]["eligible_etf_count"] == 3
        assert abs(cov[0]["coverage_rate"] - round(2 / 3, 4)) < 1e-6


@pytest.mark.skipif(
    not (Path("data/etf_signal/daily/rotation_20260803.parquet").exists()
         and Path("data/processed/sw_industry/confirmation_20260803.parquet").exists()),
    reason="formal products for 20260803 not present",
)
class TestRangeReplayIntegration:
    def test_range_consistency_with_single_date(self):
        """区间重放在已有正式日期与单日期重放完全一致（同代码路径保证）。"""
        cache = engine.build_replay_cache()
        rng = replay_range.replay_range("20260731", "20260803", layers="123", out_dir=None, cache=cache)
        single = engine.replay_single_date("20260803", out_dir=None, cache=cache)

        r = rng[rng["trade_date"] == "20260803"].reset_index(drop=True)
        s = single.reset_index(drop=True)
        # 行数随 universe 规模变化（如 2026-08 monitor-only tier 纳入 +5），
        # 只断言区间与单日期一致 + 规模下限，不硬编码具体行数
        assert len(r) == len(s)
        assert len(r) > 1000

        def _key(df):
            return df.set_index(["layer", "entity_type", "entity_code"])

        k1, k2 = _key(r), _key(s)
        assert set(k1.index) == set(k2.index)
        for c in ("rps15", "trend_score", "trend_state", "confirmation_status",
                  "selection_status", "recommended_action"):
            a = k1[c].astype(str).fillna("<NA>")
            b = k2[c].astype(str).fillna("<NA>")
            assert (a != b).sum() == 0, f"field {c} differs"

    def test_resume_and_dedup_and_determinism(self, tmp_path):
        """resume 跳过已完成日期；汇总无重复主键；重复执行结果一致。"""
        cache = engine.build_replay_cache()
        out_dir = tmp_path / "research"
        df1 = replay_range.replay_range("20260731", "20260803", layers="123",
                                        out_dir=out_dir, cache=cache, resume=True)
        # 无重复主键
        assert df1.duplicated(subset=replay_range.PRIMARY_KEY).sum() == 0
        # 第二次执行：全部 skipped，结果一致（assert_frame_equal 将 NaN 视为相等）
        df2 = replay_range.replay_range("20260731", "20260803", layers="123",
                                        out_dir=out_dir, cache=cache, resume=True)
        pd.testing.assert_frame_equal(df1.reset_index(drop=True), df2.reset_index(drop=True))
        man = json.loads((out_dir / "replay_range_20260731_20260803_manifest.json").read_text(encoding="utf-8"))
        assert set(man["date_status"].values()) == {"skipped"}
        assert man["status_counts"]["skipped"] == 2
        # 强制重放：全部 completed，结果仍一致
        df3 = replay_range.replay_range("20260731", "20260803", layers="123",
                                        out_dir=out_dir, cache=cache, resume=False)
        man3 = json.loads((out_dir / "replay_range_20260731_20260803_manifest.json").read_text(encoding="utf-8"))
        assert set(man3["date_status"].values()) == {"completed"}
        pd.testing.assert_frame_equal(df3.reset_index(drop=True), df1.reset_index(drop=True))


def _fake_signal_row(trade_date: str, code: str = "512480") -> dict:
    row = {c: "" for c in schema.SIGNAL_COLUMNS}
    row.update({
        "trade_date": trade_date, "signal_origin": "replayed", "entity_type": "etf",
        "entity_code": code, "layer": "1", "rps15": 90.0, "trend_state": "BUY_CANDIDATE",
        "source_trade_date": trade_date, "data_status": "current",
        "rule_version": "v0.6.1", "config_hash": "x",
    })
    return row


class TestRangeFaultTolerance:
    def _cache(self):
        return {
            "combined": pd.DataFrame({
                "date": pd.to_datetime(["2026-08-03", "2026-08-04"]),
                "fund_code": ["512480", "512480"], "close": [1.0, 1.0],
            }),
            "master": pd.DataFrame(), "metrics_df": pd.DataFrame(),
        }

    def test_failed_date_does_not_abort(self, monkeypatch, tmp_path):
        def fake_single(d, **kw):
            if d == "20260803":
                raise RuntimeError("boom")
            return pd.DataFrame([_fake_signal_row("20260804")])

        monkeypatch.setattr(replay_range.replay_engine, "replay_single_date", fake_single)
        out_dir = tmp_path / "research"
        df = replay_range.replay_range("20260803", "20260804", layers="1",
                                       out_dir=out_dir, cache=self._cache(), resume=False)
        man = json.loads((out_dir / "replay_range_20260803_20260804_manifest.json").read_text(encoding="utf-8"))
        assert man["status_counts"]["failed"] == 1
        assert man["status_counts"]["completed"] == 1
        assert set(man["date_status"].values()) == {"failed", "completed"}
        assert len(df) == 1

    def test_degraded_detection(self, monkeypatch, tmp_path):
        def fake_single(d, **kw):
            if d == "20260803":
                return pd.DataFrame()  # 无层产出 → degraded
            return pd.DataFrame([_fake_signal_row("20260804")])

        monkeypatch.setattr(replay_range.replay_engine, "replay_single_date", fake_single)
        out_dir = tmp_path / "research"
        df = replay_range.replay_range("20260803", "20260804", layers="1",
                                       out_dir=out_dir, cache=self._cache(), resume=False)
        man = json.loads((out_dir / "replay_range_20260803_20260804_manifest.json").read_text(encoding="utf-8"))
        assert man["status_counts"]["degraded"] == 1
        assert man["status_counts"]["completed"] == 1
        assert len(df) == 1
