"""Provider-neutral daily price snapshot contract v1."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
import hashlib
import json
import math

VERSION = "market-facts-v1"


@dataclass(frozen=True)
class SeriesIdentity:
    security_id: str
    market: str
    exchange: str
    currency: str
    asset_type: str
    provider: str
    adapter: str
    endpoint: str
    provider_symbol: str
    price_basis: str
    adjustment_version: str  # Unknown is explicit; never inferred from matching prices.
    classification_version: str = "not_applicable"
    price_basis_verified: bool = False
    identity_verified: bool = False
    volume_unit: str = "unknown"
    turnover_unit: str = "unknown"

    def __post_init__(self):
        for key, value in asdict(self).items():
            if isinstance(value, str) and not value.strip():
                raise ValueError(f"missing identity field: {key}")
        if self.asset_type == "industry_index" and self.classification_version in {"not_applicable", "unknown"}:
            raise ValueError("industry classification version required")
        if self.market not in {"CN", "HK", "US"}:
            raise ValueError("unsupported market")
        if self.price_basis not in {"unadjusted", "vendor_forward_adjusted", "unknown", "index_level"}:
            raise ValueError("unsupported price basis")


def build_snapshot(identity: SeriesIdentity, bars: list[dict], target: date,
                   fetched_at: datetime) -> dict:
    """Reject ambiguous dates/invalid prices; preserve short history as a limitation.

    Calendar completeness requires independent exchange-calendar evidence and is
    deliberately UNKNOWN here. Row count alone never certifies a valid window.
    """
    if fetched_at.tzinfo is None:
        raise ValueError("fetched_at must be timezone aware")
    rows = sorted(bars, key=lambda r: r["source_trade_date"])
    dates = []
    for row in rows:
        day = date.fromisoformat(row["source_trade_date"])
        if day > target:
            raise ValueError("future bar")
        dates.append(day)
        for key in ("open", "high", "low", "close", "volume", "turnover"):
            value = row.get(key)
            if value is None and (key in {"volume", "turnover"} or
                                  (row.get("bar_kind") == "close_only" and key in {"open", "high", "low"})):
                continue
            if value is None or isinstance(value, bool) or not math.isfinite(value) or value < 0 or (key in {"open", "high", "low", "close"} and value == 0):
                raise ValueError(f"invalid {key}")
        if row.get("bar_kind") == "close_only" and any(row.get(k) is not None for k in ("open", "high", "low")):
            raise ValueError("close-only projection must not carry OHLC")
        if row.get("bar_kind") != "close_only" and not row["low"] <= min(row["open"], row["close"]) <= max(row["open"], row["close"]) <= row["high"]:
            raise ValueError("invalid OHLC range")
    if len(dates) != len(set(dates)):
        raise ValueError("duplicate source date")
    payload = {"contract_version": VERSION, "identity": asdict(identity),
               "target_trade_date": target.isoformat(), "bars": rows}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()
    def dated(row):
        stamp = row.get("source_timestamp")
        return bool(row.get("date_evidence") == "source_dated_daily" or
                    (stamp and datetime.fromisoformat(stamp).tzinfo is not None and
                     datetime.fromisoformat(stamp).date().isoformat() == row["source_trade_date"]))
    source_dates_verified = bool(rows) and all(dated(r) for r in rows)
    target_source_date_verified = any(r["source_trade_date"] == target.isoformat() and dated(r) for r in rows)
    return {**payload, "snapshot_id": digest, "fetched_at": fetched_at.isoformat(),
            "quality": {"target_present": target in dates, "row_count": len(rows),
                        "source_dates_verified": source_dates_verified,
                        "target_source_date_verified": target_source_date_verified,
                        "calendar_completeness": "UNKNOWN", "session_complete": "UNKNOWN",
                        "history_count_sufficient": {str(n): len(rows) >= n for n in (60, 120, 360)},
                        "price_basis_verified": identity.price_basis_verified,
                        "identity_verified": identity.identity_verified}}


def validate_snapshot(snapshot: dict) -> dict:
    """Revalidate serialized facts, including their content hash, before consumption."""
    if snapshot["contract_version"] != VERSION:
        raise ValueError("unsupported contract version")
    rebuilt = build_snapshot(SeriesIdentity(**snapshot["identity"]), snapshot["bars"],
                             date.fromisoformat(snapshot["target_trade_date"]),
                             datetime.fromisoformat(snapshot["fetched_at"]))
    # Older v1 snapshots predate explicit units. Hash the stored identity exactly.
    payload = {key: snapshot[key] for key in
               ("contract_version", "identity", "target_trade_date", "bars")}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()
    if snapshot["snapshot_id"] != digest:
        raise ValueError("snapshot hash mismatch")
    return {**snapshot, "quality": rebuilt["quality"]}
