from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd


WATCHLIST_COLUMNS = [
    "ts_code",
    "name",
    "first_seen",
    "last_seen",
    "max_score",
    "current_score",
    "days_in_watchlist",
    "status",
]


def _parse_report_date_from_path(p: Path) -> date | None:
    stem = p.stem
    parts = stem.split("_")
    if not parts:
        return None
    ymd = parts[-1]
    if len(ymd) != 8 or not ymd.isdigit():
        return None
    try:
        return date(int(ymd[0:4]), int(ymd[4:6]), int(ymd[6:8]))
    except ValueError:
        return None


def list_recent_report_paths(reports_dir: Path, n: int = 10) -> list[Path]:
    if not reports_dir.exists():
        return []
    items: list[tuple[date, Path]] = []
    for p in reports_dir.glob("trend_report_*.csv"):
        d = _parse_report_date_from_path(p)
        if d is None:
            continue
        items.append((d, p))
    items.sort(key=lambda x: x[0])
    return [p for _, p in items[-n:]]


def consecutive_c_days(symbol: str, reports_dir: Path, max_lookback: int = 10) -> int:
    paths = list_recent_report_paths(reports_dir, n=max_lookback)
    count = 0
    for p in reversed(paths):
        try:
            df = pd.read_csv(p, dtype=str)
        except Exception:
            break
        row = df[df["symbol"].astype(str) == symbol]
        if row.empty:
            break
        wl = str(row.iloc[0].get("watch_level", "")).strip()
        if wl == "C":
            count += 1
            continue
        break
    return count


def load_watchlist(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=WATCHLIST_COLUMNS)
    try:
        df = pd.read_csv(path, dtype=str).fillna("")
    except Exception:
        return pd.DataFrame(columns=WATCHLIST_COLUMNS)

    for c in WATCHLIST_COLUMNS:
        if c not in df.columns:
            df[c] = ""
    return df[WATCHLIST_COLUMNS].copy()


def update_watchlist(
    report_date: date,
    summary_df: pd.DataFrame,
    reports_dir: Path,
    watchlist_path: Path,
) -> pd.DataFrame:
    wl = load_watchlist(watchlist_path)

    today_str = f"{report_date:%Y-%m-%d}"
    today_candidates = summary_df.copy()
    today_candidates = today_candidates[(today_candidates["symbol"].astype(str).str.upper() != "TBD") & (today_candidates["watch_level"].isin(["S", "A", "B", "C"]))]

    upsert_syms = set(today_candidates[today_candidates["watch_level"].isin(["S", "A"])]["symbol"].astype(str).tolist())

    existing_syms = set(wl["ts_code"].astype(str).tolist())
    for sym in sorted(upsert_syms):
        row = today_candidates[today_candidates["symbol"].astype(str) == sym].iloc[0]
        name = str(row.get("name", ""))
        score = int(float(row.get("score", 0)))
        status = str(row.get("watch_level", ""))

        if sym in existing_syms:
            idx = wl.index[wl["ts_code"].astype(str) == sym][0]
            prev_last_seen = str(wl.at[idx, "last_seen"])
            wl.at[idx, "name"] = name
            wl.at[idx, "last_seen"] = today_str
            wl.at[idx, "current_score"] = str(score)
            wl.at[idx, "status"] = status
            try:
                wl.at[idx, "max_score"] = str(max(int(float(wl.at[idx, "max_score"] or 0)), score))
            except Exception:
                wl.at[idx, "max_score"] = str(score)
            try:
                days = int(float(wl.at[idx, "days_in_watchlist"] or 0))
            except Exception:
                days = 0
            if prev_last_seen != today_str:
                wl.at[idx, "days_in_watchlist"] = str(days + 1)
        else:
            wl = pd.concat(
                [
                    wl,
                    pd.DataFrame(
                        [
                            {
                                "ts_code": sym,
                                "name": name,
                                "first_seen": today_str,
                                "last_seen": today_str,
                                "max_score": str(score),
                                "current_score": str(score),
                                "days_in_watchlist": "1",
                                "status": status,
                            }
                        ]
                    ),
                ],
                ignore_index=True,
            )

    to_remove: list[str] = []
    for sym in wl["ts_code"].astype(str).tolist():
        if not sym or sym.upper() == "TBD":
            continue
        today_row = today_candidates[today_candidates["symbol"].astype(str) == sym]
        today_is_c = False
        if not today_row.empty:
            today_is_c = str(today_row.iloc[0].get("watch_level", "")).strip() == "C"

        streak = consecutive_c_days(sym, reports_dir, max_lookback=10)
        if today_is_c:
            streak += 1

        if streak >= 5:
            to_remove.append(sym)

    if to_remove:
        wl = wl[~wl["ts_code"].astype(str).isin(to_remove)].reset_index(drop=True)

    watchlist_path.parent.mkdir(parents=True, exist_ok=True)
    wl.to_csv(watchlist_path, index=False, encoding="utf-8-sig")
    return wl
