"""
Legulegu vs 东方财富 涨跌幅对比验证

对照两个数据源的近 N 日涨跌幅，判断 legulegu 是否可以替代东方财富
作为 drilldown 的主力行情源（至少对标准窗口而言）。
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

import akshare as ak
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.sw_industry_rps.constituents import fetch_constituent_list
from src.sw_industry_rps import storage
from src.common.paths import sw_industry_raw_dir


logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(message)s")
logger = logging.getLogger("verify")


def em_return(symbol: str, start_date: str, end_date: str, window: int) -> float | None:
    for attempt in range(3):
        try:
            time.sleep(0.5)
            df = ak.stock_zh_a_hist(symbol=symbol, period="daily",
                                     start_date=start_date, end_date=end_date,
                                     adjust="qfq")
            if df is None or df.empty:
                return None
            df["日期"] = pd.to_datetime(df["日期"])
            df = df.sort_values("日期")
            if len(df) < window + 1:
                return None
            ret = (df["收盘"].iloc[-1] / df["收盘"].iloc[-(window + 1)] - 1) * 100
            return round(float(ret), 2)
        except Exception as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
            else:
                logger.debug("  EM fail %s: %s", symbol, e)
    return None


def verify_industry(
    industry_code: str,
    industry_name: str,
    target_date: str,
    windows: list[int],
    max_stocks: int = 30,
) -> list[dict]:
    end_dt = pd.Timestamp(target_date)
    results: list[dict] = []

    # 从 legulegu 获取成分股 + 涨跌幅
    const = fetch_constituent_list(industry_code)
    if const.empty:
        logger.warning("  no constituent data for %s", industry_code)
        return results

    const = const.head(max_stocks)
    logger.info("%s (%s): %d stocks", industry_name, industry_code, len(const))

    date_cols = {w: f"近{w}日涨幅" for w in windows}
    available_windows = [w for w in windows if date_cols[w] in const.columns]
    if not available_windows:
        logger.warning("  no matching return columns in legulegu data")
        logger.info("  available columns: %s", [c for c in const.columns if "涨幅" in c])
        return results

    for _, row in const.iterrows():
        raw_code = row["股票代码"]
        symbol = raw_code.replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
        name = row["股票简称"]

        for w in available_windows:
            col = date_cols[w]
            leg_val = row.get(col)
            if leg_val is None or (isinstance(leg_val, float) and pd.isna(leg_val)):
                continue
            leg_val = float(leg_val)

            # 从东方财富获取同期涨跌幅
            start_dt = end_dt - pd.Timedelta(days=(w + 3) * 2)
            em_val = em_return(symbol, start_dt.strftime("%Y%m%d"), target_date, w)

            gap = (em_val - leg_val) if em_val is not None else None

            results.append({
                "stock_code": raw_code,
                "stock_name": name,
                "window": w,
                "legulegu_pct": leg_val,
                "em_pct": em_val,
                "gap_pct": round(gap, 2) if gap is not None else None,
            })

        if len(results) >= max_stocks * len(available_windows):
            break

    return results


def main():
    target = "20260716"
    windows = [1, 5]

    # 选取 2 个行业：影视院线（19 stocks）、乘用车（8 stocks）→ 约 27 stocks
    industries = [
        ("801766.SI", "影视院线"),
        ("801095.SI", "乘用车"),
    ]

    all_rows: list[dict] = []
    for code, name in industries:
        rows = verify_industry(code, name, target, windows, max_stocks=30)
        all_rows.extend(rows)
        time.sleep(3)

    if not all_rows:
        logger.warning("no data collected")
        return

    df = pd.DataFrame(all_rows)

    # 按窗口分组汇总
    for w in windows:
        sub = df[df["window"] == w].copy()
        valid = sub.dropna(subset=["gap_pct"])
        if valid.empty:
            logger.info("\n窗口=%dd: 无有效对比数据", w)
            continue

        gaps = valid["gap_pct"]
        abs_gaps = gaps.abs()

        logger.info("\n%s", "=" * 60)
        logger.info("窗口=%dd 对比结果 (%d 只股票)", w, len(valid))
        logger.info("%s", "=" * 60)
        logger.info("  误差中位数:        %.4f pp", gaps.median())
        logger.info("  误差均值:          %.4f pp", gaps.mean())
        logger.info("  绝对误差中位数:    %.4f pp", abs_gaps.median())
        logger.info("  绝对误差均值:      %.4f pp", abs_gaps.mean())
        logger.info("  绝对误差 P90:      %.4f pp", abs_gaps.quantile(0.90))
        logger.info("  误差 <= 0.1pp:     %d / %d (%.1f%%)",
                     (abs_gaps <= 0.1).sum(), len(abs_gaps),
                     (abs_gaps <= 0.1).mean() * 100)
        logger.info("  误差 <= 0.2pp:     %d / %d (%.1f%%)",
                     (abs_gaps <= 0.2).sum(), len(abs_gaps),
                     (abs_gaps <= 0.2).mean() * 100)
        logger.info("  误差 <= 0.5pp:     %d / %d (%.1f%%)",
                     (abs_gaps <= 0.5).sum(), len(abs_gaps),
                     (abs_gaps <= 0.5).mean() * 100)

        # 异常值明细
        outliers = valid[abs_gaps > 0.5]
        if not outliers.empty:
            logger.info("\n  异常值 (>0.5pp):")
            for _, r in outliers.iterrows():
                logger.info("    %s %s: leg=%+.2f%% em=%+.2f%% gap=%+.2fpp",
                            r["stock_code"], r["stock_name"],
                            r["legulegu_pct"], r["em_pct"], r["gap_pct"])

    # 保存原始数据
    out_path = Path("outputs/sw_industry_rps") / f"legulegu_em_verify_{target}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    logger.info("\n原始数据已保存: %s", out_path)


if __name__ == "__main__":
    main()
