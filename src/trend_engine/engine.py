"""
trend_engine — 趋势分析引擎（Layer 3 的内部依赖）

定位：纯引擎，不承担任何业务决策。
职责：给定一组资产，获取行情 → 计算技术指标 → 输出趋势评分。

本模块是原 stock_trend 底层能力（行情获取/缓存/多源回退/限流/指标/评分）
的保留与重组；业务层（CLI、报告、watchlist）已移除，由 Layer 3 selection 调用。
"""

from __future__ import annotations

import logging
import random
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from src.common.paths import (
    raw_dir as common_raw_dir,
    processed_dir as common_processed_dir,
    asset_state_path,
)
from .asset import Asset
from .data_provider import AKShareProvider
from . import fetch_data
from . import indicators
from . import scoring


def build_logger(level: str) -> logging.Logger:
    logger = logging.getLogger("trend_engine")
    if logger.handlers:
        return logger
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


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


# ── 缓存 / 状态 ─────────────────────────────────────────────────

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
    "market", "symbol", "last_success_date", "last_attempt_date",
    "last_status", "fail_count", "next_retry_date", "data_source",
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
        out = pd.concat([out, pd.DataFrame([{
            "market": asset.market, "symbol": asset.symbol,
            "last_success_date": "", "last_attempt_date": "",
            "last_status": "", "fail_count": "0", "next_retry_date": "", "data_source": "",
        }])], ignore_index=True)
        mask = (out["market"].astype(str) == asset.market) & (out["symbol"].astype(str) == asset.symbol)

    idx = out.index[mask][0]
    out.at[idx, "last_attempt_date"] = today.isoformat()
    if attempted_online and latest_data_date is None:
        fail_count = int(float(out.at[idx, "fail_count"] or 0)) + 1
        cooldown = min(7, 2 ** (min(fail_count, 3) - 1))
        out.at[idx, "fail_count"] = str(fail_count)
        out.at[idx, "last_status"] = "failed"
        out.at[idx, "next_retry_date"] = (today + timedelta(days=cooldown)).isoformat()
    else:
        out.at[idx, "last_success_date"] = (latest_data_date or today).isoformat()
        out.at[idx, "fail_count"] = "0"
        out.at[idx, "last_status"] = data_source
        out.at[idx, "next_retry_date"] = ""
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
            "RemoteDisconnected", "ConnectionError", "Timeout", "ReadTimeout",
            "ConnectTimeout", "ChunkedEncodingError", "SSLError", "JSONDecodeError",
        }
        if names & retryable_names:
            return True
        merged = " | ".join(t for t in texts if t).lower()
        if any(k in merged for k in [
            "fetch returned empty", "no value to decode", "remote end closed connection",
            "connection aborted", "remotedisconnected",
        ]):
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
            logger.info("fetch online success: %s (source=%s)", asset_key, source or "default")
            if merge_with_cache and cache_path.exists():
                df_cached = _load_cached_raw(cache_path)
                df = _merge_ohlcv(df_cached, df)
            df.to_csv(cache_path, index=False, encoding="utf-8")
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
    prev_score_by_symbol: dict[str, int],
    prev_available: bool,
    report_date: date,
) -> dict[str, object]:
    df_ind = indicators.add_indicators(df_raw)
    df_ind["date"] = pd.to_datetime(df_ind["date"], errors="coerce").astype("datetime64[ns]")
    if not bench.empty:
        df_ind = pd.merge_asof(
            df_ind.sort_values("date"),
            bench[["date", "bench_return_20d"]].sort_values("date"),
            on="date", direction="backward",
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
    action_value = calc_action(score_value, watch_level_value, rs20, volume_ratio, price_near_ma20, drawdown_from_high, change)

    return {
        "name": asset.name,
        "symbol": asset.symbol,
        "market": asset.market,
        "exchange": asset.exchange or "",
        "currency": asset.currency or "",
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


def sort_summary_df(df: pd.DataFrame) -> pd.DataFrame:
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


# ── 批量趋势计算 API ─────────────────────────────────────────────

def compute_trends(
    items: Sequence[Any],
    *,
    start_date: str = "20180101",
    end_date: str = "",
    adjust: str = "qfq",
    force: bool = False,
    refresh_all: bool = False,
    refresh_missing: bool = False,
    offline: bool = False,
    only_symbols: str = "",
    log_level: str = "INFO",
) -> pd.DataFrame:
    """对一组标的批量计算趋势评分。

    Args:
        items: 每项需具备 `asset`（Asset）、`note`、`update_policy` 属性
               （selection.universe.UniverseItem 满足该鸭子类型）

    Returns:
        排序后的趋势 DataFrame（含 name/symbol/score_trend/watch_level/action 等）
    """
    from datetime import datetime, timezone
    logger = build_logger(log_level)
    report_date = date.today()

    if only_symbols:
        raw_tokens = [t.strip() for t in only_symbols.split(",") if t.strip()]
        wanted: set[tuple[str | None, str]] = set()
        for tok in raw_tokens:
            if ":" in tok:
                mkt, sym = tok.split(":", 1)
                wanted.add((mkt.strip().upper() or None, sym.strip()))
            elif "_" in tok:
                mkt, sym = tok.split("_", 1)
                wanted.add((mkt.strip().upper() or None, sym.strip()))
            else:
                wanted.add((None, tok))
        items = [
            it for it in items
            if (it.asset.market, it.asset.symbol) in wanted or (None, it.asset.symbol) in wanted
        ]
        logger.info("filtered by --only-symbols: %d assets", len(items))

    if not items:
        logger.error("no assets to analyze")
        return pd.DataFrame()

    raw_dir = common_raw_dir()
    processed_dir = common_processed_dir()

    eff_end_date = end_date or "22220101"
    fetch_cfg = fetch_data.FetchConfig(start_date=start_date, end_date=eff_end_date, adjust=adjust)

    bench_raw = fetch_data.load_or_fetch_hs300(raw_dir=raw_dir, cfg=fetch_cfg, logger=logger, force=force)
    bench = bench_raw[["date", "close"]].rename(columns={"close": "bench_close"}).copy() if not bench_raw.empty else pd.DataFrame()
    if not bench.empty:
        bench["date"] = pd.to_datetime(bench["date"], errors="coerce").astype("datetime64[ns]")
        bench["bench_return_20d"] = bench["bench_close"].pct_change(20)

    provider = AKShareProvider(cfg=fetch_cfg, logger=logger)
    rows: list[dict[str, object]] = []
    state_path = asset_state_path()
    state_df = load_asset_state(state_path)

    for item in items:
        asset = item.asset
        note = str(getattr(item, "note", "") or "").strip()
        update_policy = str(getattr(item, "update_policy", "daily") or "").strip().lower()

        if not asset.symbol or asset.symbol.strip().upper() == "TBD":
            logger.warning("skip placeholder asset: (%s,%s)", asset.market, asset.symbol)
            continue

        raw_path = _raw_cache_path(raw_dir, asset)
        mode, inc_start, inc_end = decide_refresh_mode(
            asset=asset, update_policy=update_policy, state_df=state_df,
            raw_path=raw_path, today=report_date,
            refresh_all=refresh_all, refresh_missing=refresh_missing, offline=offline,
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
                    asset, provider, raw_dir=raw_dir, logger=logger,
                    start_date=inc_start, end_date=inc_end, merge_with_cache=True,
                )
            else:  # online_full
                df_raw, data_source = load_or_fetch_daily(
                    asset, provider, raw_dir=raw_dir, logger=logger,
                    start_date=start_date, end_date=eff_end_date,
                )

            try:
                latest_data_date = pd.to_datetime(df_raw["date"], errors="coerce").dropna().max()
                latest_date = latest_data_date.date() if latest_data_date is not None and pd.notna(latest_data_date) else None
            except Exception:
                latest_date = None
            state_df = update_asset_state(
                state_df=state_df, asset=asset, today=report_date,
                data_source=data_source, attempted_online=attempted_online,
                latest_data_date=latest_date,
            )

            row = analyze_asset(
                asset=asset, note=note, data_source=data_source, df_raw=df_raw,
                bench=bench, processed_dir=processed_dir,
                prev_score_by_symbol={}, prev_available=False, report_date=report_date,
            )
            rows.append(row)
        except Exception as e:
            logger.warning("analyze failed for %s: %s", asset.symbol, e)
            state_df = update_asset_state(
                state_df=state_df, asset=asset, today=report_date,
                data_source="failed", attempted_online=attempted_online, latest_data_date=None,
            )
            rows.append({
                "name": asset.name, "symbol": asset.symbol, "market": asset.market,
                "exchange": asset.exchange or "", "currency": asset.currency or "",
                "data_source": "failed", "data_freshness_days": None, "date": None,
                "close": None, "score": 0, "score_trend": 0, "label": "无数据",
                "watch_level": "", "action": "跳过", "change": "无变化",
                "relative_strength_20d": None, "risk_flags": "", "reason": str(e), "note": note,
            })

        if asset.market == "CN":
            time.sleep(random.uniform(2.0, 4.0))
        else:
            time.sleep(random.uniform(0.5, 2.0))

    save_asset_state(state_df, state_path)
    return sort_summary_df(pd.DataFrame(rows))
