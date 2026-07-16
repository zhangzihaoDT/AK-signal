from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd


def build_portfolio_summary(summary_df: pd.DataFrame) -> dict[str, object]:
    df = summary_df.copy()
    if df.empty:
        return {
            "strong_count": 0,
            "observe_count": 0,
            "avg_score": None,
            "avg_relative_strength_20d": None,
            "strongest_stock": None,
            "weakest_stock": None,
        }

    valid = df[(df["symbol"].astype(str).str.upper() != "TBD") & (df["label"].astype(str).isin(["强势上行", "偏强", "震荡", "偏弱"]))].copy()
    if valid.empty:
        return {
            "strong_count": 0,
            "observe_count": 0,
            "avg_score": None,
            "avg_relative_strength_20d": None,
            "strongest_stock": None,
            "weakest_stock": None,
        }

    strong_count = int(valid["watch_level"].isin(["S", "A"]).sum()) if "watch_level" in valid.columns else 0
    observe_count = int((valid["watch_level"] == "B").sum()) if "watch_level" in valid.columns else 0

    avg_score = float(valid["score"].astype(float).mean()) if "score" in valid.columns else None

    rs = pd.to_numeric(valid.get("relative_strength_20d"), errors="coerce")
    avg_rs = float(rs.dropna().mean()) if rs is not None and rs.notna().any() else None

    strongest = valid.sort_values(["score", "relative_strength_20d"], ascending=[False, False]).iloc[0]
    weakest = valid.sort_values(["score", "relative_strength_20d"], ascending=[True, True]).iloc[0]

    strongest_stock = {"name": str(strongest["name"]), "symbol": str(strongest["symbol"]), "score": int(strongest["score"])}
    weakest_stock = {"name": str(weakest["name"]), "symbol": str(weakest["symbol"]), "score": int(weakest["score"])}

    return {
        "strong_count": strong_count,
        "observe_count": observe_count,
        "avg_score": round(avg_score, 2) if avg_score is not None else None,
        "avg_relative_strength_20d": round(avg_rs, 6) if avg_rs is not None else None,
        "strongest_stock": strongest_stock,
        "weakest_stock": weakest_stock,
    }


def write_portfolio_summary(report_date: date, summary: dict[str, object], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"portfolio_summary_{report_date:%Y%m%d}.json"
    payload = {"date": f"{report_date:%Y-%m-%d}", **summary}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path

