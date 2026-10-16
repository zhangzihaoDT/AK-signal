import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_hesai_active_pool_uses_correct_hk_symbol_and_keeps_old_code_resolved():
    universe = yaml.safe_load((ROOT / "config/selection_universe.yaml").read_text(encoding="utf-8"))
    assets = []
    for theme in universe["themes"].values():
        for tier in theme.get("tiers", []):
            assets.extend(tier.get("assets", []))

    hesai = [row for row in assets if row.get("name") == "禾赛"]
    assert len(hesai) == 1
    assert hesai[0]["market"] == "HK"
    assert hesai[0]["symbol"] == "02525"
    assert not any(row.get("market") == "HK" and row.get("symbol") == "02510" for row in assets)

    issues = json.loads((ROOT / "config/observation_identity_issues.json").read_text(encoding="utf-8"))
    assert not any(row.get("symbol") == "02510" for row in issues.get("issues", []))
    assert any(row.get("symbol") == "02510" and row.get("expected_symbol") == "02525"
               and row.get("status") == "CONFIG_CORRECTED"
               for row in issues.get("resolved_issues", []))
