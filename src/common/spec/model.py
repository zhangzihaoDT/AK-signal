"""Typed, immutable settings for factual indicator and data-quality calculations."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IndicatorSpec:
    """RPS windows and thresholds consumed by market-observation calculations."""
    rps_short_window: int
    rps_medium_window: int
    rps_long_window: int
    rps_today_window: int
    rps_velocity_window: int
    data_quality_max_single_day_return: float
    data_quality_flag_window: int
    ma_default_window: int
    etf_strong_threshold: float
    etf_watch_threshold: float
    confirmation_strong_threshold: float
    confirmation_observe_threshold: float
    confirmation_neutral_threshold: float
    confirmation_broad_fraction: float
    confirmation_watch_proximity: float
    tier_gate_strong: float
    tier_gate_observe: float
    tier_broad_fraction: float
    tier_strong_trend_min: float

    @property
    def rps_windows(self) -> tuple[int, int, int]:
        return (self.rps_short_window, self.rps_medium_window, self.rps_long_window)
