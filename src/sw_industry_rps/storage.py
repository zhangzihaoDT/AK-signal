from __future__ import annotations

from pathlib import Path

import pandas as pd


def safe_code(code: str) -> str:
    return code.replace(".", "_")


def load_master(raw_dir: Path) -> pd.DataFrame:
    path = raw_dir / "industry_master.csv"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, dtype=str)


def save_master(df: pd.DataFrame, raw_dir: Path) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / "industry_master.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def load_industry_raw(raw_dir: Path, code: str) -> pd.DataFrame:
    path = raw_dir / f"{safe_code(code)}.csv"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, parse_dates=["trade_date"])


def save_industry_raw(df: pd.DataFrame, raw_dir: Path, code: str) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"{safe_code(code)}.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def merge_incremental(df_cached: pd.DataFrame, df_new: pd.DataFrame) -> pd.DataFrame:
    if df_cached.empty:
        return df_new.sort_values("trade_date").reset_index(drop=True)
    if df_new.empty:
        return df_cached
    out = pd.concat([df_cached, df_new], ignore_index=True)
    out["trade_date"] = pd.to_datetime(out["trade_date"], errors="coerce")
    out = out.dropna(subset=["trade_date"])
    out = out.drop_duplicates(subset=["trade_date"], keep="last")
    return out.sort_values("trade_date").reset_index(drop=True)


def load_metrics(processed_dir: Path) -> pd.DataFrame:
    path = processed_dir / "industry_daily_metrics.csv"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, parse_dates=["trade_date"])


def save_metrics(df: pd.DataFrame, processed_dir: Path) -> Path:
    processed_dir.mkdir(parents=True, exist_ok=True)
    path = processed_dir / "industry_daily_metrics.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def save_snapshot(df: pd.DataFrame, processed_dir: Path) -> Path:
    processed_dir.mkdir(parents=True, exist_ok=True)
    path = processed_dir / "latest_snapshot.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def save_rotation_matrix(df: pd.DataFrame, processed_dir: Path) -> Path:
    processed_dir.mkdir(parents=True, exist_ok=True)
    path = processed_dir / "rotation_matrix.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def save_report_csv(df: pd.DataFrame, reports_dir: Path, date_str: str) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"sw_industry_rps_{date_str}.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def load_previous_metrics(processed_dir: Path, lookback: int = 5) -> pd.DataFrame:
    path = processed_dir / "industry_daily_metrics.csv"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path, parse_dates=["trade_date"])
    if df.empty:
        return df
    latest_date = df["trade_date"].max()
    cutoff = latest_date - pd.Timedelta(days=lookback * 2)
    return df[df["trade_date"] >= cutoff].copy()
