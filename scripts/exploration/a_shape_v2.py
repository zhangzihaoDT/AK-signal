"""Multi-scale, soft-ranked detector of *completed* A-shaped (inverted-V) price moves.

Upgrade candidate for a_shape.py, not a trading system or drop-in replacement:
    scan_a_shapes_v2({"159680.SZ": close_prices}, lookbacks=(20, 40, 60, 120, 200))

Dependencies: numpy and scipy. Prices must be positive, finite, chronological,
with consistent adjustment and sampling. Missing bars/timestamps must be checked
upstream: this implementation does not pad or interpolate.

By default, it searches for A shapes ending at the last observed price. To search
historical episodes, use mode="history". All historical results are ex-post:
forward-testing must evaluate each as-of date using only data available then.
The score is a heuristic *shape similarity*, not a probability or trade signal.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from math import isfinite

import numpy as np
from scipy.signal import find_peaks, savgol_filter


@dataclass(frozen=True)
class AShapeCandidate:
    symbol: str
    start_index: int
    peak_index: int
    end_index: int
    window_bars: int
    score: float                 # Heuristic shape score, 0..100
    rise: float                  # (peak/start) - 1
    drawdown: float              # 1 - (end/peak)
    retracement: float           # (peak-end)/(peak-start), may exceed 1
    ascent_fit: float            # Signed linear trend quality, 0..1
    descent_fit: float           # Signed linear trend quality, 0..1
    peak_prominence: float       # Peak prominence of smoothed *log price*, log units
    top_sharpness: float         # 0..1, lower for broad plateaus


def _directed_r_squared(log_values: np.ndarray, direction: int) -> float:
    """Only reward a fitted trend with the expected sign (not pointwise monotonicity)."""
    x = np.arange(log_values.size, dtype=float)
    if log_values.size < 3 or np.std(log_values) < 1e-12:
        return 0.0
    correlation = float(np.corrcoef(x, log_values)[0, 1])
    if not isfinite(correlation) or direction * correlation <= 0:
        return 0.0
    return correlation ** 2


def _as_array(prices: Iterable[float | None]) -> np.ndarray:
    try:
        raw = list(prices)
        if any(x is None for x in raw):
            return np.empty(0, dtype=float)
        y = np.asarray(raw, dtype=float)
    except (ValueError, TypeError, OverflowError):
        return np.empty(0, dtype=float)
    if y.ndim != 1 or not np.all(np.isfinite(y)) or np.any(y <= 0):
        return np.empty(0, dtype=float)
    return y


def _evaluate_window(
    symbol: str,
    close: np.ndarray,
    end: int,
    lookback: int,
    *,
    min_leg_bars: int,
    min_rise: float,
    min_drawdown: float,
    smoothing_bars: int,
    min_peak_prominence: float,
) -> list[AShapeCandidate]:
    window_start = end + 1 - lookback
    if window_start < 0:
        return []
    y = close[window_start:end + 1]
    log_y = np.log(y)
    smooth_len = min(smoothing_bars, lookback if lookback % 2 else lookback - 1)
    smoothed = savgol_filter(log_y, smooth_len, 2, mode="interp")

    # Prominence disqualifies tiny noisy oscillations, unlike a raw local maximum.
    peaks, props = find_peaks(
        smoothed,
        prominence=np.log1p(min_peak_prominence),
        distance=min_leg_bars,
    )
    result: list[AShapeCandidate] = []
    for apex, prominence in zip(peaks, props["prominences"]):
        if apex < min_leg_bars or lookback - 1 - apex < min_leg_bars:
            continue

        # Seek a preceding *local base* instead of forcing Jan-01 to be the base.
        start = int(np.argmin(smoothed[:apex - min_leg_bars + 1]))
        if apex - start < min_leg_bars:
            continue
        peak = float(y[apex]); base = float(y[start]); last = float(y[-1])
        rise = peak / base - 1.0
        drawdown = 1.0 - last / peak
        if rise < min_rise or drawdown < min_drawdown:
            continue
        retracement = (peak - last) / (peak - base)
        if retracement <= 0:
            continue

        rise_fit = _directed_r_squared(log_y[start:apex + 1], +1)
        fall_fit = _directed_r_squared(log_y[apex:], -1)
        trend_quality = (rise_fit * fall_fit) ** 0.5
        if trend_quality == 0:
            continue

        # A sharp single crest scores better than a long rounded high.
        local_segment = smoothed[start:]
        near_top_bars = int(np.sum(local_segment >= smoothed[apex] - np.log1p(0.012)))
        total_bars = len(local_segment)
        sharpness = float(np.clip(1.0 - max(0, near_top_bars - 2) /
                                  max(2.0, total_bars * 0.20), 0, 1))

        ascent_bars = apex - start
        descent_bars = lookback - 1 - apex
        balance = (min(ascent_bars, descent_bars) /
                   max(ascent_bars, descent_bars)) ** 0.25
        amplitude_rise = min(1.0, rise / 0.25)
        amplitude_fall = min(1.0, drawdown / 0.25)
        retrace_quality = min(1.0, retracement / 0.8)
        prominence_quality = min(1.0, float(np.expm1(prominence)) / 0.15)

        # Weights are tunable and NOT statistically calibrated. Favor geometry.
        score = 100.0 * (
            0.40 * trend_quality
            + 0.15 * amplitude_rise
            + 0.15 * amplitude_fall
            + 0.10 * retrace_quality
            + 0.10 * sharpness
            + 0.05 * balance
            + 0.05 * prominence_quality
        )
        result.append(AShapeCandidate(
            symbol=symbol,
            start_index=window_start + start,
            peak_index=window_start + int(apex),
            end_index=end,
            window_bars=lookback,
            score=round(float(score), 2),
            rise=round(rise, 6),
            drawdown=round(drawdown, 6),
            retracement=round(retracement, 6),
            ascent_fit=round(rise_fit, 6),
            descent_fit=round(fall_fit, 6),
            peak_prominence=round(float(prominence), 6),
            top_sharpness=round(sharpness, 6),
        ))
    return result


def scan_a_shapes_v2(
    series: Mapping[str, Iterable[float | None]],
    *,
    lookbacks: tuple[int, ...] = (20, 40, 60, 120, 200),
    min_leg_bars: int = 4,
    min_rise: float = 0.05,
    min_drawdown: float = 0.05,
    min_peak_prominence: float = 0.04,
    smoothing_bars: int = 7,
    mode: str = "latest",
    history_step: int = 1,
    best_per_symbol: bool = True,
) -> list[AShapeCandidate]:
    """Rank A-shape candidates by a soft shape score (highest first).

    mode="latest": a pattern still visible at the last observed bar.
    mode="history": search every historical endpoint (at history_step bars).
    These are different questions, not interchangeable rankings.

    `best_per_symbol=True` keeps the best candidate per ETF across windows/endpoints.
    Historical ranking can over-select in hindsight. For backtests, only compute
    latest mode on the prefix available at the tested date.
    """
    if not lookbacks or any(type(w) is not int or w < 2 * min_leg_bars + 1 for w in lookbacks):
        raise ValueError("lookbacks must be >= 2*min_leg_bars+1")
    if type(min_leg_bars) is not int or min_leg_bars < 2:
        raise ValueError("min_leg_bars must be an integer >=2")
    if type(smoothing_bars) is not int or smoothing_bars < 3 or smoothing_bars % 2 == 0:
        raise ValueError("smoothing_bars must be an odd integer >=3")
    if any(not isfinite(x) or not 0 < x < 1 for x in
           (min_rise, min_drawdown, min_peak_prominence)):
        raise ValueError("min rise/drawdown/prominence must be in (0,1)")
    if mode not in {"latest", "history"}:
        raise ValueError("mode must be 'latest' or 'history'")
    if type(history_step) is not int or history_step < 1:
        raise ValueError("history_step must be a positive integer")
    if type(best_per_symbol) is not bool:
        raise ValueError("best_per_symbol must be bool")
    out: list[AShapeCandidate] = []
    for symbol, prices in series.items():
        close = _as_array(prices)
        if close.size < min(lookbacks):
            continue
        ends = ([len(close) - 1] if mode == "latest" else
                list(range(min(lookbacks) - 1, len(close), history_step)))
        for end in ends:
            for w in lookbacks:
                if end + 1 < w:
                    continue
                out.extend(_evaluate_window(
                    symbol, close, end, w,
                    min_leg_bars=min_leg_bars,
                    min_rise=min_rise,
                    min_drawdown=min_drawdown,
                    smoothing_bars=smoothing_bars,
                    min_peak_prominence=min_peak_prominence,
                ))
    out.sort(key=lambda m: (-m.score, -m.drawdown, m.symbol))
    if not best_per_symbol:
        return out
    seen: set[str] = set()
    best: list[AShapeCandidate] = []
    for match in out:
        if match.symbol not in seen:
            best.append(match)
            seen.add(match.symbol)
    return best


if __name__ == "__main__":
    rng = np.random.default_rng(8)
    n = 120
    A = np.r_[np.linspace(1.0, 1.55, 51), np.linspace(1.55, 1.10, 69)]
    A *= np.exp(rng.normal(0, 0.007, n))
    DOWN = np.linspace(1.6, 1.0, n)
    V = np.r_[np.linspace(1.5, 1.0, 50), np.linspace(1.0, 1.45, 70)]
    SIDEWAYS = 1.1 + rng.normal(0, 0.01, n)
    for match in scan_a_shapes_v2({"A": A, "DOWN": DOWN,
                                   "V": V, "SIDEWAYS": SIDEWAYS}):
        print(match)
