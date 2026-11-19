"""Screen price windows for an A-shaped (inverted-V) move."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class AShapeMatch:
    symbol: str
    start_index: int
    peak_index: int
    peak_end_index: int
    end_index: int
    peak_price: float
    rise: float
    drawdown: float
    retracement: float
    ascent_efficiency: float
    descent_efficiency: float


def _leg_efficiency(prices: list[float], direction: int) -> float:
    steps = [direction * (b - a) for a, b in zip(prices, prices[1:])]
    if sum(step > 0 for step in steps) < 2:
        return 0.0  # A single jump is not a leg, even if surrounded by flat bars.
    return direction * (prices[-1] - prices[0]) / sum(map(abs, steps))


def filter_a_shapes(
    series: Mapping[str, Iterable[float | None]],
    *,
    lookback: int | None = None,
    min_rise: float = 0.10,
    min_drawdown: float = 0.10,
    min_retracement: float = 0.60,
    min_leg_efficiency: float = 0.60,
    min_leg_steps: int = 3,
    max_peak_bars: int = 3,
) -> list[AShapeMatch]:
    """Return matches sorted by leg quality, then by peak-to-end drawdown.

    Supply oldest-to-newest prices sampled at the same frequency within each
    series. Each symbol is evaluated on its full series, or its last `lookback`
    observations (shorter series are skipped). This does not scan older windows.
    Missing, non-finite or non-positive prices inside the window disqualify it;
    check for absent timestamps and adjustment discontinuities before calling.

    The highest price must form one interior peak (a short contiguous plateau is
    allowed). Each leg needs enough steps in total and at least two price moves
    in the intended direction. Leg efficiency is net move / total absolute move.
    Rise is (peak-start)/start, drawdown is (peak-end)/peak, and retracement is
    (peak-end)/(peak-start). Metrics are fractions, not percentage points;
    retracement can exceed 1. Indices are zero-based in the original series.
    """
    for name, value, minimum in (
        ('min_leg_steps', min_leg_steps, 2), ('max_peak_bars', max_peak_bars, 1),
    ):
        if type(value) is not int or value < minimum:
            raise ValueError(f'{name} must be an integer >= {minimum}')
    min_size = 2 * min_leg_steps + 1
    if lookback is not None and (type(lookback) is not int or lookback < min_size):
        raise ValueError(f'lookback must be None or an integer >= {min_size}')
    if not isfinite(min_rise) or min_rise <= 0:
        raise ValueError('min_rise must be finite and > 0')
    if not 0 < min_drawdown < 1 or not 0 < min_leg_efficiency <= 1:
        raise ValueError('min_drawdown must be in (0, 1) and min_leg_efficiency in (0, 1]')
    if not isfinite(min_retracement) or min_retracement < 0:
        raise ValueError('min_retracement must be finite and >= 0')

    matches = []
    for symbol, values in series.items():
        raw = list(values)
        size = len(raw) if lookback is None else lookback
        if len(raw) < size or size < min_size:
            continue
        start = len(raw) - size
        try:
            prices = [float(value) for value in raw[start:] if value is not None]
        except (TypeError, ValueError, OverflowError):
            continue
        if len(prices) != size or any(not isfinite(p) or p <= 0 for p in prices):
            continue

        peak = max(prices)
        top = [i for i, price in enumerate(prices) if price == peak]
        left, right = top[0], top[-1]
        if len(top) != right - left + 1 or len(top) > max_peak_bars:
            continue
        if min(left, size - 1 - right) < min_leg_steps:
            continue

        gain, loss = peak - prices[0], peak - prices[-1]
        rise, drawdown = gain / prices[0], loss / peak
        if rise < min_rise or drawdown < min_drawdown:
            continue
        retracement = loss / gain
        if retracement < min_retracement:
            continue
        ascent = _leg_efficiency(prices[:left + 1], 1)
        descent = _leg_efficiency(prices[right:], -1)
        if min(ascent, descent) < min_leg_efficiency:
            continue
        matches.append(AShapeMatch(
            symbol, start, start + left, start + right, len(raw) - 1,
            peak, rise, drawdown, retracement, ascent, descent,
        ))

    return sorted(matches, key=lambda m: (
        -min(m.ascent_efficiency, m.descent_efficiency), -m.drawdown, m.symbol,
    ))
