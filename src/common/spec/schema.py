"""Validation for immutable market-observation indicator settings."""
from __future__ import annotations

from typing import Any


class SpecValidationError(ValueError):
    pass


def _require(cfg: dict[str, Any], path: str, msg: str) -> Any:
    node: Any = cfg
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            raise SpecValidationError(f"{path}: {msg}")
        node = node[key]
    return node


def _num_in_range(value: Any, low: float, high: float, path: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise SpecValidationError(f"{path}: must be numeric, got {value!r}")
    if not low <= number <= high:
        raise SpecValidationError(f"{path}: must be in [{low}, {high}], got {number}")
    return number


def validate_indicators(cfg: dict[str, Any]) -> None:
    rps = _require(cfg, "rps", "must define rps windows")
    for window in ("short_window", "medium_window", "long_window", "today_window", "velocity_window"):
        if int(rps.get(window, 0)) <= 0:
            raise SpecValidationError(f"indicators.rps.{window}: must be > 0")
    quality = cfg.get("data_quality")
    if not isinstance(quality, dict):
        raise SpecValidationError("indicators.data_quality: required (max_single_day_return / flag_window)")
    _num_in_range(quality.get("max_single_day_return"), 0, 100,
                  "indicators.data_quality.max_single_day_return")
    if int(quality.get("flag_window", 0)) <= 0:
        raise SpecValidationError("indicators.data_quality.flag_window: must be > 0")

    # Preserve the published IndicatorSpec schema and values while retiring the
    # separate selection / strategy / execution runtime that previously read them.
    gates = _require(cfg, "signal_gates", "must define signal_gates")
    _num_in_range(gates["etf"].get("strong_threshold"), 0, 100, "signal_gates.etf.strong_threshold")
    _num_in_range(gates["etf"].get("watch_threshold"), 0, 100, "signal_gates.etf.watch_threshold")
    confirmation = _require(cfg, "confirmation", "must define confirmation thresholds")
    _num_in_range(confirmation.get("strong_threshold"), 0, 100, "confirmation.strong_threshold")
    _num_in_range(confirmation.get("observe_threshold"), 0, 100, "confirmation.observe_threshold")
    _num_in_range(confirmation.get("broad_fraction"), 0, 1, "confirmation.broad_fraction")
    _num_in_range(confirmation.get("watch_proximity"), 0, 100, "confirmation.watch_proximity")
    tier = _require(cfg, "tier_confirmation", "must define tier_confirmation thresholds")
    _num_in_range(tier.get("tier_gate_strong"), 0, 100, "tier_confirmation.tier_gate_strong")
    _num_in_range(tier.get("tier_gate_observe"), 0, 100, "tier_confirmation.tier_gate_observe")
    _num_in_range(tier.get("broad_fraction"), 0, 1, "tier_confirmation.broad_fraction")
    _num_in_range(tier.get("strong_trend_min"), 0, 100, "tier_confirmation.strong_trend_min")
