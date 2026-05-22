"""
run:
  default:
    - command: "python src/main.py"
      behavior: "默认行为等价于 refresh-needed（只更新需要更新的资产）"
    - command: "python src/main.py --only-symbols CN:510500,CN:518880"
      behavior: "仅运行中证500ETF和黄金ETF（用于预热缓存）"
"
  options:
    - flag: "--refresh-all",desc: "强制全部尝试在线刷新"
    - flag: "--refresh-missing",desc: "只更新无缓存资产"
    - flag: "--offline",desc: "完全不联网（有缓存就出报告；无缓存会 failed）"
    - flag: "--only-symbols ...",desc: "仅运行指定资产（用于预热缓存/小范围更新）"
"""

from __future__ import annotations

import argparse
import logging
import random
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from asset import Asset
from data_provider import AKShareProvider

import fetch_data
import indicators
import portfolio
import report
import scoring


def build_logger(level: str) -> logging.Logger:
    logger = logging.getLogger("a_stock_monitor")
    if logger.handlers:
        return logger
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_stock_pool(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path, dtype=str).fillna("")
    return df


def _normalize_market(raw: str) -> str:
    v = (raw or "").strip().upper()
    if v in {"A", "ASHARE", "A_SHARE", "CN", "CHINA"}:
        return "CN"
    if v in {"HK", "HKG"}:
        return "HK"
    if v in {"US", "USA"}:
        return "US"
    return v or "CN"


def _infer_cn_exchange(symbol: str) -> str:
    s = (symbol or "").strip()
    if s.startswith(("5", "6", "688", "689")):
        return "SSE"
    if s.startswith(("0", "1", "2", "3")):
        return "SZSE"
    return ""


def _default_currency(market: str) -> str:
    if market == "CN":
        return "CNY"
    if market == "HK":
        return "HKD"
    if market == "US":
        return "USD"
    return ""


def load_assets_from_pool(csv_path: Path) -> list[dict[str, object]]:
    df = load_stock_pool(csv_path)
    if df.empty:
        return []

    cols = {c.lower().strip(): c for c in df.columns}
    enabled_col = cols.get("enabled")
    symbol_col = cols.get("symbol")
    name_col = cols.get("name")
    market_col = cols.get("market")
    exchange_col = cols.get("exchange")
    currency_col = cols.get("currency")
    category_col = cols.get("category")
    update_policy_col = cols.get("update_policy")
    priority_col = cols.get("priority")
    note_col = cols.get("note")

    if symbol_col is None and "ts_code" in cols:
        symbol_col = cols["ts_code"]

    if symbol_col is None:
        raise ValueError(f"stock pool csv missing symbol column: {csv_path} columns={list(df.columns)}")

    items: list[dict[str, object]] = []
    for _, r in df.iterrows():
        if enabled_col:
            enabled_raw = str(r.get(enabled_col, "")).strip().lower()
            if enabled_raw in {"false", "0", "no", "n", "off"}:
                continue
        symbol = str(r.get(symbol_col, "")).strip()
        name = str(r.get(name_col, "")).strip() if name_col else ""
        market = _normalize_market(str(r.get(market_col, "")).strip() if market_col else "")

        exchange = str(r.get(exchange_col, "")).strip() if exchange_col else ""
        currency = str(r.get(currency_col, "")).strip() if currency_col else ""
        category = str(r.get(category_col, "")).strip() if category_col else ""
        update_policy = str(r.get(update_policy_col, "")).strip() if update_policy_col else ""
        priority = str(r.get(priority_col, "")).strip() if priority_col else ""
        note = str(r.get(note_col, "")).strip() if note_col else ""

        if not name:
            name = symbol

        if not market:
            market = "CN"

        if not currency:
            currency = _default_currency(market)

        if market == "CN" and not exchange:
            exchange = _infer_cn_exchange(symbol)

        asset = Asset(
            symbol=symbol,
            name=name,
            market=market,  # type: ignore[arg-type]
            exchange=exchange or None,
            currency=currency or None,
            category=category or None,
        )
        items.append(
            {
                "asset": asset,
                "note": note,
                "update_policy": (update_policy or "daily").lower(),
                "priority": (priority or "B").upper(),
            }
        )
    return items


def calc_change(curr_score: int, prev_score: int | None, prev_available: bool) -> str:
    if not prev_available:
        return "无变化"

    if prev_score is None:
        bucket = scoring.trend_bucket(curr_score)
        if bucket == "strong":
            return "新增强趋势"
        if bucket == "observe":
            return "新增观察"
        return "无变化"

    prev_bucket = scoring.trend_bucket(prev_score)
    curr_bucket = scoring.trend_bucket(curr_score)

    if prev_bucket == curr_bucket:
        if curr_bucket == "strong":
            return "维持强趋势"
        if curr_bucket == "observe":
            return "维持观察"
        return "无变化"

    if curr_bucket == "strong":
        return "新增强趋势"
    if prev_bucket == "strong":
        return "降级"
    if prev_bucket == "observe" and curr_bucket == "weak":
        return "退出观察"
    if prev_bucket == "weak" and curr_bucket == "observe":
        return "新增观察"
    return "无变化"


def calc_watch_level(
    score_value: int,
    relative_strength_20d: float | None,
    ma20: float | None,
    ma60: float | None,
    volume_ratio: float | None,
) -> str:
    if score_value < 30:
        return "C"
    if score_value < 70:
        return "B"

    rs = relative_strength_20d
    if rs is None:
        return "B"

    cond_ma = ma20 is not None and ma60 is not None and ma20 > ma60
    cond_vol = volume_ratio is not None and volume_ratio > 1.2
    if rs > 0.15 and cond_ma and cond_vol:
        return "S"
    if rs > 0:
        return "A"
    return "B"


def calc_action(
    score_value: int,
    watch_level: str,
    relative_strength_20d: float | None,
    volume_ratio: float | None,
    price_near_ma20: bool | None,
    drawdown_from_high: float | None,
    change: str,
) -> str:
    if drawdown_from_high is not None and drawdown_from_high >= 0.15:
        return "风险警戒"
    if score_value < 30 or watch_level == "C":
        return "剔除观察"

    rs = relative_strength_20d
    vr = volume_ratio
    if watch_level in ["S", "A"]:
        if vr is not None and vr > 1.5:
            return "继续跟踪"
        if change == "新增强趋势":
            return "重点观察"
        if rs is not None and rs > 0:
            return "重点观察"
        return "继续跟踪"

    if watch_level == "B":
        if price_near_ma20:
            return "等待突破"
        return "观察等待"

    return "无变化"


def _raw_cache_path(raw_dir: Path, asset: Asset) -> Path:
    safe_symbol = str(asset.symbol).replace("/", "_").replace("\\", "_").replace(":", "_")
    return raw_dir / f"{asset.market}_{safe_symbol}.csv"


def _load_cached_raw(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["date"])
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"], errors="coerce").astype("datetime64[ns]")
    for c in ["open", "high", "low", "close", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    return df


ASSET_STATE_COLUMNS = [
    "market",
    "symbol",
    "last_success_date",
    "last_attempt_date",
    "last_status",
    "fail_count",
    "next_retry_date",
    "data_source",
]


def _parse_iso_date(s: object) -> date | None:
    txt = "" if s is None else str(s).strip()
    if not txt:
        return None
    try:
        return date.fromisoformat(txt)
    except Exception:
        return None


def load_asset_state(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=ASSET_STATE_COLUMNS)
    try:
        df = pd.read_csv(path, dtype=str).fillna("")
    except Exception:
        return pd.DataFrame(columns=ASSET_STATE_COLUMNS)
    for c in ASSET_STATE_COLUMNS:
        if c not in df.columns:
            df[c] = ""
    return df[ASSET_STATE_COLUMNS].copy()


def save_asset_state(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = df.copy()
    for c in ASSET_STATE_COLUMNS:
        if c not in out.columns:
            out[c] = ""
    out = out[ASSET_STATE_COLUMNS].copy()
    out.to_csv(path, index=False, encoding="utf-8-sig")


def cached_last_date(raw_path: Path) -> date | None:
    if not raw_path.exists():
        return None
    try:
        df = pd.read_csv(raw_path, usecols=["date"], parse_dates=["date"])
    except Exception:
        return None
    if df.empty or "date" not in df.columns:
        return None
    try:
        d = pd.to_datetime(df["date"], errors="coerce").dropna().max()
    except Exception:
        return None
    if d is None or pd.isna(d):
        return None
    return d.date()


def _merge_ohlcv(df_cached: pd.DataFrame, df_new: pd.DataFrame) -> pd.DataFrame:
    if df_cached.empty:
        return df_new.sort_values("date").reset_index(drop=True)
    if df_new.empty:
        return df_cached.sort_values("date").reset_index(drop=True)
    out = pd.concat([df_cached, df_new], ignore_index=True)
    out["date"] = pd.to_datetime(out["date"], errors="coerce").astype("datetime64[ns]")
    out = out.dropna(subset=["date"]).copy()
    out = out.drop_duplicates(subset=["date"], keep="last")
    return out.sort_values("date").reset_index(drop=True)


def decide_refresh_mode(
    asset: Asset,
    update_policy: str,
    state_df: pd.DataFrame,
    raw_path: Path,
    today: date,
    refresh_all: bool,
    refresh_missing: bool,
    offline: bool,
) -> tuple[str, str | None, str | None]:
    has_cache = raw_path.exists()
    last_cache = cached_last_date(raw_path)

    if offline:
        return ("cache" if has_cache else "failed", None, None)

    if refresh_all:
        if asset.market == "CN" and has_cache and last_cache is not None and last_cache < today:
            return ("online_incremental", f"{(last_cache + timedelta(days=1)):%Y%m%d}", f"{today:%Y%m%d}")
        return ("online_full", None, None)

    if refresh_missing:
        return ("online_full", None, None) if not has_cache else ("cache", None, None)

    if not has_cache:
        return ("online_full", None, None)

    key_df = state_df[(state_df["market"].astype(str) == asset.market) & (state_df["symbol"].astype(str) == asset.symbol)]
    row = key_df.iloc[0] if not key_df.empty else None

    if row is not None:
        next_retry = _parse_iso_date(row.get("next_retry_date"))
        if next_retry is not None and today < next_retry:
            return ("cache", None, None)

    pol = (update_policy or "daily").strip().lower()
    if pol == "manual":
        return ("cache", None, None)

    if last_cache is not None and last_cache >= today:
        return ("cache", None, None)

    if pol == "weekly" and last_cache is not None and (today - last_cache).days < 7:
        return ("cache", None, None)

    if asset.market == "CN" and last_cache is not None:
        return ("online_incremental", f"{(last_cache + timedelta(days=1)):%Y%m%d}", f"{today:%Y%m%d}")
    return ("online_full", None, None)


def update_asset_state(
    state_df: pd.DataFrame,
    asset: Asset,
    today: date,
    data_source: str,
    attempted_online: bool,
    latest_data_date: date | None,
) -> pd.DataFrame:
    out = state_df.copy()
    for c in ASSET_STATE_COLUMNS:
        if c not in out.columns:
            out[c] = ""

    mask = (out["market"].astype(str) == asset.market) & (out["symbol"].astype(str) == asset.symbol)
    if not mask.any():
        out = pd.concat(
            [
                out,
                pd.DataFrame(
                    [
                        {
                            "market": asset.market,
                            "symbol": asset.symbol,
                            "last_success_date": "",
                            "last_attempt_date": "",
                            "last_status": "",
                            "fail_count": "0",
                            "next_retry_date": "",
                            "data_source": "",
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )
        mask = (out["market"].astype(str) == asset.market) & (out["symbol"].astype(str) == asset.symbol)

    idx = out.index[mask][0]
    out.at[idx, "last_attempt_date"] = today.isoformat()
    out.at[idx, "data_source"] = data_source

    try:
        prev_fc = int(float(out.at[idx, "fail_count"] or 0))
    except Exception:
        prev_fc = 0

    if data_source == "online":
        out.at[idx, "last_status"] = "online"
        out.at[idx, "fail_count"] = "0"
        out.at[idx, "next_retry_date"] = ""
        if latest_data_date is not None:
            out.at[idx, "last_success_date"] = latest_data_date.isoformat()
        return out

    if data_source == "cache":
        out.at[idx, "last_status"] = "cache"
        if attempted_online:
            fc = prev_fc + 1
            out.at[idx, "fail_count"] = str(fc)
            cooldown_days = min(7, 2 ** (min(fc, 3) - 1))
            out.at[idx, "next_retry_date"] = (today + timedelta(days=cooldown_days)).isoformat()
        return out

    out.at[idx, "last_status"] = "failed"
    if attempted_online:
        fc = prev_fc + 1
        out.at[idx, "fail_count"] = str(fc)
        cooldown_days = min(7, 2 ** (min(fc, 3) - 1))
        out.at[idx, "next_retry_date"] = (today + timedelta(days=cooldown_days)).isoformat()
    return out


def load_or_fetch_daily(
    asset: Asset,
    provider: AKShareProvider,
    raw_dir: Path,
    logger: logging.Logger,
    max_retries: int = 3,
    retry_sleep_base: float = 1.0,
    start_date: str | None = None,
    end_date: str | None = None,
    merge_with_cache: bool = False,
) -> tuple[pd.DataFrame, str]:
    raw_dir.mkdir(parents=True, exist_ok=True)
    cache_path = _raw_cache_path(raw_dir, asset)
    asset_key = f"{asset.market}_{asset.symbol}"

    def _is_retryable_error(err: Exception) -> bool:
        seen: set[int] = set()
        cur: BaseException | None = err
        names: set[str] = set()
        texts: list[str] = []
        while cur is not None and id(cur) not in seen:
            seen.add(id(cur))
            names.add(type(cur).__name__)
            try:
                texts.append(str(cur))
            except Exception:
                pass
            cur = getattr(cur, "__cause__", None) or getattr(cur, "__context__", None)

        retryable_names = {
            "RemoteDisconnected",
            "ConnectionError",
            "Timeout",
            "ReadTimeout",
            "ConnectTimeout",
            "ChunkedEncodingError",
            "SSLError",
            "JSONDecodeError",
        }
        if names & retryable_names:
            return True

        merged = " | ".join(t for t in texts if t).lower()
        if "fetch returned empty" in merged:
            return True
        if "no value to decode" in merged:
            return True
        if "remote end closed connection" in merged:
            return True
        if "connection aborted" in merged:
            return True
        if "remotedisconnected" in merged:
            return True
        return False

    last_err: Exception | None = None
    eff_max_retries = int(max_retries)
    eff_retry_sleep_base = float(retry_sleep_base)
    if asset.market == "CN" and eff_max_retries == 3 and abs(eff_retry_sleep_base - 1.0) < 1e-9:
        eff_max_retries = 5
        eff_retry_sleep_base = 2.0

    retries = max(1, eff_max_retries)
    cn_sources = ["em", "sina", "tx"]
    for i in range(1, retries + 1):
        try:
            if asset.market == "CN":
                time.sleep(random.uniform(1.2, 2.5))
                source = cn_sources[(i - 1) % len(cn_sources)]
            else:
                time.sleep(random.uniform(0.1, 0.6))
                source = None

            df = provider.get_daily(asset, start_date=start_date, end_date=end_date, source=source)
            if df is None or df.empty:
                raise RuntimeError("fetch returned empty")
            if source:
                logger.info("fetch online success: %s (source=%s)", asset_key, source)
            else:
                logger.info("fetch online success: %s", asset_key)
            if merge_with_cache and cache_path.exists():
                df_cached = _load_cached_raw(cache_path)
                df = _merge_ohlcv(df_cached, df)
            df.to_csv(cache_path, index=False, encoding="utf-8")
            logger.info("raw data saved: %s (%d rows)", cache_path, len(df))
            return df, "online"
        except Exception as e:
            last_err = e
            if not _is_retryable_error(e):
                logger.warning("fetch failed (non-retryable), using fallback: %s (err=%s)", asset_key, e)
                break
            logger.warning("fetch retry %d/%d: %s (err=%s)", i, retries, asset_key, e)
            if i < retries:
                base = eff_retry_sleep_base * (2 ** (i - 1))
                jitter = random.uniform(0.0, 0.4 * base)
                time.sleep(base + jitter)

    if cache_path.exists():
        logger.warning("fetch failed, using cached raw data: %s", asset_key)
        return _load_cached_raw(cache_path), "cache"

    logger.error("fetch failed and no cache, skip asset: %s (err=%s)", asset_key, last_err)
    raise RuntimeError("fetch failed and no cache") from last_err


def analyze_asset(
    asset: Asset,
    note: str,
    data_source: str,
    df_raw: pd.DataFrame,
    bench: pd.DataFrame,
    processed_dir: Path,
    plot_last_n: int,
    prev_score_by_symbol: dict[str, int],
    prev_available: bool,
    report_date: date,
) -> tuple[dict[str, object], tuple[str, object]]:
    df_ind = indicators.add_indicators(df_raw)
    df_ind["date"] = pd.to_datetime(df_ind["date"], errors="coerce").astype("datetime64[ns]")
    if not bench.empty:
        df_ind = pd.merge_asof(
            df_ind.sort_values("date"),
            bench[["date", "bench_return_20d"]].sort_values("date"),
            on="date",
            direction="backward",
        )
        df_ind["relative_strength_20d"] = df_ind["return_20d"] - df_ind["bench_return_20d"]
    else:
        df_ind["relative_strength_20d"] = pd.NA

    processed_dir.mkdir(parents=True, exist_ok=True)
    processed_path = processed_dir / f"{asset.market}_{asset.symbol}.csv"
    df_ind.to_csv(processed_path, index=False, encoding="utf-8")

    score_value, details = scoring.score_latest_row(df_ind)
    label = scoring.score_trend_label(score_value)
    prev_key = f"{asset.market}:{asset.symbol}"
    prev_score = prev_score_by_symbol.get(prev_key)
    if prev_score is None:
        prev_score = prev_score_by_symbol.get(asset.symbol)
    change = calc_change(score_value, prev_score, prev_available)

    latest_row = df_ind.iloc[-1]
    latest_date = latest_row["date"]
    rs20_raw = latest_row.get("relative_strength_20d")
    rs20 = float(rs20_raw) if pd.notna(rs20_raw) else None
    ma20_raw = latest_row.get("ma20")
    ma60_raw = latest_row.get("ma60")
    ma20 = float(ma20_raw) if pd.notna(ma20_raw) else None
    ma60 = float(ma60_raw) if pd.notna(ma60_raw) else None
    vr_raw = latest_row.get("volume_ratio")
    volume_ratio = float(vr_raw) if pd.notna(vr_raw) else None
    pnm_raw = latest_row.get("price_near_ma20")
    price_near_ma20 = bool(pnm_raw) if pd.notna(pnm_raw) else None
    dd_raw = latest_row.get("drawdown_from_high")
    drawdown_from_high = float(dd_raw) if pd.notna(dd_raw) else None

    watch_level_value = calc_watch_level(score_value, rs20, ma20, ma60, volume_ratio)
    action_value = calc_action(
        score_value,
        watch_level_value,
        rs20,
        volume_ratio,
        price_near_ma20,
        drawdown_from_high,
        change,
    )

    row = {
        "name": asset.name,
        "symbol": asset.symbol,
        "market": asset.market,
        "exchange": asset.exchange or "",
        "currency": asset.currency or "",
        "category": asset.category or "",
        "data_source": data_source,
        "data_freshness_days": (
            int((report_date - latest_date.date()).days) if hasattr(latest_date, "date") and latest_date is not None else None
        ),
        "date": latest_date.date() if hasattr(latest_date, "date") else latest_date,
        "close": float(latest_row["close"]),
        "score_trend": score_value,
        "score": score_value,
        "label": label,
        "watch_level": watch_level_value,
        "action": action_value,
        "change": change,
        "relative_strength_20d": rs20,
        "risk_flags": scoring.risk_flags_text(latest_row),
        "reason": details.get("reason", ""),
        "note": note,
    }

    df_plot = df_ind.tail(plot_last_n).copy()
    chart_key = f"{asset.market}:{asset.symbol}"
    fig = report.build_price_chart(df_plot, title=f"{asset.name}({asset.symbol},{asset.market}) - 趋势评分 {score_value} / 100")
    return row, (chart_key, fig)


def sort_report_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    wl_rank = {"": 0, "C": 1, "B": 2, "A": 3, "S": 4}
    out = df.copy()
    out["_wl_rank"] = out.get("watch_level", "").map(wl_rank).fillna(0)
    rs = pd.to_numeric(out.get("relative_strength_20d"), errors="coerce")
    out["_rs_rank"] = rs.fillna(-1e18)
    out = out.sort_values(["_wl_rank", "score", "_rs_rank", "symbol"], ascending=[False, False, False, True]).drop(
        columns=["_wl_rank", "_rs_rank"]
    )
    return out.reset_index(drop=True)


def save_watchlist(report_df: pd.DataFrame, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    report.reorder_summary_df(report.public_summary_df(report_df)).to_csv(out_path, index=False, encoding="utf-8-sig")


def save_skipped_assets(skipped: list[dict[str, str]], reports_dir: Path, report_date: date) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"skipped_assets_{report_date:%Y%m%d}.csv"
    df = pd.DataFrame(skipped, columns=["symbol", "name", "market", "reason"])
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def run(args: argparse.Namespace) -> tuple[Path, Path]:
    root = project_root()
    logger = build_logger(args.log_level)
    report_date = date.today()

    pool_path = root / "config" / "stock_pool.csv"
    pool_items = load_assets_from_pool(pool_path)
    if getattr(args, "only_symbols", ""):
        raw_tokens = [t.strip() for t in str(args.only_symbols).split(",") if t.strip()]
        wanted: set[tuple[str | None, str]] = set()
        for tok in raw_tokens:
            if ":" in tok:
                mkt, sym = tok.split(":", 1)
                wanted.add((mkt.strip().upper() or None, sym.strip()))
                continue
            if "_" in tok:
                mkt, sym = tok.split("_", 1)
                wanted.add((mkt.strip().upper() or None, sym.strip()))
                continue
            wanted.add((None, tok))

        filtered: list[dict[str, object]] = []
        for item in pool_items:
            asset = item["asset"]
            assert isinstance(asset, Asset)
            if (asset.market, asset.symbol) in wanted or (None, asset.symbol) in wanted:
                filtered.append(item)
        pool_items = filtered
        logger.info("filtered by --only-symbols: %s (%d assets)", ",".join(raw_tokens), len(pool_items))

    raw_dir = root / "data" / "raw"
    processed_dir = root / "data" / "processed"
    reports_dir = root / "data" / "reports"

    end_date = args.end_date or "22220101"
    fetch_cfg = fetch_data.FetchConfig(start_date=args.start_date, end_date=end_date, adjust=args.adjust)

    bench_raw = fetch_data.load_or_fetch_hs300(raw_dir=raw_dir, cfg=fetch_cfg, logger=logger, force=args.force)
    bench = bench_raw[["date", "close"]].rename(columns={"close": "bench_close"}).copy() if not bench_raw.empty else pd.DataFrame()
    if not bench.empty:
        bench["date"] = pd.to_datetime(bench["date"], errors="coerce").astype("datetime64[ns]")
        bench["bench_return_20d"] = bench["bench_close"].pct_change(20)

    prev_summary = report.load_previous_summary(reports_dir, report_date)
    prev_available = not prev_summary.empty
    prev_score_by_symbol: dict[str, int] = {}
    if not prev_summary.empty and "symbol" in prev_summary.columns:
        for _, r in prev_summary.iterrows():
            sym = str(r.get("symbol", "")).strip()
            mkt = str(r.get("market", "")).strip().upper()
            if not sym or sym.upper() == "TBD":
                continue
            try:
                key = f"{mkt}:{sym}" if mkt else sym
                prev_score_by_symbol[key] = int(float(r.get("score_trend", r.get("score", 0))))
            except Exception:
                continue

    provider = AKShareProvider(cfg=fetch_cfg, logger=logger)
    rows: list[dict[str, object]] = []
    charts: dict[str, object] = {}
    skipped: list[dict[str, str]] = []
    state_path = root / "data" / "asset_state.csv"
    state_df = load_asset_state(state_path)

    refresh_all = bool(getattr(args, "refresh_all", False))
    refresh_missing = bool(getattr(args, "refresh_missing", False))
    offline = bool(getattr(args, "offline", False))

    for item in pool_items:
        asset = item["asset"]
        assert isinstance(asset, Asset)
        note = str(item.get("note", "")).strip()
        update_policy = str(item.get("update_policy", "daily")).strip().lower()

        if not asset.symbol or asset.symbol.strip().upper() == "TBD":
            logger.warning("skip placeholder asset: (%s,%s)", asset.market, asset.symbol)
            skipped.append({"symbol": asset.symbol, "name": asset.name, "market": asset.market, "reason": "TBD placeholder"})
            continue

        raw_path = _raw_cache_path(raw_dir, asset)
        mode, inc_start, inc_end = decide_refresh_mode(
            asset=asset,
            update_policy=update_policy,
            state_df=state_df,
            raw_path=raw_path,
            today=report_date,
            refresh_all=refresh_all,
            refresh_missing=refresh_missing,
            offline=offline,
        )

        attempted_online = mode.startswith("online")
        try:
            if mode == "cache":
                if not raw_path.exists():
                    raise RuntimeError("offline/cache-only but no raw cache")
                df_raw = _load_cached_raw(raw_path)
                data_source = "cache"
            elif mode == "online_incremental":
                df_raw, data_source = load_or_fetch_daily(
                    asset,
                    provider,
                    raw_dir=raw_dir,
                    logger=logger,
                    start_date=inc_start,
                    end_date=inc_end,
                    merge_with_cache=True,
                )
            else:
                df_raw, data_source = load_or_fetch_daily(asset, provider, raw_dir=raw_dir, logger=logger)

            if df_raw is None or df_raw.empty:
                raise RuntimeError("empty daily data")

            try:
                latest_data_date = pd.to_datetime(df_raw["date"], errors="coerce").dropna().max()
                latest_date = latest_data_date.date() if latest_data_date is not None and pd.notna(latest_data_date) else None
            except Exception:
                latest_date = None
            state_df = update_asset_state(
                state_df=state_df,
                asset=asset,
                today=report_date,
                data_source=data_source,
                attempted_online=attempted_online,
                latest_data_date=latest_date,
            )

            row, (chart_key, fig) = analyze_asset(
                asset=asset,
                note=note,
                data_source=data_source,
                df_raw=df_raw,
                bench=bench,
                processed_dir=processed_dir,
                plot_last_n=args.plot_last_n,
                prev_score_by_symbol=prev_score_by_symbol,
                prev_available=prev_available,
                report_date=report_date,
            )
            rows.append(row)
            charts[chart_key] = fig
        except Exception as e:
            skipped.append({"symbol": asset.symbol, "name": asset.name, "market": asset.market, "reason": str(e)})
            state_df = update_asset_state(
                state_df=state_df,
                asset=asset,
                today=report_date,
                data_source="failed",
                attempted_online=attempted_online,
                latest_data_date=None,
            )
            rows.append(
                {
                    "name": asset.name,
                    "symbol": asset.symbol,
                    "market": asset.market,
                    "exchange": asset.exchange or "",
                    "currency": asset.currency or "",
                    "category": asset.category or "",
                    "data_source": "failed",
                    "data_freshness_days": None,
                    "date": None,
                    "close": None,
                    "score": 0,
                    "score_trend": 0,
                    "label": "无数据",
                    "watch_level": "",
                    "action": "跳过",
                    "change": "无变化",
                    "relative_strength_20d": None,
                    "risk_flags": "",
                    "reason": str(e),
                    "note": note,
                }
            )

        if asset.market == "CN":
            time.sleep(random.uniform(2.0, 4.0))
        else:
            time.sleep(random.uniform(0.5, 2.0))

    summary = sort_report_df(pd.DataFrame(rows))

    portfolio_summary = portfolio.build_portfolio_summary(summary)
    ds = summary.get("data_source", pd.Series([], dtype=str)).astype(str)
    fetch_status_summary = {
        "online_count": int((ds == "online").sum()),
        "cache_count": int((ds == "cache").sum()),
        "failed_count": int((ds == "failed").sum()),
    }
    portfolio_summary["fetch_status_summary"] = fetch_status_summary
    portfolio_json = portfolio.write_portfolio_summary(report_date, portfolio_summary, out_dir=reports_dir)
    logger.info("portfolio summary saved: %s", portfolio_json)

    save_asset_state(state_df, state_path)
    logger.info("asset state saved: %s (%d rows)", state_path, len(state_df))

    csv_path, html_path = report.write_daily_report(report_date, summary, charts, out_dir=reports_dir, portfolio_summary=portfolio_summary)
    logger.info("report csv saved: %s", csv_path)
    logger.info("report html saved: %s", html_path)

    watchlist_path = root / "data" / "watchlist.csv"
    save_watchlist(summary, watchlist_path)
    logger.info("watchlist saved: %s (%d rows)", watchlist_path, len(summary))

    skipped_path = save_skipped_assets(skipped, reports_dir=reports_dir, report_date=report_date)
    logger.info("skipped assets saved: %s (%d rows)", skipped_path, len(skipped))
    return csv_path, html_path


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="A股技术趋势监控（极简版）")
    p.add_argument("--start-date", default="20180101", help="起始日期 YYYYMMDD")
    p.add_argument("--end-date", default="", help="结束日期 YYYYMMDD（默认到最新）")
    p.add_argument("--adjust", default="qfq", choices=["", "qfq", "hfq"], help="复权方式")
    p.add_argument("--force", action="store_true", help="忽略缓存重新拉取")
    p.add_argument("--refresh-all", action="store_true", help="强制全部尝试在线刷新")
    p.add_argument("--refresh-needed", action="store_true", help="只更新需要更新的资产（默认行为）")
    p.add_argument("--refresh-missing", action="store_true", help="只更新无缓存资产")
    p.add_argument("--offline", action="store_true", help="完全不联网，只用缓存生成报告")
    p.add_argument("--only-symbols", default="", help="仅运行指定 symbols，逗号分隔；可用 CN:600519 或 CN_600519 指定市场")
    p.add_argument("--plot-last-n", type=int, default=180, help="图表展示最近 N 个交易日")
    p.add_argument("--log-level", default="INFO", help="日志级别")
    return p


def main() -> None:
    args = build_arg_parser().parse_args()
    run(args)


if __name__ == "__main__":
    main()
