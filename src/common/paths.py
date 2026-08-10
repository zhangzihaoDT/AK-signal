from __future__ import annotations

from pathlib import Path


_ROOT: Path | None = None


def project_root() -> Path:
    global _ROOT
    if _ROOT is None:
        _ROOT = Path(__file__).resolve().parents[2]
    return _ROOT


def config_dir() -> Path:
    return project_root() / "config"


def stock_pool_path() -> Path:
    return config_dir() / "stock_pool.csv"


def stock_universe_path() -> Path:
    return config_dir() / "stock_universe.yaml"


def sw_industry_confirmation_dir() -> Path:
    return processed_dir() / "sw_industry"


def sw_industry_rps_config_path() -> Path:
    return config_dir() / "sw_industry_rps.yaml"


def data_dir() -> Path:
    return project_root() / "data"


def raw_dir() -> Path:
    return data_dir() / "raw"


def processed_dir() -> Path:
    return data_dir() / "processed"


def sw_industry_raw_dir() -> Path:
    return raw_dir() / "sw_industry"


def sw_industry_processed_dir() -> Path:
    return processed_dir() / "sw_industry"


def state_dir() -> Path:
    return data_dir() / "state"


def asset_state_path() -> Path:
    return state_dir() / "asset_state.csv"


def outputs_dir() -> Path:
    return project_root() / "outputs"


def sw_industry_rps_output_dir() -> Path:
    return outputs_dir() / "sw_industry_rps"


# ── ETF Signal ───────────────────────────────────────────────────

def etf_signal_raw_dir() -> Path:
    return data_dir() / "etf_signal" / "raw"


def etf_signal_master_dir() -> Path:
    return data_dir() / "etf_signal" / "master"


def etf_signal_daily_dir() -> Path:
    return data_dir() / "etf_signal" / "daily"


def etf_signal_signals_dir() -> Path:
    return data_dir() / "etf_signal" / "signals"


def etf_signal_positions_dir() -> Path:
    return data_dir() / "etf_signal" / "positions"


def etf_signal_manifests_dir() -> Path:
    return data_dir() / "etf_signal" / "manifests"


def etf_signal_output_dir() -> Path:
    return outputs_dir() / "etf_signal"


def stock_metrics_dir() -> Path:
    """个股趋势指标 Observation 产物（Layer② Tier 确认 / Layer③ Selection 共同消费）。"""
    return outputs_dir() / "stock_metrics"


def docs_dir() -> Path:
    return project_root() / "docs"


def manifest_path() -> Path:
    return outputs_dir() / "manifest.json"
