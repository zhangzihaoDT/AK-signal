import pytest

from scripts.exploration.a_shape import filter_a_shapes


def test_filters_and_ranks_clear_and_noisy_a_shapes():
    matches = filter_a_shapes({
        'noisy': [100, 112, 110, 130, 150, 135, 137, 120, 105],
        'clear': [100, 110, 120, 140, 130, 120, 110],
        'monotone': [100, 110, 120, 130, 140, 150, 160],
        'valley': [140, 130, 120, 100, 120, 130, 140],
        'm_shape': [100, 120, 150, 110, 150, 130, 120, 100],
        'single_jumps': [100, 100, 100, 150, 150, 100, 100, 100],
        'shallow_retracement': [100, 110, 125, 150, 145, 140, 134],
        'choppy': [100, 145, 101, 150, 120, 132, 105],
    })
    assert [m.symbol for m in matches] == ['clear', 'noisy']
    m = matches[0]
    assert (m.start_index, m.peak_index, m.peak_end_index, m.end_index) == (0, 3, 3, 6)
    assert m.peak_price == 140
    assert m.rise == pytest.approx(0.40)
    assert m.drawdown == pytest.approx(30 / 140)
    assert m.retracement == pytest.approx(0.75)


def test_tail_window_keeps_original_indices_and_allows_short_plateau():
    result = filter_a_shapes({
        'tail': [None, 90, 100, 110, 120, 140, 140, 130, 120, 108],
        'old_a': [100, 110, 120, 140, 130, 120, 108, 120, 130, 144, 150],
        'too_short': [100, 110, 120, 140, 130, 120, 108],
    }, lookback=8)
    assert [(m.symbol, m.start_index, m.peak_index, m.peak_end_index, m.end_index)
            for m in result] == [('tail', 2, 5, 6, 9)]


@pytest.mark.parametrize('bad', [None, float('nan'), float('inf'), 0, -1])
def test_does_not_fill_bad_values(bad):
    assert filter_a_shapes({'x': [100, 110, bad, 140, 130, 120, 105]}) == []


def test_rejects_long_flat_top_and_impossible_window():
    assert filter_a_shapes({'x': [100, 110, 120, 140, 140, 140, 140, 130, 120, 105]}) == []
    with pytest.raises(ValueError, match='lookback'):
        filter_a_shapes({}, lookback=6)
