from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from src.common.paths import (
    project_root, config_dir, stock_pool_path,
    sw_industry_rps_config_path, data_dir, raw_dir, processed_dir,
    sw_industry_raw_dir, sw_industry_processed_dir, state_dir,
    asset_state_path, outputs_dir, stock_trend_output_dir,
    sw_industry_rps_output_dir, docs_dir, manifest_path,
)
from src.common.run_context import RunContext
from src.common.manifest import (
    write_run_manifest, read_subsystem_manifest,
    read_latest_run, find_latest_artifact,
)


class TestPaths:
    def test_project_root_resolves(self):
        root = project_root()
        assert (root / "src" / "common").is_dir()
        assert (root / "config" / "stock_pool.csv").is_file()

    def test_config_dir(self):
        assert config_dir() == project_root() / "config"

    def test_stock_pool_path(self):
        assert stock_pool_path().name == "stock_pool.csv"
        assert stock_pool_path().parent == config_dir()

    def test_sw_config_path(self):
        assert sw_industry_rps_config_path().name == "sw_industry_rps.yaml"

    def test_data_dir(self):
        assert data_dir() == project_root() / "data"

    def test_raw_dir(self):
        assert raw_dir() == data_dir() / "raw"

    def test_processed_dir(self):
        assert processed_dir() == data_dir() / "processed"

    def test_sw_subdirs(self):
        assert sw_industry_raw_dir() == raw_dir() / "sw_industry"
        assert sw_industry_processed_dir() == processed_dir() / "sw_industry"

    def test_state_dir(self):
        assert state_dir() == data_dir() / "state"

    def test_asset_state_path(self):
        assert asset_state_path() == state_dir() / "asset_state.csv"

    def test_outputs_dir(self):
        assert outputs_dir() == project_root() / "outputs"

    def test_stock_trend_output(self):
        assert stock_trend_output_dir() == outputs_dir() / "stock_trend"

    def test_sw_industry_rps_output(self):
        assert sw_industry_rps_output_dir() == outputs_dir() / "sw_industry_rps"

    def test_docs_dir(self):
        assert docs_dir() == project_root() / "docs"

    def test_manifest_path(self):
        assert manifest_path() == outputs_dir() / "manifest.json"


class TestRunContext:
    def test_default_fields(self):
        ctx = RunContext(subsystem="test", run_date=date(2026, 7, 16), status="ok")
        assert ctx.run_id != ""
        assert ctx.schema_version == 1
        assert ctx.subsystem == "test"
        assert ctx.run_date == date(2026, 7, 16)
        assert ctx.started_at != ""

    def test_to_dict_contains_all_keys(self):
        ctx = RunContext(
            subsystem="stock_trend",
            run_date=date(2026, 7, 16),
            status="completed",
            offline=True,
            summary={"count": 5},
            artifacts=["outputs/stock_trend/report.csv"],
        )
        d = ctx.to_dict()
        assert d["schema_version"] == 1
        assert d["subsystem"] == "stock_trend"
        assert d["run_date"] == "2026-07-16"
        assert d["status"] == "completed"
        assert d["offline"] is True
        assert d["summary"]["count"] == 5
        assert "run_id" in d
        assert "started_at" in d
        assert "finished_at" in d


class TestManifest:
    def test_write_and_read(self, tmp_path):
        ctx = RunContext(
            subsystem="stock_trend",
            run_date=date(2026, 7, 16),
            status="completed",
            summary={"assets": 10},
            artifacts=["outputs/report.csv"],
        )
        import src.common.manifest as m
        import src.common.paths as p
        original_root = p._ROOT
        p._ROOT = tmp_path
        try:
            path = write_run_manifest(ctx)
            assert path.exists()

            entry = read_subsystem_manifest("stock_trend")
            assert entry is not None
            assert entry["status"] == "completed"

            restored = read_latest_run("stock_trend")
            assert restored is not None
            assert restored.subsystem == "stock_trend"
            assert restored.run_date == date(2026, 7, 16)

            missing = read_subsystem_manifest("nonexistent")
            assert missing is None
        finally:
            p._ROOT = original_root

    def test_find_latest_artifact_empty(self):
        result = find_latest_artifact("nonexistent", "*.csv")
        assert result is None

    def test_find_latest_artifact(self, tmp_path):
        st_dir = tmp_path / "outputs" / "stock_trend"
        st_dir.mkdir(parents=True)
        (st_dir / "report_20260715.html").write_text("a")
        (st_dir / "report_20260716.html").write_text("b")

        import src.common.manifest as m
        import src.common.paths as p
        original_root = p._ROOT
        p._ROOT = tmp_path
        try:
            result = find_latest_artifact("stock_trend", "report_*.html")
            assert result is not None
            assert "20260716" in result.name
        finally:
            p._ROOT = original_root

    def test_failed_run_does_not_overwrite_success(self, tmp_path):
        import src.common.paths as p
        original_root = p._ROOT
        p._ROOT = tmp_path
        try:
            success = RunContext(
                subsystem="stock_trend", run_date=date(2026, 7, 16), status="completed",
                artifacts=["outputs/stock_trend/ok.csv"],
            )
            write_run_manifest(success)

            failure = RunContext(
                subsystem="stock_trend", run_date=date(2026, 7, 16), status="failed",
                artifacts=["outputs/stock_trend/fail.csv"],
            )
            write_run_manifest(failure)

            entry = read_subsystem_manifest("stock_trend")
            assert entry["status"] == "completed"
            assert "ok.csv" in entry["artifacts"][0]
        finally:
            p._ROOT = original_root

    def test_failed_overwrites_failed(self, tmp_path):
        import src.common.paths as p
        original_root = p._ROOT
        p._ROOT = tmp_path
        try:
            f1 = RunContext(
                subsystem="stock_trend", run_date=date(2026, 7, 16), status="failed",
            )
            write_run_manifest(f1)
            f2 = RunContext(
                subsystem="stock_trend", run_date=date(2026, 7, 16), status="failed",
            )
            write_run_manifest(f2)
            restored = read_latest_run("stock_trend", require_status="failed")
            assert restored is not None
        finally:
            p._ROOT = original_root

    def test_status_filter(self, tmp_path):
        import src.common.paths as p
        original_root = p._ROOT
        p._ROOT = tmp_path
        try:
            ctx = RunContext(
                subsystem="stock_trend", run_date=date(2026, 7, 16), status="failed",
            )
            write_run_manifest(ctx)

            good = read_latest_run("stock_trend", require_status="completed")
            assert good is None

            bad = read_latest_run("stock_trend", require_status={"failed", "partial"})
            assert bad is not None
        finally:
            p._ROOT = original_root

    def test_manifest_atomic_write(self, tmp_path):
        import src.common.paths as p
        original_root = p._ROOT
        p._ROOT = tmp_path
        try:
            ctx = RunContext(
                subsystem="stock_trend", run_date=date(2026, 7, 16), status="completed",
            )
            path = write_run_manifest(ctx)
            assert path.exists()
            assert not path.with_suffix(".tmp").exists()
        finally:
            p._ROOT = original_root

    def test_find_latest_artifact_only_considers_manifest_on_status_filter(self, tmp_path):
        import src.common.paths as p
        original_root = p._ROOT
        p._ROOT = tmp_path
        try:
            (tmp_path / "outputs" / "stock_trend").mkdir(parents=True)
            (tmp_path / "outputs" / "stock_trend" / "good.csv").write_text("")
            success = RunContext(
                subsystem="stock_trend", run_date=date(2026, 7, 16), status="completed",
                artifacts=["outputs/stock_trend/good.csv"],
            )
            write_run_manifest(success)

            (tmp_path / "outputs" / "stock_trend" / "latest.csv").write_text("")
            failure = RunContext(
                subsystem="stock_trend", run_date=date(2026, 7, 16), status="failed",
                artifacts=["outputs/stock_trend/latest.csv"],
            )
            write_run_manifest(failure)

            result = find_latest_artifact("stock_trend", "*.csv", require_status="completed")
            assert result is not None
            assert "good.csv" in result.name
        finally:
            p._ROOT = original_root

    def test_manifest_idempotent(self, tmp_path):
        ctx1 = RunContext(subsystem="stock_trend", run_date=date(2026, 7, 16), status="completed")
        ctx2 = RunContext(subsystem="sw_industry_rps", run_date=date(2026, 7, 16), status="partial")

        import src.common.manifest as m
        import src.common.paths as p
        original_root = p._ROOT
        p._ROOT = tmp_path
        try:
            write_run_manifest(ctx1)
            write_run_manifest(ctx2)
            data = json.loads((tmp_path / "outputs" / "manifest.json").read_text())
            assert "stock_trend" in data
            assert "sw_industry_rps" in data
        finally:
            p._ROOT = original_root
