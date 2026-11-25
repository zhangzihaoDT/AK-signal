import pytest

from src.common.spec import schema
from src.common.spec.loaders import load_indicator_spec


def test_indicator_spec_preserves_published_observation_and_quality_settings():
    spec = load_indicator_spec()
    assert spec.rps_windows == (15, 20, 60)
    assert spec.rps_today_window == 1
    assert spec.rps_velocity_window == 5
    assert spec.data_quality_max_single_day_return == 20.0
    assert spec.data_quality_flag_window == 60
    assert spec.etf_strong_threshold == 80.0
    assert spec.confirmation_strong_threshold == 90.0


def test_indicator_spec_is_frozen():
    spec = load_indicator_spec()
    with pytest.raises(Exception):
        spec.rps_short_window = 10


def test_indicator_schema_rejects_invalid_quality_threshold():
    with pytest.raises(schema.SpecValidationError):
        schema.validate_indicators({
            "rps": {"short_window": 15, "medium_window": 20, "long_window": 60,
                    "today_window": 1, "velocity_window": 5},
            "data_quality": {"max_single_day_return": 200, "flag_window": 60},
        })
