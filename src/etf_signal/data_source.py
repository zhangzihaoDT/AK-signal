"""
AKShare ETF 数据源

职责：
  - 从 AKShare 获取可观察的沪深场内 ETF 数据
  - 多源策略：主源 + 校验源 + 备用源
  - 覆盖：代码、名称、价格、行情、成交额、换手率、规模、份额、IOPV、折溢价、跟踪指数、基金类型、上市时间
  - 不做过滤、归类或投资结论判断，只做数据搬运
  - 统一请求节流和重试

多源数据策略（2026-07 确认，引用 AKShare 官方文档）：

  | 用途             | 主源                    | 校验源                 | 备用源                  |
  |------------------|------------------------|-----------------------|------------------------|
  | ETF 清单与快照    | fund_etf_spot_em       | fund_etf_spot_ths     | —                      |
  | ETF 历史日行情    | fund_etf_hist_em       | —                     | fund_etf_hist_sina     |
  | ETF 补充信息      | fund_etf_info_sina     | —                     | —                      |

  主源负责日常采集，校验源用于交叉验证 U0 → U1 清单完整性，
  备用源在主源异常时自动回退。

分层说明：
  AKShare 是数据层，提供可观察的 ETF 数据底座。
  AKsignal（heat / signal 等模块）基于此数据计算热度、趋势、信号。
  "全市场"在此处的精确含义是：AKShare 接口可观察的沪深场内 ETF 市场。

P0-A 交付物
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime
from typing import Any

import pandas as pd

logger = logging.getLogger("etf_signal.data_source")

_AKSHARE_IMPORTED = False


def _ensure_akshare():
    global _AKSHARE_IMPORTED
    if not _AKSHARE_IMPORTED:
        try:
            global ak
            import akshare as ak
            _AKSHARE_IMPORTED = True
        except ImportError:
            logger.error("akshare not installed")
            return False
    return True


# ═══════════════════════════════════════════════════════════════════
# 错误分类 & 熔断器
# ═══════════════════════════════════════════════════════════════════


class SchemaChangedError(Exception):
    """EM 接口数据格式变化（字段缺失 / JSON 结构变化 / 解析失败），不可重试。"""


class TransientNetworkError(Exception):
    """网络临时故障（超时 / ConnectionReset / 5xx），可重试。"""


_em_circuit_open = False          # SchemaChangedError 熔断
_em_consecutive_schema_errors = 0
_EM_SCHEMA_CIRCUIT_LIMIT = 3

_em_rate_limit_hit = False         # rate-limit / connection-refused 熔断

_fetch_stats: dict[str, dict] = {}

EM_REQUEST_INTERVAL = 6            # 历史补缺请求间隔（秒）
EM_BACKFILL_LIMIT = 30             # 每轮最多补缺 ETF 数量


def reset_em_circuit_breakers():
    global _em_circuit_open, _em_consecutive_schema_errors, _em_rate_limit_hit
    _em_circuit_open = False
    _em_consecutive_schema_errors = 0
    _em_rate_limit_hit = False
    logger.debug("EM circuit breakers reset")


def is_rate_limit_error(e: Exception) -> bool:
    """判断是否为服务端限流/连接拒绝。"""
    msg = str(e).lower()
    if any(k in msg for k in (
        "remote disconnected", "connection aborted", "connection reset",
        "connection refused", "eof", "broken pipe",
    )):
        return True
    # 空响应也可能表示限流
    if isinstance(e, ConnectionError):
        return True
    return False


def classify_em_error(e: Exception) -> Exception:
    """将原始异常分类为 SchemaChangedError 或 TransientNetworkError。"""
    msg = str(e).lower()
    # 字段缺失 / 列名变化 → 格式变更，不可重试
    if any(k in msg for k in ("not in index", "not in columns", "keyerror", "column")):
        return SchemaChangedError(str(e))
    # 网络层故障
    if any(k in msg for k in (
        "connection aborted", "connection reset", "remote disconnected",
        "timeout", "time out", "eof", "broken pipe",
    )):
        return TransientNetworkError(str(e))
    if isinstance(e, (ConnectionError, TimeoutError)):
        return TransientNetworkError(str(e))
    status = _extract_http_status(str(e))
    if status and status >= 500:
        return TransientNetworkError(str(e))
    return SchemaChangedError(str(e))


def _extract_http_status(msg: str) -> int | None:
    """从错误消息中提取 HTTP 状态码。"""
    import re
    m = re.search(r'\b(5\d{2})\b', msg)
    return int(m.group(1)) if m else None


def is_kcb(code: str) -> bool:
    """科创板 ETF：588xxx / 589xxx"""
    return code.startswith(("588", "589"))


def sina_exchange(code: str) -> str:
    """新浪接口交易所前缀映射。

    上交所（sh）：代码 5/6 开头——宽基 510/511/512/513/515/516/517/518、
      行业/主题 512/513/515/516/561/562/563、新代码 520/526/530/560、
      科创板 588/589。
    深交所（sz）：代码 1 开头——159/16x/18x。

    通用规则（对未知新前缀同样安全）：5/6 → sh，其余 → sz。
    映射矩阵由 tests/etf_signal/test_routing.py 锁定，改坏立即失败。
    """
    if code.startswith(("5", "6")):
        return "sh"
    return "sz"


def sina_symbol(code: str) -> str:
    """新浪接口完整 symbol（如 sh588220）。"""
    return f"{sina_exchange(code)}{code}"


def is_sina_viable(code: str) -> bool:
    """新浪 ETF 历史接口是否可能覆盖该代码。

    实测新浪已覆盖科创板（588/589）及较新的 52/53/56 代码，不再按代码段
    一刀切排除；未覆盖的代码由 _fetch_hist_sina 优雅返回空 DataFrame。
    """
    return True


def clear_fetch_stats():
    global _fetch_stats
    _fetch_stats = {}


def get_fetch_stats() -> dict[str, dict]:
    return dict(_fetch_stats)


def log_fetch_summary():
    if not _fetch_stats:
        logger.info("fetch stats: no records")
        return
    by_source: dict[str, int] = {}
    by_error: dict[str, int] = {}
    no_source: list[str] = []
    total_ms = 0
    n_timed = 0
    for code, info in _fetch_stats.items():
        src = info.get("source", "unknown")
        by_source[src] = by_source.get(src, 0) + 1
        if err := info.get("primary_error_type"):
            by_error[err] = by_error.get(err, 0) + 1
        if src == "none":
            no_source.append(code)
        if (ms := info.get("elapsed_ms")) is not None:
            total_ms += ms
            n_timed += 1
    logger.info("fetch stats — source breakdown: %s", by_source)
    if by_error:
        logger.info("fetch stats — error breakdown: %s", by_error)
    if n_timed:
        logger.info("fetch stats — elapsed: total=%.1fs avg=%.0fms/code",
                    total_ms / 1000.0, total_ms / max(n_timed, 1))
    if no_source:
        logger.info("fetch stats — no_source (%d): %s", len(no_source), no_source[:10])


# ═══════════════════════════════════════════════════════════════════
# 1. ETF Master — 主源: 东方财富
# ═══════════════════════════════════════════════════════════════════

def fetch_etf_master() -> pd.DataFrame:
    """从东方财富获取 ETF Master（主源）。

    AKShare 接口：fund_etf_spot_em

    官方描述：单次返回东方财富 ETF 行情页面的全部数据，
    包含代码、名称、价格、IOPV、折溢价、成交额、份额和市值等字段。

    Returns:
        标准化的 etf_master DataFrame
    """
    if not _ensure_akshare():
        return pd.DataFrame()

    try:
        spot = ak.fund_etf_spot_em()
        if spot.empty:
            logger.warning("fund_etf_spot_em returned empty")
            return pd.DataFrame()
        logger.info("fund_etf_spot_em: %d ETFs", len(spot))
    except Exception as e:
        logger.error("fund_etf_spot_em failed: %s", e)
        return pd.DataFrame()

    return _normalise_spot_master(spot, source="em")


# ═══════════════════════════════════════════════════════════════════
# 1b. ETF Master — 校验源: 同花顺
# ═══════════════════════════════════════════════════════════════════

def fetch_etf_master_ths() -> pd.DataFrame:
    """从同花顺获取 ETF 清单（校验源）。

    AKShare 接口：fund_etf_spot_ths

    用于与主源交叉验证，确保 U0 → U1 不遗漏有效标的
    或混入主源中的异常记录。

    Returns:
        标准化的 etf_master DataFrame（仅含基础标识字段）
    """
    if not _ensure_akshare():
        return pd.DataFrame()
    try:
        spot = ak.fund_etf_spot_ths()
        if spot.empty:
            logger.warning("fund_etf_spot_ths returned empty")
            return pd.DataFrame()
        logger.info("fund_etf_spot_ths: %d ETFs", len(spot))
    except Exception as e:
        logger.error("fund_etf_spot_ths failed: %s", e)
        return pd.DataFrame()

    cols_lower = {c.lower(): c for c in spot.columns}

    def _col(*keys: str) -> str | None:
        for k in keys:
            if k in cols_lower:
                return cols_lower[k]
            for c in cols_lower:
                if k in c:
                    return cols_lower[c]
        return None

    code_col = _col("代码")
    name_col = _col("名称")
    if not code_col:
        logger.warning("no code column in THS data")
        return pd.DataFrame()

    rows = []
    for _, row in spot.iterrows():
        fund_code = str(row.get(code_col, ""))
        fund_name = str(row.get(name_col, "")) if name_col else ""
        if not fund_code:
            continue
        exchange = "SSE" if fund_code.startswith(("51", "56")) else "SZSE"
        rows.append({
            "fund_code": fund_code,
            "fund_name": fund_name,
            "exchange": exchange,
            "source_ths": "ths",
        })

    return pd.DataFrame(rows)


def cross_validate_master(
    em_df: pd.DataFrame,
    ths_df: pd.DataFrame,
) -> tuple[pd.DataFrame, list[str], list[str]]:
    """交叉验证东方财富与同花顺的 ETF 清单。

    确保 U0 到 U1 过程中：
    - 两个源都存在的标的标记为 `cross_validated=True`
    - 仅在单个源存在的标的记录差异，供人工核查

    Args:
        em_df: 东方财富 normalised master
        ths_df: 同花顺 normalised master

    Returns:
        (merged_master, only_in_em, only_in_ths)
    """
    if em_df.empty:
        return em_df, [], ths_df["fund_code"].tolist() if not ths_df.empty else []
    if ths_df.empty:
        em_df["cross_validated"] = False
        return em_df, em_df["fund_code"].tolist(), []

    em_codes = set(em_df["fund_code"])
    ths_codes = set(ths_df["fund_code"])

    common = em_codes & ths_codes
    only_em = em_codes - ths_codes
    only_ths = ths_codes - em_codes

    result = em_df.copy()
    result["cross_validated"] = result["fund_code"].isin(common)
    result["source"] = "em"
    result.loc[result["cross_validated"], "source"] = "em+ths"

    if only_em:
        logger.warning("only in EM (%d): %s", len(only_em), sorted(only_em)[:10])
    if only_ths:
        logger.info("only in THS (%d): %s — may need manual check", len(only_ths), sorted(only_ths)[:10])

    return result, sorted(only_em), sorted(only_ths)


def _normalise_spot_master(spot: pd.DataFrame, source: str = "em") -> pd.DataFrame:
    """将 AKShare spot 数据标准化为统一的 Master 格式。

    智能列名匹配，适配 AKShare 版本变化。
    """
    cols_lower = {c.lower(): c for c in spot.columns}

    def _col(*keys: str) -> str | None:
        for k in keys:
            if k in cols_lower:
                return cols_lower[k]
            for candidate in cols_lower:
                if k in candidate:
                    return cols_lower[candidate]
        return None

    name_col = _col("名称")
    code_col = _col("代码")
    price_col = _col("最新价")
    change_pct_col = _col("涨跌幅")
    amount_col = _col("成交额")
    turnover_col = _col("换手率")
    premium_col = _col("折溢价率")
    shares_col = _col("基金份额")
    scale_col = _col("基金规模")
    volume_col = _col("成交量")

    rows: list[dict[str, Any]] = []
    for _, row in spot.iterrows():
        fund_code = str(row.get(code_col, "")) if code_col else ""
        fund_name = str(row.get(name_col, "")) if name_col else ""
        if not fund_code or not fund_name:
            continue

        exchange = "SSE" if fund_code.startswith(("51", "56")) else "SZSE"

        rows.append({
            "fund_code": fund_code,
            "fund_name": fund_name,
            "exchange": exchange,
            "price": _safe_float(row, price_col),
            "change_pct": _safe_float(row, change_pct_col),
            "volume": _safe_float(row, volume_col),
            "amount": _safe_float(row, amount_col),
            "turnover": _safe_float(row, turnover_col),
            "premium_discount": _safe_float(row, premium_col),
            "shares": _safe_float(row, shares_col),
            "fund_size": _safe_float(row, scale_col) or (
                _safe_float(row, shares_col) * _safe_float(row, price_col)
                if _safe_float(row, price_col) is not None and _safe_float(row, shares_col) is not None else None
            ),
            "asset_bucket": "",
            "exposure_type": "",
            "exposure_name": "",
            "market_scope": "",
            "tracking_index": "",
            "tracking_index_code": "",
            "listing_date": None,
            "is_qdii": False,
            "is_active": True,
            "guojin_tradable": False,
        })

    return pd.DataFrame(rows)


def _safe_float(row: pd.Series, col: str | None) -> float | None:
    if col is None or col not in row:
        return None
    try:
        v = row[col]
        if pd.isna(v):
            return None
        return float(v)
    except (ValueError, TypeError):
        return None


# ═══════════════════════════════════════════════════════════════════
# 1c. ETF 全市场快照 → 当日 OHLCV
# ═══════════════════════════════════════════════════════════════════

def fetch_ohlcv_from_spot() -> pd.DataFrame:
    """从 EM 全市场快照批量获取当日 OHLCV。

    fund_etf_spot_em() 一次返回全市场 ETF 的实时行情，
    包含开盘价、最高价、最低价、最新价、成交量、成交额、数据日期。

    收盘后调用：1 次请求 ≈ 全市场 1500+ 只 ETF 的当日日线。

    Returns:
        DataFrame: date, open, high, low, close, volume, amount, fund_code
    """
    if not _ensure_akshare():
        return pd.DataFrame()

    try:
        spot = ak.fund_etf_spot_em()
    except Exception as e:
        logger.error("fund_etf_spot_em failed: %s", e)
        return pd.DataFrame()

    if spot.empty:
        logger.warning("fund_etf_spot_em returned empty")
        return pd.DataFrame()

    cols_lower = {c.lower(): c for c in spot.columns}
    def _col(*keys: str) -> str | None:
        for k in keys:
            if k in cols_lower:
                return cols_lower[k]
        return None

    code_c = _col("代码")
    open_c = _col("开盘价")
    high_c = _col("最高价")
    low_c = _col("最低价")
    close_c = _col("最新价")
    volume_c = _col("成交量")
    amount_c = _col("成交额")
    date_c = _col("数据日期")

    required = [code_c, open_c, high_c, low_c, close_c, volume_c, amount_c]
    if not all(required):
        missing = [n for n, c in zip(["代码","开盘价","最高价","最低价","最新价","成交量","成交额"], required) if not c]
        logger.warning("spot missing columns: %s", missing)
        return pd.DataFrame()

    rows = []
    for _, row in spot.iterrows():
        code = str(row.get(code_c, ""))
        if not code:
            continue
        d = row.get(date_c)
        if d is None or pd.isna(d):
            continue
        rows.append({
            "date": pd.Timestamp(d),
            "open": _safe_float(row, open_c),
            "high": _safe_float(row, high_c),
            "low": _safe_float(row, low_c),
            "close": _safe_float(row, close_c),
            "volume": _safe_float(row, volume_c),
            "amount": _safe_float(row, amount_c),
            "fund_code": code,
        })

    result = pd.DataFrame(rows)
    logger.info("spot OHLCV: %d ETFs, date=%s", len(result),
                result["date"].iloc[0].strftime("%Y-%m-%d") if not result.empty else "?")
    return result


# ═══════════════════════════════════════════════════════════════════
# 2. ETF 历史日行情 — 主源: 东方财富 + 备用: 新浪
# ═══════════════════════════════════════════════════════════════════

def fetch_etf_hist(
    fund_code: str,
    start_date: str = "20200101",
    end_date: str | None = None,
) -> pd.DataFrame:
    """获取单只 ETF 的历史日行情。

    主源：fund_etf_hist_em（东方财富）
    备用：fund_etf_hist_sina（新浪），主源异常时自动回退

    熔断策略：
      - SchemaChangedError → 不可重试，立即回退或标记
      - TransientNetworkError → 最多重试 1 次后回退
      - 连续 3 次 SchemaChangedError → 本轮跳过 EM

    科创板（588xxx）：新浪不覆盖，EM 失败后直接标记待补。

    Returns:
        DataFrame: date, open, high, low, close, volume, amount
    """
    global _em_circuit_open, _em_consecutive_schema_errors, _em_rate_limit_hit

    if not _ensure_akshare():
        return pd.DataFrame()
    if end_date is None:
        end_date = datetime.now().strftime("%Y%m%d")

    _t_start = time.perf_counter()
    info: dict[str, Any] = {"code": fund_code}

    def _stamp_elapsed(info: dict[str, Any]) -> None:
        info["elapsed_ms"] = round((time.perf_counter() - _t_start) * 1000)

    if is_kcb(fund_code) and not is_sina_viable(fund_code):
        info["fallback_reason"] = "科创板 — 新浪不覆盖"
        info["primary_error_type"] = "no_source_for_kcb"

    # ── 主源：EM ──────────────────────────────────────────────
    df_em = pd.DataFrame()
    em_attempted = False
    em_skipped_reason = None

    if _em_rate_limit_hit:
        em_skipped_reason = "em_rate_limit_circuit"
    elif _em_circuit_open:
        em_skipped_reason = "em_schema_circuit"

    if em_skipped_reason:
        info["primary_error_type"] = em_skipped_reason
        info["fallback_reason"] = f"EM skipped: {em_skipped_reason}"
    else:
        em_attempted = True
        try:
            df_em = _fetch_hist_em(fund_code, start_date, end_date)
        except SchemaChangedError as e:
            _em_consecutive_schema_errors += 1
            logger.info("em schema changed for %s (consecutive=%d): %s", fund_code, _em_consecutive_schema_errors, e)
            if _em_consecutive_schema_errors >= _EM_SCHEMA_CIRCUIT_LIMIT:
                _em_circuit_open = True
                logger.warning("EM schema circuit breaker OPEN")
            info["primary_error_type"] = "SchemaChangedError"
            info["fallback_reason"] = str(e)
        except TransientNetworkError as e:
            info["primary_error_type"] = "TransientNetworkError"
            info["fallback_reason"] = str(e)
            if is_rate_limit_error(e):
                _em_rate_limit_hit = True
                logger.warning("EM rate-limit circuit breaker OPEN — skipping EM for remaining ETFs this session")
        except Exception as e:
            _em_consecutive_schema_errors += 1
            if _em_consecutive_schema_errors >= _EM_SCHEMA_CIRCUIT_LIMIT:
                _em_circuit_open = True
                logger.warning("EM schema circuit breaker OPEN")
            info["primary_error_type"] = type(e).__name__
            info["fallback_reason"] = str(e)

    if not df_em.empty:
        info["source"] = "em"
        _stamp_elapsed(info)
        _fetch_stats[fund_code] = info
        logger.debug("hist_em ok: %s (%d rows)", fund_code, len(df_em))
        return df_em

    if em_attempted:
        _em_consecutive_schema_errors = 0  # 有数据或非 schema 错误时重置

    # ── 备用：新浪 ─────────────────────────────────────────────
    if is_sina_viable(fund_code):
        df = _fetch_hist_sina(fund_code, start_date, end_date)
        if not df.empty:
            info["source"] = "sina"
            info["fallback_reason"] = info.get("fallback_reason", "em_failed")
            _stamp_elapsed(info)
            _fetch_stats[fund_code] = info
            logger.info("hist_sina fallback ok: %s (%d rows)", fund_code, len(df))
            return df
        if "primary_error_type" not in info:
            info["primary_error_type"] = "sina_failed"
        info["fallback_reason"] = "sina_failed"
    else:
        if "primary_error_type" not in info:
            info["primary_error_type"] = "no_source_for_kcb"
        info["fallback_reason"] = "科创板无可用源"

    info["source"] = "none"
    _stamp_elapsed(info)
    _fetch_stats[fund_code] = info
    logger.warning("all hist sources exhausted: %s", fund_code)
    return pd.DataFrame()


def _fetch_hist_em(
    fund_code: str, start_date: str, end_date: str,
    max_retries: int = 1, base_delay: float = 1.0, max_delay: float = 3.0,
) -> pd.DataFrame:
    """东方财富历史日行情。

    - SchemaChangedError 立即抛出（不可重试）。
    - TransientNetworkError 最多重试 1 次后抛出。
    """
    for attempt in range(1, max_retries + 2):
        try:
            df = ak.fund_etf_hist_em(
                symbol=fund_code,
                period="daily",
                start_date=start_date,
                end_date=end_date,
            )
            if df.empty:
                raise SchemaChangedError(f"fund_etf_hist_em returned empty DataFrame for {fund_code}")
            return _normalise_etf_hist(df, fund_code)
        except (SchemaChangedError, TransientNetworkError):
            raise
        except Exception as e:
            classified = classify_em_error(e)
            if isinstance(classified, SchemaChangedError):
                raise classified from e
            logger.warning(
                "fund_etf_hist_em attempt %d/2 %s: %s",
                attempt, fund_code, classified,
            )
            if attempt <= max_retries:
                time.sleep(min(base_delay * (2 ** (attempt - 1)), max_delay))
            else:
                raise classified from e
    return pd.DataFrame()


def _fetch_hist_sina(fund_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """新浪基金历史日行情（备用源）。

    新浪接口需要交易所前缀：sh（上交所）/ sz（深交所），
    由 sina_exchange / sina_symbol 统一映射（见 tests/etf_signal/test_routing.py）。
    """
    symbol = sina_symbol(fund_code)

    try:
        df = ak.fund_etf_hist_sina(symbol=symbol)
        if df.empty:
            logger.debug("fund_etf_hist_sina empty for %s", symbol)
            return pd.DataFrame()
        df = _normalise_etf_hist(df, fund_code)
        if "date" in df.columns:
            df = df[
                (df["date"] >= pd.Timestamp(start_date))
                & (df["date"] <= pd.Timestamp(end_date))
            ]
        return df
    except Exception as e:
        logger.debug("fund_etf_hist_sina failed for %s (%s): %s", symbol, fund_code, e)
        return pd.DataFrame()


_F10_NAV_URL = "https://api.fund.eastmoney.com/f10/lsjz"


def fetch_nav_latest(code: str, timeout: float = 10.0) -> tuple[date | None, float | None]:
    """查询基金最新净值日期（东财 f10 lsjz，直接 HTTP）。

    K 线接口（push2his）被限流时，此基金净值接口通常仍可用，
    用于判断缺口分类：
      - TERMINATED：净值停在很久以前（基金终止 / 净值停发）
      - SOURCE_STALE：净值已到 target，但日 K 源没跟上

    Returns:
        (最新净值日期, 最新单位净值)；失败返回 (None, None)。
    """
    try:
        import requests

        resp = requests.get(
            _F10_NAV_URL,
            params={"fundCode": code, "pageIndex": 1, "pageSize": 5},
            headers={"Referer": "https://fundf10.eastmoney.com/"},
            timeout=timeout,
        )
        resp.raise_for_status()
        rows = (resp.json().get("Data") or {}).get("LSJZList") or []
        if not rows:
            return None, None
        first = rows[0]
        return date.fromisoformat(first["FSRQ"]), float(first["DWJZ"])
    except Exception:
        return None, None


def _normalise_etf_hist(raw: pd.DataFrame, fund_code: str) -> pd.DataFrame:
    """标准化日行情列名。

    兼容两种输入格式：
      - 东方财富（中文列名）：日期、开盘、收盘、最高、最低、成交量、成交额
      - 新浪（英文列名）：date、open、high、low、close、volume、amount
    """
    cols_lower = {c.lower(): c for c in raw.columns}

    def _col(*keys: str) -> str | None:
        for k in keys:
            if k in cols_lower:
                return cols_lower[k]
            for candidate in cols_lower:
                if k in candidate:
                    return cols_lower[candidate]
        return None

    date_col = _col("日期", "date", "trade_date")
    if not date_col:
        logger.warning("no date column in ETF hist: %s", fund_code)
        return pd.DataFrame()

    rename = {date_col: "date"}
    for src, dst in [("开盘", "open"), ("最高", "high"), ("最低", "low"),
                     ("收盘", "close"), ("成交量", "volume"), ("成交额", "amount")]:
        c = _col(src)
        if c and c != date_col:
            rename[c] = dst

    df = raw.rename(columns=rename)
    cols = ["date"] + [v for v in ["open", "high", "low", "close", "volume", "amount"] if v in df.columns]
    df = df[cols]
    df["date"] = pd.to_datetime(df["date"])
    df["fund_code"] = fund_code
    return df.sort_values("date").reset_index(drop=True)


# ═══════════════════════════════════════════════════════════════════
# 3. ETF 补充信息（跟踪指数、基金类型、上市日期）
# ═══════════════════════════════════════════════════════════════════

def fetch_etf_info(fund_code: str) -> dict[str, Any]:
    """获取单只 ETF 的补充信息（新浪）。

    AKShare 接口：fund_etf_info_sina

    Returns:
        {tracking_index, tracking_index_code, fund_type, listing_date, is_qdii}
    """
    if not _ensure_akshare():
        return {}
    try:
        info = ak.fund_etf_info_sina(fund_code)
        if info is None or info.empty:
            return {}
        return _parse_info(info, fund_code)
    except Exception as e:
        logger.debug("fund_etf_info_sina failed for %s: %s", fund_code, e)
        return {}


def _parse_info(info: pd.DataFrame, fund_code: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "fund_code": fund_code,
        "tracking_index": "",
        "tracking_index_code": "",
        "fund_type": "",
        "listing_date": None,
        "is_qdii": False,
    }
    for _, row in info.iterrows():
        item = str(row.get("item", "")).strip()
        value = str(row.get("value", "")).strip()

        if "跟踪标的" in item:
            parts = value.rsplit("（", 1)
            result["tracking_index"] = parts[0].strip()
            if len(parts) > 1:
                result["tracking_index_code"] = parts[1].rstrip("）").strip()
        elif "基金类型" in item:
            result["fund_type"] = value
        elif "上市日期" in item:
            try:
                result["listing_date"] = pd.Timestamp(value).date()
            except Exception:
                pass
        elif "QDII" in item and "是" in value:
            result["is_qdii"] = True
    return result


# ═══════════════════════════════════════════════════════════════════
# 4. 数据新鲜度探针
# ═══════════════════════════════════════════════════════════════════

def freshness_probe(probe_code: str = "510050") -> date | None:
    """对上游执行一次真实请求，探测源端最新交易日期。"""
    try:
        df = fetch_etf_hist(probe_code, start_date="20260101")
        if not df.empty and "date" in df.columns:
            return df["date"].max().date()
        return None
    except Exception as e:
        logger.warning("freshness probe failed for %s: %s", probe_code, e)
        return None
