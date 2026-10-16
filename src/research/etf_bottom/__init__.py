"""ETF price-position calculation shared by the daily Observation report."""

from __future__ import annotations

from pathlib import Path

from src.common.paths import outputs_dir

STUDY_DIR = outputs_dir() / "research" / "etf_bottom"
