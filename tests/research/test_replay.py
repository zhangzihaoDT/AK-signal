"""Replay（v0.5）单元测试：schema、parity 逻辑、单日期重放集成。"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from src.research.replay import engine
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
        assert schema.RULE_VERSION == "v0.4.3"


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


class TestCheckParity:
    def _replayed(self):
        return pd.DataFrame([
            {"layer": "1", "entity_type": "etf", "entity_code": "512480",
             "rps15": 90.0, "trend_state": "BUY_CANDIDATE", "selection_status": "", "recommended_action": "",
             "rule_version": "v0.4.3", "config_hash": "abc"},
            {"layer": "2", "entity_type": "industry", "entity_code": "801161.SI",
             "rps15": 61.0, "confirmation_status": "中性", "selection_status": "", "recommended_action": "",
             "rule_version": "v0.4.3", "config_hash": "abc"},
            {"layer": "3", "entity_type": "stock", "entity_code": "600900",
             "selection_status": "RECOMMENDED", "recommended_action": "BUY",
             "rule_version": "v0.4.3", "config_hash": "abc"},
        ])

    def _formal(self):
        return {
            "rotation": pd.DataFrame([{"fund_code": "512480", "rps15": 90.005}]),
            "account_candidates": pd.DataFrame([{"fund_code": "512480", "trend_state": "BUY_CANDIDATE"}]),
            "confirmation": pd.DataFrame([{"industry_code": "801161.SI", "RPS15": 61.01, "strength_level": "中性"}]),
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
        assert df["rule_version"].iloc[0] == "v0.4.3"
        r = parity.check_parity("20260803", df)
        assert r["ok"] is True, r
        assert r["layers"]["layer1"]["matched"] > 1000
        assert r["layers"]["layer2"]["matched"] >= 10
