"""Typed loader for immutable market-observation indicator settings."""
from __future__ import annotations

from functools import lru_cache
from typing import Any

import yaml

from src.common.paths import config_dir
from . import schema
from .model import IndicatorSpec


def _read_yaml(relative_path: str) -> dict[str, Any]:
    path = config_dir() / relative_path
    if not path.exists():
        raise FileNotFoundError(f"config/{relative_path} missing")
    with path.open("r", encoding="utf-8") as stream:
        return yaml.safe_load(stream) or {}


@lru_cache(maxsize=None)
def load_indicator_spec() -> IndicatorSpec:
    cfg = _read_yaml("indicator_spec.yaml")
    schema.validate_indicators(cfg)
    rps, moving_average = cfg["rps"], cfg["moving_average"]
    gates, confirmation = cfg["signal_gates"], cfg["confirmation"]
    quality = cfg.get("data_quality", {})
    etf = gates["etf"]
    tier = cfg.get("tier_confirmation", {})
    return IndicatorSpec(
        rps_short_window=int(rps["short_window"]),
        rps_medium_window=int(rps["medium_window"]),
        rps_long_window=int(rps["long_window"]),
        rps_today_window=int(rps["today_window"]),
        rps_velocity_window=int(rps["velocity_window"]),
        data_quality_max_single_day_return=float(quality["max_single_day_return"]),
        data_quality_flag_window=int(quality["flag_window"]),
        ma_default_window=int(moving_average["default_window"]),
        etf_strong_threshold=float(etf["strong_threshold"]),
        etf_watch_threshold=float(etf["watch_threshold"]),
        confirmation_strong_threshold=float(confirmation["strong_threshold"]),
        confirmation_observe_threshold=float(confirmation["observe_threshold"]),
        confirmation_neutral_threshold=float(confirmation["neutral_threshold"]),
        confirmation_broad_fraction=float(confirmation["broad_fraction"]),
        confirmation_watch_proximity=float(confirmation["watch_proximity"]),
        tier_gate_strong=float(tier["tier_gate_strong"]),
        tier_gate_observe=float(tier["tier_gate_observe"]),
        tier_broad_fraction=float(tier["broad_fraction"]),
        tier_strong_trend_min=float(tier["strong_trend_min"]),
    )
