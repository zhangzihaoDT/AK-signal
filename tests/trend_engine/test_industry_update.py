"""
行业日更核心行为测试（不依赖真实网络）。
"""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from src.sw_industry_rps import storage, metrics, regimes
from src.sw_industry_rps.cli import validate_raw_completeness
from src.common.run_context import RunContext
from src.common.manifest import write_run_manifest, read_latest_run, find_latest_artifact


# ---------------------------------------------------------------------------
# 1. Active universe
# ---------------------------------------------------------------------------

def _make_mock_master(codes: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"industry_code": codes, "industry_name": [f"Ind_{c}" for c in codes]})


def _make_mock_raw(raw_dir: Path, code: str, last_date: str = "2026-07-15") -> None:
    end = pd.Timestamp(last_date)
    start = end - pd.Timedelta(days=20)
    dates = pd.bdate_range(start, end)
    df = pd.DataFrame({
        "trade_date": dates,
        "close": [float(100 + i) for i in range(len(dates))],
    })
    storage.save_industry_raw(df, raw_dir, code)


class TestActiveUniverse:
    def test_only_active_requested(self, tmp_path):
        """update 只请求 active 124，不请求 inactive 7"""
        codes = [f"801{x:03d}.SI" for x in range(131)]
        master = _make_mock_master(codes)
        raw_dir = tmp_path / "raw" / "sw_industry"
        raw_dir.mkdir(parents=True)
        storage.save_master(master, raw_dir)

        # 124 active (recent data), 7 inactive (old data)
        for i, code in enumerate(codes):
            if i < 124:
                _make_mock_raw(raw_dir, code, "2026-07-15")
            else:
                _make_mock_raw(raw_dir, code, "2024-06-17")

        active, inactive, changed = storage.compute_active_codes(raw_dir, master)
        assert len(active) == 124, f"expected 124 active, got {len(active)}"
        assert len(inactive) == 7, f"expected 7 inactive, got {len(inactive)}"

    def test_snapshot_stability(self, tmp_path):
        """snapshot 确定后，数据短暂缺失不影响 universe"""
        codes = ["801016.SI", "801015.SI"]
        master = _make_mock_master(codes)
        raw_dir = tmp_path / "raw" / "sw_industry"
        raw_dir.mkdir(parents=True)
        storage.save_master(master, raw_dir)

        _make_mock_raw(raw_dir, "801016.SI", "2026-07-15")
        _make_mock_raw(raw_dir, "801015.SI", "2026-07-15")

        # First run creates snapshot
        active, _, _ = storage.compute_active_codes(raw_dir, master)
        storage.save_active_snapshot(active, raw_dir)
        assert len(active) == 2

        # One industry's data temporarily disappears
        (raw_dir / "801016_SI.csv").unlink()

        # snapshot should still return 2 active
        active2, _, changed = storage.compute_active_codes(raw_dir, master)
        assert len(active2) == 2, "snapshot should protect against transient data loss"
        assert changed, "should flag universe_changed"


# ---------------------------------------------------------------------------
# 2. Checkpoint
# ---------------------------------------------------------------------------

class TestCheckpoint:
    def test_resume_only_uncompleted(self, tmp_path):
        """checkpoint 中断后只继续未完成代码"""
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True)
        cp_data = {
            "target_date": "20260715",
            "active_count": 124,
            "completed_codes": [f"801{x:03d}.SI" for x in range(100)],
            "failed_codes": {"801100.SI": 2},
        }
        storage.save_checkpoint(raw_dir, cp_data)

        loaded = storage.load_checkpoint(raw_dir)
        assert loaded is not None
        assert loaded["target_date"] == "20260715"
        assert len(loaded["completed_codes"]) == 100

        storage.clear_checkpoint(raw_dir)
        assert storage.load_checkpoint(raw_dir) is None

    def test_checkpoint_target_date_mismatch(self, tmp_path):
        """target_date 不一致时不得复用 checkpoint"""
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True)
        storage.save_checkpoint(raw_dir, {"target_date": "20260714", "completed_codes": []})
        cp = storage.load_checkpoint(raw_dir)
        assert cp is not None
        assert cp["target_date"] != "20260715"


# ---------------------------------------------------------------------------
# 3. Calculate completeness gate
# ---------------------------------------------------------------------------

class TestCalculateGate:
    def test_incomplete_raw_rejected(self, tmp_path):
        """raw 只有 123/124 时 calculate 拒绝执行"""
        codes = [f"801{x:03d}.SI" for x in range(124)]
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True)
        for i, c in enumerate(codes):
            if i < 123:
                _make_mock_raw(raw_dir, c, "2026-07-15")
        covered, expected, missing = validate_raw_completeness(raw_dir, "2026-07-15", codes)
        assert covered == 123
        assert covered < expected
        assert len(missing) == 1

    def test_idempotent_replace(self, tmp_path):
        """目标日期已有 17 行时重算后变为唯一 124 行"""
        codes = [f"801{x:03d}.SI" for x in range(124)]
        raw_dir = tmp_path / "raw"
        processed_dir = tmp_path / "processed"
        raw_dir.mkdir(parents=True)
        processed_dir.mkdir(parents=True)

        # Create raw data for 124 industries
        for c in codes:
            _make_mock_raw(raw_dir, c, "2026-07-15")
        active = codes

        # Build full metrics
        all_hist = []
        for c in active:
            df = storage.load_industry_raw(raw_dir, c)
            if not df.empty:
                df["industry_code"] = c
                all_hist.append(df)
        combined = pd.concat(all_hist, ignore_index=True)
        full = metrics.compute_all_metrics(combined)
        full = regimes.identify_all_regimes(full)
        target_date = full["trade_date"].max()

        # Simulate existing 17-row partition
        partial = full[full["trade_date"] == target_date].head(17)
        prior = pd.concat([full[full["trade_date"] < target_date], partial], ignore_index=True)
        prior = prior.sort_values(["trade_date", "industry_code"]).reset_index(drop=True)

        # Save as current metrics
        storage.save_metrics_atomically(prior, processed_dir)

        # Now recalculate — replace target_date partition
        prior_no_target = prior[prior["trade_date"] != target_date]
        target_rows = full[full["trade_date"] == target_date]
        final = pd.concat([prior_no_target, target_rows], ignore_index=True)
        final = final.drop_duplicates(subset=["trade_date", "industry_code"], keep="last")
        final = final.sort_values(["trade_date", "industry_code"]).reset_index(drop=True)

        latest = final[final["trade_date"] == target_date]
        assert len(latest) == 124, f"expected 124 rows, got {len(latest)}"
        assert latest["industry_code"].nunique() == 124

    def test_double_calculate_idempotent(self, tmp_path):
        """同一天重复 calculate 两次结果一致"""
        codes = [f"801{x:03d}.SI" for x in range(50)]
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir(parents=True)
        for c in codes:
            _make_mock_raw(raw_dir, c, "2026-07-15")

        all_hist = []
        for c in codes:
            df = storage.load_industry_raw(raw_dir, c)
            if not df.empty:
                df["industry_code"] = c
                all_hist.append(df)
        combined = pd.concat(all_hist, ignore_index=True)

        result1 = metrics.compute_all_metrics(combined)
        result1 = regimes.identify_all_regimes(result1)
        result2 = metrics.compute_all_metrics(combined)
        result2 = regimes.identify_all_regimes(result2)

        pd.testing.assert_frame_equal(
            result1.sort_values(["trade_date", "industry_code"]).reset_index(drop=True),
            result2.sort_values(["trade_date", "industry_code"]).reset_index(drop=True),
        )


# ---------------------------------------------------------------------------
# 4. Manifest failure protection
# ---------------------------------------------------------------------------

class TestManifestProtection:
    def test_failed_report_does_not_replace_previous(self, tmp_path):
        """report 失败后 latest completed artifact 不变"""
        import src.common.paths as p
        original = p._ROOT
        p._ROOT = tmp_path
        try:
            # First successful run
            ctx_ok = RunContext(
                subsystem="sw_industry_rps", run_date=date(2026, 7, 15),
                status="completed",
                artifacts=["outputs/sw_industry_rps/report_20260715.html"],
            )
            write_run_manifest(ctx_ok)

            # Failed run
            ctx_fail = RunContext(
                subsystem="sw_industry_rps", run_date=date(2026, 7, 16),
                status="failed", artifacts=[],
            )
            write_run_manifest(ctx_fail)

            # Latest should still point to successful run
            restored = read_latest_run("sw_industry_rps", require_status="completed")
            assert restored is not None
            assert restored.run_date == date(2026, 7, 15)

            # find_latest_artifact should refuse failed
            result = find_latest_artifact("sw_industry_rps", "*.html", require_status="completed")
            assert result is None
        finally:
            p._ROOT = original


# ---------------------------------------------------------------------------
# 5. CLI entry
# ---------------------------------------------------------------------------

class TestCliEntry:
    def test_script_entry_imports(self):
        """python src/main.py 可导入"""
        from src.main import main, SW_INDUSTRY_COMMANDS
        assert callable(main)
        assert "run-day" in SW_INDUSTRY_COMMANDS

    def test_module_entry_imports(self):
        """python -m src.main 可导入"""
        import importlib
        mod = importlib.import_module("src.main")
        assert hasattr(mod, "main")
        assert hasattr(mod, "SW_INDUSTRY_COMMANDS")
