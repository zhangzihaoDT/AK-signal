import json
from pathlib import Path

import pandas as pd

from src import daily_run
from src.common import batch_release as release


def test_industry_failure_publishes_other_modules_as_partial(tmp_path, monkeypatch):
    monkeypatch.setattr("src.market_facts.policy.source_policy", lambda: {})
    commands = []
    monkeypatch.setattr(daily_run, "project_root", lambda: tmp_path)
    monkeypatch.setattr(daily_run, "step_artifacts", lambda *_: [])
    monkeypatch.setattr(daily_run, "check_generated_reports", lambda *_: None)

    def check_step(command, _date):
        if command[:2] == ["industry", "update"]:
            raise ValueError("行业目标日 20261015 带日期价格尚未就绪（已核验 0/124）")

    monkeypatch.setattr(daily_run, "check_step", check_step)

    def run_step(command, _env, log, _title):
        commands.append(command[:2])
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("stage ran", encoding="utf-8")
        return 0

    monkeypatch.setattr(daily_run, "run_step", run_step)

    def validate_release(trade_date):
        stage = daily_run.outputs_dir()
        observation = stage / "observation"
        run_day = stage / "run_day"
        observation.mkdir(parents=True, exist_ok=True)
        run_day.mkdir(parents=True, exist_ok=True)
        for suffix in (".html", ".md", ".json"):
            body = json.dumps({"trade_date": trade_date}) if suffix == ".json" else "partial facts"
            (observation / f"observation_{trade_date}{suffix}").write_text(body)
        receipt = {"ok": True, "trade_date": trade_date, "scope": "observation_only",
                   "status": "PARTIAL", "warnings": ["industry_rotation unavailable"],
                   "errors": [], "data_complete": False, "industry_price_dates_verified": False}
        (run_day / f"final_check_{trade_date}.json").write_text(json.dumps(receipt))
        return receipt

    monkeypatch.setattr(daily_run, "validate_release", validate_release)

    assert daily_run.execute_batch(tmp_path, "20261015", offline=False) == 0
    assert ["industry", "calculate"] not in commands
    assert ["etf", "calculate"] in commands
    assert ["observation", "stock-metrics"] in commands
    assert ["observation", "build"] in commands
    receipt = release.published_batch(tmp_path, "20261015")
    assert receipt is not None
    assert receipt["result"]["status"] == "PARTIAL"
    state = release.read_checkpoint(tmp_path, "20261015", False)
    assert state is not None
    assert state["status"] == "published"
    assert state["degraded_modules"]["industry_rotation"]["status"] == "unavailable"
    assert state["steps"][1]["status"] == "degraded"
    assert state["steps"][3]["status"] == "skipped_degraded"
