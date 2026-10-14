"""Offline, bounded exchange-calendar snapshot for daily orchestration only."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


def shanghai_now() -> datetime:
    return datetime.now(SHANGHAI)


class TradingCalendar:
    def __init__(self, path: Path):
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema_version") != 1 or data.get("exchange") != "SSE_A":
            raise ValueError("交易日历格式或市场不支持")
        self.start = date.fromisoformat(data["coverage_start"])
        self.end = date.fromisoformat(data["coverage_end"])
        if self.start > self.end or data.get("verified_through") != data["coverage_end"]:
            raise ValueError("交易日历覆盖未完整核验")
        sources = data.get("sources", [])
        if not isinstance(sources, list) or not sources or any(
                not isinstance(s, dict) or not isinstance(s.get('url'), str) or not s['url'].startswith(
                "https://www.sse.com.cn/disclosure/") for s in sources):
            raise ValueError("交易日历缺少交易所来源")
        source_years = {int(s['coverage'][:4]) for s in sources}
        if not set(range(self.start.year, self.end.year + 1)) <= source_years:
            raise ValueError("交易日历来源未覆盖全部年份")
        values = data["trading_days"]
        if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
            raise ValueError("交易日历日期列表格式不支持")
        digest = hashlib.sha256("\n".join(values).encode()).hexdigest()
        if digest != data["trading_days_sha256"]:
            raise ValueError("交易日历摘要不匹配")
        self.days = tuple(date.fromisoformat(v) for v in values)
        if not self.days or tuple(sorted(set(self.days))) != self.days or any(
                not self.start <= d <= self.end or d.weekday() >= 5 for d in self.days):
            raise ValueError("交易日历日期非法、重复或未排序")
        self.digest = digest

    def complete_day(self, at: datetime) -> str:
        if at.tzinfo is None:
            raise ValueError("交易日判定必须提供时区")
        local = at.astimezone(SHANGHAI)
        if not self.start <= local.date() <= self.end:
            raise ValueError("交易日历缺失覆盖或已过期，拒绝推断交易日")
        candidates = [d for d in self.days if d < local.date() or
                      (d == local.date() and local.time() >= time(16, 30))]
        if not candidates:
            raise ValueError("交易日历缺少最近完整交易日")
        return candidates[-1].strftime("%Y%m%d")

    def scheduled_target(self, at: datetime) -> tuple[str, datetime]:
        """Most recent 08:30 occurrence, even after sleep; never a historical loop."""
        if at.tzinfo is None:
            raise ValueError("调度时间必须提供时区")
        local = at.astimezone(SHANGHAI)
        # Check the actual wall date as well, so Jan 1 cannot reuse an expired year.
        if not self.start <= local.date() <= self.end:
            raise ValueError("交易日历缺失覆盖或已过期，拒绝推断交易日")
        occurrence = datetime.combine(local.date(), time(8, 30), SHANGHAI)
        if local < occurrence:
            occurrence -= timedelta(days=1)
        return self.complete_day(occurrence), occurrence


def default_calendar(root: Path) -> TradingCalendar:
    return TradingCalendar(root / "config" / "a_share_calendar.json")
