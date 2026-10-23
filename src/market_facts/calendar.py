"""Bounded, independently versioned session calendars for pilot validation."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class SessionCalendar:
    exchange: str
    start: date
    end: date
    days: tuple[date, ...]
    closes: dict[str, str]
    version: str
    authority: str
    digest: str

    @classmethod
    def load(cls, path: Path):
        raw = json.loads(path.read_text())
        payload = {k: v for k, v in raw.items() if k != "sha256"}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        if raw["sha256"] != digest:
            raise ValueError("calendar hash mismatch")
        days = tuple(date.fromisoformat(d) for d in raw["sessions"])
        start, end = date.fromisoformat(raw["start"]), date.fromisoformat(raw["end"])
        if not days or tuple(sorted(set(days))) != days or not all(start <= d <= end for d in days):
            raise ValueError("invalid calendar sessions")
        if set(raw["closes"]) != set(raw["sessions"]):
            raise ValueError("calendar close evidence incomplete")
        if any(datetime.fromisoformat(v).tzinfo is None for v in raw["closes"].values()):
            raise ValueError("calendar close timezone unknown")
        return cls(raw["exchange"], start, end, days, raw["closes"], raw["version"], raw["authority"], digest)

    def assess(self, snapshot):
        from .contract import validate_snapshot
        snapshot = validate_snapshot(snapshot)
        exchange = snapshot["identity"]["exchange"]
        if exchange not in {"SSE": "XSHG", "SZSE": "XSHG", "HKEX": "XHKG"} or {
                "SSE": "XSHG", "SZSE": "XSHG", "HKEX": "XHKG"}[exchange] != self.exchange:
            raise ValueError("calendar exchange mismatch")
        target = date.fromisoformat(snapshot["target_trade_date"])
        actual = {date.fromisoformat(r["source_trade_date"]) for r in snapshot["bars"]}
        covered = self.start <= target <= self.end
        first = min(actual) if actual else target
        expected = {d for d in self.days if max(first, self.start) <= d <= target}
        missing = sorted(expected - actual)
        unexpected = sorted(d for d in actual if self.start <= d <= self.end and d not in self.days)
        windows = {}
        eligible = [d for d in self.days if d <= target]
        for n in (60, 120, 360):
            window = eligible[-n:]
            windows[str(n)] = ("CALENDAR_UNCOVERED" if not covered or len(window) < n else
                               "TARGET_NOT_SESSION" if target not in self.days else
                               "INSUFFICIENT_HISTORY" if not actual or window[0] < first else
                               "DATA_GAP" if set(window) - actual else
                               "NON_SESSION_BARS" if unexpected else "OK")
        close = self.closes.get(target.isoformat())
        complete = (datetime.fromisoformat(snapshot["fetched_at"]) >= datetime.fromisoformat(close)) if close else None
        return {"calendar_version": self.version, "calendar_sha256": self.digest,
                "calendar_authority": self.authority, "calendar_exchange": self.exchange,
                "target_is_session": target in self.days if covered else None,
                "session_complete": complete, "missing_sessions": [d.isoformat() for d in missing],
                "unexpected_sessions": [d.isoformat() for d in unexpected], "windows": windows,
                "full_history_covered": covered and first >= self.start}

    def index(self):
        return pd.DatetimeIndex(self.days)

    def expected_session(self, target: date) -> date:
        """Latest actual session at a bounded report date; never infer beyond coverage."""
        if not self.start <= target <= self.end:
            raise ValueError('calendar target outside coverage')
        eligible = [d for d in self.days if d <= target]
        if not eligible:
            raise ValueError('calendar has no preceding session')
        return eligible[-1]


def export_model_calendars(output: Path, start: date, end: date):
    """Optional dependency, explicit rule-model authority, never exchange-certified."""
    import exchange_calendars as xcals
    output.mkdir(parents=True, exist_ok=True)
    for name in ("XSHG", "XHKG"):
        c = xcals.get_calendar(name, start=start.isoformat(), end=end.isoformat())
        days = c.sessions
        payload = {"exchange": name, "start": start.isoformat(), "end": end.isoformat(),
                   "version": f"exchange_calendars:{xcals.__version__}:{name}",
                   "authority": "independent_rule_model",
                   "source": "https://github.com/gerrymanoim/exchange_calendars",
                   "sessions": [d.date().isoformat() for d in days],
                   "closes": {d.date().isoformat(): c.session_close(d).isoformat() for d in days}}
        payload["sha256"] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        (output / f"{name}.json").write_text(json.dumps(payload, indent=2))


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description='Export bounded independent rule-model calendars for pilot validation')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--start', type=date.fromisoformat, required=True)
    p.add_argument('--end', type=date.fromisoformat, required=True)
    args = p.parse_args()
    export_model_calendars(args.output, args.start, args.end)
