"""Read-only production Longbridge source adapter; no trading or publication."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import os
from pathlib import Path
import shlex
from zoneinfo import ZoneInfo

from .contract import SeriesIdentity, build_snapshot


def identity_for(asset):
    if asset.market == "HK":
        exchange, symbol = "HKEX", f"{asset.symbol.zfill(5)}.HK"
    elif asset.market == "CN" and asset.exchange in {"SSE", "SZSE"}:
        exchange = asset.exchange
        symbol = f"{asset.symbol.zfill(6)}.{'SH' if exchange == 'SSE' else 'SZ'}"
    else:
        raise ValueError("unsupported or ambiguous exchange")
    return SeriesIdentity(f"{exchange}:{symbol.split('.')[0]}", asset.market, exchange,
                          asset.currency or ("HKD" if asset.market == "HK" else "CNY"),
                          "stock", "longbridge", "longbridge_sdk",
                          "history_candlesticks_by_date", symbol,
                          "vendor_forward_adjusted", "unknown")


def fetch_snapshot(ctx, identity, target, period, adjustment, start_date=None):
    # <1000 calendar days: below endpoint's 1000-bar cap, enough for 360 bars.
    bars = ctx.history_candlesticks_by_date(identity.provider_symbol, period, adjustment,
                                          start_date or target - timedelta(days=800), target)
    tz = ZoneInfo("Asia/Hong_Kong" if identity.market == "HK" else "Asia/Shanghai")
    rows = []
    for bar in bars:
        # SDK PyOffsetDateTimeWrapper uses datetime.fromtimestamp(epoch, None):
        # naive output is host-local, not market-local or UTC. astimezone reverses
        # that conversion before converting to the security's exchange timezone.
        # https://github.com/longbridge/openapi/blob/main/python/src/time.rs

        rows.append({"source_trade_date": bar.timestamp.astimezone(tz).date().isoformat(),
                     "source_timestamp": bar.timestamp.astimezone(tz).isoformat(),
                     "date_evidence": "source_dated_daily",
                     "timestamp_conversion": "sdk_host_local_to_market" if bar.timestamp.tzinfo is None else "aware_to_market",
                     **{key: float(getattr(bar, key)) for key in
                        ("open", "high", "low", "close", "volume", "turnover")}})
    return build_snapshot(identity, rows, target, datetime.now(tz))


def quote_context(env_file: Path | None = None):
    from longbridge.openapi import Config, QuoteContext
    # Credentials are environment-only. Never log config, tokens or SDK exceptions.
    if env_file:
        for line in env_file.read_text().splitlines():
            parts = shlex.split(line, comments=True)
            if len(parts) == 1 and "=" in parts[0]:
                key, value = parts[0].split("=", 1)
                if key in {"longbridge_KEY", "longbridge_SECRET", "longbridge_TOKEN",
                           "LONGBRIDGE_APP_KEY", "LONGBRIDGE_APP_SECRET", "LONGBRIDGE_ACCESS_TOKEN"}:
                    os.environ.setdefault(key, value)
    credentials = [os.environ.get(key) or os.environ.get(legacy) for key, legacy in
                   (("LONGBRIDGE_APP_KEY", "longbridge_KEY"),
                    ("LONGBRIDGE_APP_SECRET", "longbridge_SECRET"),
                    ("LONGBRIDGE_ACCESS_TOKEN", "longbridge_TOKEN"))]
    if not all(credentials):
        raise ValueError("missing Longbridge credentials")
    return QuoteContext(Config.from_apikey(*credentials, enable_print_quote_packages=False))
