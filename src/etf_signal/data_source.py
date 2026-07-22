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
# 2. ETF 历史日行情 — 主源: 东方财富 + 备用: 新浪
# ═══════════════════════════════════════════════════════════════════

def fetch_etf_hist(
    fund_code: str,
    start_date: str = "20200101",
    end_date: str | None = None,
    max_retries: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 10.0,
) -> pd.DataFrame:
    """获取单只 ETF 的历史日行情。

    主源：fund_etf_hist_em（东方财富）
    备用：fund_etf_hist_sina（新浪），主源异常时自动回退

    Returns:
        DataFrame: date, open, high, low, close, volume, amount
    """
    if not _ensure_akshare():
        return pd.DataFrame()
    if end_date is None:
        end_date = datetime.now().strftime("%Y%m%d")

    # 主源
    df = _fetch_hist_em(fund_code, start_date, end_date, max_retries, base_delay, max_delay)
    if not df.empty:
        logger.debug("hist_em ok: %s (%d rows)", fund_code, len(df))
        return df

    # 备用：新浪
    logger.info("hist_em failed for %s, falling back to hist_sina", fund_code)
    df = _fetch_hist_sina(fund_code, start_date, end_date)
    if not df.empty:
        logger.info("hist_sina fallback ok: %s (%d rows)", fund_code, len(df))
        return df

    logger.warning("all hist sources exhausted: %s", fund_code)
    return pd.DataFrame()


def _fetch_hist_em(
    fund_code: str, start_date: str, end_date: str,
    max_retries: int = 5, base_delay: float = 0.5, max_delay: float = 5.0,
) -> pd.DataFrame:
    """东方财富历史日行情。"""
    for attempt in range(1, max_retries + 1):
        try:
            df = ak.fund_etf_hist_em(
                symbol=fund_code,
                period="daily",
                start_date=start_date,
                end_date=end_date,
            )
            if not df.empty:
                return _normalise_etf_hist(df, fund_code)
        except Exception as e:
            logger.warning(
                "fund_etf_hist_em attempt %d/%d %s: %s",
                attempt, max_retries, fund_code, e,
            )
            if attempt < max_retries:
                time.sleep(min(base_delay * (2 ** (attempt - 1)), max_delay))
    return pd.DataFrame()


def _fetch_hist_sina(fund_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """新浪基金历史日行情（备用源）。

    新浪接口需要交易所前缀：
      sh = SSE（上交所，代码 51/56 开头）
      sz = SZSE（深交所，代码 15/16/18/58 开头）
    """
    prefix = "sh" if fund_code.startswith(("51", "56")) else "sz"
    symbol = f"{prefix}{fund_code}"

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


def _normalise_etf_hist(raw: pd.DataFrame, fund_code: str) -> pd.DataFrame:
    """标准化日行情列名。"""
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

    rename = {}
    for src, dst in [("开盘", "open"), ("最高", "high"), ("最低", "low"),
                     ("收盘", "close"), ("成交量", "volume"), ("成交额", "amount")]:
        c = _col(src)
        if c:
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
