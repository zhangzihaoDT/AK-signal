from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd


INACTIVE_THRESHOLD_DAYS = 365
_ACTIVE_SNAPSHOT_FILE = "active_universe.csv"


def safe_code(code: str) -> str:
    return code.replace(".", "_")


def _raw_path(raw_dir: Path, code: str) -> Path:
    return raw_dir / f"{safe_code(code)}.csv"


def load_master(raw_dir: Path) -> pd.DataFrame:
    path = raw_dir / "industry_master.csv"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, dtype=str)


def save_master(df: pd.DataFrame, raw_dir: Path) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / "industry_master.csv"
    tmp = path.with_suffix(".tmp")
    df.to_csv(tmp, index=False, encoding="utf-8-sig")
    os.replace(str(tmp), str(path))
    return path


def _active_snapshot_path(raw_dir: Path) -> Path:
    return raw_dir / _ACTIVE_SNAPSHOT_FILE


def load_active_snapshot(raw_dir: Path) -> list[str] | None:
    path = _active_snapshot_path(raw_dir)
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path, dtype=str)
        return df["industry_code"].tolist() if "industry_code" in df.columns else None
    except Exception:
        return None


def save_active_snapshot(codes: list[str], raw_dir: Path) -> Path:
    path = _active_snapshot_path(raw_dir)
    tmp = path.with_suffix(".tmp")
    pd.DataFrame({"industry_code": codes}).to_csv(tmp, index=False, encoding="utf-8-sig")
    os.replace(str(tmp), str(path))
    return path


def compute_active_codes(
    raw_dir: Path,
    master: pd.DataFrame | None = None,
) -> tuple[list[str], list[str], bool]:
    """
    返回 (stable_active, inactive, universe_changed)。

    stable_active: 优先使用 active snapshot；无 snapshot 时从数据活跃度推断。
    universe_changed: 数据推断结果与 snapshot 不一致时为 True。
    """
    if master is None:
        master = load_master(raw_dir)
    if master.empty:
        return [], [], False

    all_codes = master["industry_code"].tolist()
    cutoff = date.today() - timedelta(days=INACTIVE_THRESHOLD_DAYS)
    inferred_active: list[str] = []
    inferred_inactive: list[str] = []
    for code in all_codes:
        last = load_industry_latest_date(raw_dir, code)
        if last is not None and last >= cutoff:
            inferred_active.append(code)
        else:
            inferred_inactive.append(code)

    snapshot = load_active_snapshot(raw_dir)
    if snapshot is None:
        return inferred_active, inferred_inactive, False

    stable_active = snapshot
    stable_inactive = sorted(set(all_codes) - set(stable_active))

    inferred_set = set(inferred_active)
    snapshot_set = set(stable_active)
    universe_changed = (inferred_set != snapshot_set)

    return stable_active, stable_inactive, universe_changed


def load_industry_raw(raw_dir: Path, code: str) -> pd.DataFrame:
    path = _raw_path(raw_dir, code)
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, parse_dates=["trade_date"])


def load_industry_latest_date(raw_dir: Path, code: str) -> date | None:
    path = _raw_path(raw_dir, code)
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path, usecols=["trade_date"])
        if df.empty:
            return None
        return pd.to_datetime(df["trade_date"]).max().date()
    except Exception:
        return None


def save_industry_raw(df: pd.DataFrame, raw_dir: Path, code: str) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = _raw_path(raw_dir, code)
    tmp = path.with_suffix(".tmp")
    df.to_csv(tmp, index=False, encoding="utf-8-sig")
    os.replace(str(tmp), str(path))
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


def save_metrics_atomically(df: pd.DataFrame, processed_dir: Path) -> Path:
    processed_dir.mkdir(parents=True, exist_ok=True)
    path = processed_dir / "industry_daily_metrics.csv"
    tmp = path.with_suffix(".tmp")
    df.to_csv(tmp, index=False, encoding="utf-8-sig")
    tmp_df = pd.read_csv(tmp, parse_dates=["trade_date"])
    assert not tmp_df.empty, "refusing to publish empty metrics"
    os.replace(str(tmp), str(path))
    return path


def save_snapshot(df: pd.DataFrame, processed_dir: Path) -> Path:
    processed_dir.mkdir(parents=True, exist_ok=True)
    path = processed_dir / "latest_snapshot.csv"
    tmp = path.with_suffix(".tmp")
    df.to_csv(tmp, index=False, encoding="utf-8-sig")
    os.replace(str(tmp), str(path))
    return path


def save_rotation_matrix(df: pd.DataFrame, processed_dir: Path) -> Path:
    processed_dir.mkdir(parents=True, exist_ok=True)
    path = processed_dir / "rotation_matrix.csv"
    tmp = path.with_suffix(".tmp")
    df.to_csv(tmp, index=False, encoding="utf-8-sig")
    os.replace(str(tmp), str(path))
    return path


def save_report_csv(df: pd.DataFrame, reports_dir: Path, date_str: str) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"sw_industry_rps_{date_str}.csv"
    tmp = path.with_suffix(".tmp")
    df.to_csv(tmp, index=False, encoding="utf-8-sig")
    os.replace(str(tmp), str(path))
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


# --- Update status (confirmed / provisional) ---

_UPDATE_STATUS_FILE = "update_status.json"


def load_update_status(raw_dir: Path) -> dict[str, Any]:
    path = raw_dir / _UPDATE_STATUS_FILE
    default = {"latest_available_date": "", "latest_confirmed_date": "",
               "status": "", "source": ""}
    if not path.exists():
        return default
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        # 兼容遗留字段 latest_date → latest_confirmed_date
        if "latest_confirmed_date" not in raw or not raw.get("latest_confirmed_date"):
            raw["latest_confirmed_date"] = raw.get("latest_date", "")
        if "latest_available_date" not in raw or not raw.get("latest_available_date"):
            raw["latest_available_date"] = raw.get("latest_date", "")
        for k in default:
            raw.setdefault(k, "")
        return raw
    except Exception:
        return default


def save_update_status(
    raw_dir: Path,
    status: str,
    available_date: str,
    source: str,
    confirmed_date: str | None = None,
) -> Path:
    """写入更新状态，同时记录 available（含 provisional）和 confirmed 两个日期。

    Args:
        status: "confirmed" | "provisional" | "completed_provisional" | "waiting_for_source"
        available_date: 当前可获得的最新日期（含 provisional）
        source: 数据来源
        confirmed_date: 正式确认的最新日期（覆盖旧值），None 时保留旧值
    """
    existing = load_update_status(raw_dir)
    prev_confirmed = existing.get("latest_confirmed_date", "")
    path = raw_dir / _UPDATE_STATUS_FILE
    data = {
        "latest_available_date": available_date,
        "latest_confirmed_date": confirmed_date if confirmed_date else prev_confirmed,
        "status": status,
        "source": source,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(path))
    return path


def batch_save_industry_data(
    df: pd.DataFrame,
    raw_dir: Path,
    date_str: str,
) -> tuple[int, int]:
    """将批量数据按行业代码拆分写入每个行业的 CSV。

    df 必须包含 trade_date, industry_code, close 等列。
    每个行业的现有数据会与新增数据 merge（按 trade_date 去重）。

    Returns:
        (saved_count, error_count)
    """
    saved = 0
    errors = 0
    for code, group in df.groupby("industry_code"):
        group = group.drop(columns=["industry_code"], errors="ignore")
        cached = load_industry_raw(raw_dir, code)
        merged = merge_incremental(cached, group)
        try:
            save_industry_raw(merged, raw_dir, code)
            saved += 1
        except Exception:
            errors += 1
    return saved, errors


# --- Checkpoint for resumable updates ---

def checkpoint_path(raw_dir: Path) -> Path:
    return raw_dir / "update_checkpoint.json"


def save_checkpoint(raw_dir: Path, data: dict[str, Any]) -> Path:
    path = checkpoint_path(raw_dir)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(path))
    return path


def load_checkpoint(raw_dir: Path) -> dict[str, Any] | None:
    path = checkpoint_path(raw_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def clear_checkpoint(raw_dir: Path) -> None:
    path = checkpoint_path(raw_dir)
    if path.exists():
        path.unlink()
