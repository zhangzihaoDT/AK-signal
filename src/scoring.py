from __future__ import annotations

import pandas as pd


def trend_bucket(score: int) -> str:
    if score >= 70:
        return "strong"
    if score >= 30:
        return "observe"
    return "weak"


def build_risk_flags(row: pd.Series) -> list[str]:
    flags: list[str] = []

    close = row.get("close")
    ma20 = row.get("ma20")
    ma60 = row.get("ma60")
    ma120 = row.get("ma120")
    macd_hist = row.get("macd_hist")
    rsi14 = row.get("rsi14")
    rs20 = row.get("relative_strength_20d")

    if pd.notna(close):
        if pd.notna(ma20) and float(close) < float(ma20):
            flags.append("跌破MA20")
        if pd.notna(ma60) and float(close) < float(ma60):
            flags.append("跌破MA60")
        if pd.notna(ma120) and float(close) < float(ma120):
            flags.append("跌破MA120")

    if pd.notna(macd_hist) and float(macd_hist) < 0:
        flags.append("MACD转弱")

    if pd.notna(rsi14):
        r = float(rsi14)
        if r > 70:
            flags.append("RSI偏热")
        if r < 40:
            flags.append("RSI偏弱")

    if pd.notna(rs20) and float(rs20) < 0:
        flags.append("RS为负")

    return flags


def risk_flags_text(row: pd.Series) -> str:
    return "，".join(build_risk_flags(row))


def build_reason(row: pd.Series) -> str:
    close = row.get("close")
    ma20 = row.get("ma20")
    ma60 = row.get("ma60")
    ma120 = row.get("ma120")
    ma20_slope = row.get("ma20_slope")
    ma60_slope = row.get("ma60_slope")
    macd_hist = row.get("macd_hist")
    rsi14 = row.get("rsi14")
    volume = row.get("volume")
    vol_ma20 = row.get("vol_ma20")
    rs20 = row.get("relative_strength_20d")

    trend_parts: list[str] = []
    if pd.notna(close):
        above = []
        for ma_col, ma_val, tag in [("MA20", ma20, "MA20"), ("MA60", ma60, "MA60"), ("MA120", ma120, "MA120")]:
            if pd.notna(ma_val) and float(close) >= float(ma_val):
                above.append(tag)
        if above:
            trend_parts.append("收盘在" + "/".join(above) + "之上")
        else:
            below = []
            for ma_val, tag in [(ma20, "MA20"), (ma60, "MA60"), (ma120, "MA120")]:
                if pd.notna(ma_val) and float(close) < float(ma_val):
                    below.append(tag)
            if below:
                trend_parts.append("收盘在" + "/".join(below) + "之下")

    slope_parts = []
    if pd.notna(ma20_slope) and float(ma20_slope) > 0:
        slope_parts.append("MA20上行")
    if pd.notna(ma60_slope) and float(ma60_slope) > 0:
        slope_parts.append("MA60上行")
    if slope_parts:
        trend_parts.append("；".join(slope_parts))

    if pd.notna(macd_hist):
        trend_parts.append("MACD柱体为正" if float(macd_hist) > 0 else "MACD柱体为负")

    trend_text = "结构：" + ("；".join(trend_parts) if trend_parts else "信息不足")

    vol_text = "量能：信息不足"
    if pd.notna(volume) and pd.notna(vol_ma20) and float(vol_ma20) != 0:
        ratio = float(volume) / float(vol_ma20)
        if ratio >= 1.3:
            vol_state = "放量"
        elif ratio <= 0.7:
            vol_state = "缩量"
        else:
            vol_state = "平稳"
        vol_text = f"量能：{vol_state}({ratio:.2f}x)"

    rs_text = "相对强度：信息不足"
    if pd.notna(rs20):
        rs_pct = float(rs20) * 100
        sign = "+" if rs_pct >= 0 else ""
        rs_text = f"相对强度：近20日相对沪深300 {sign}{rs_pct:.2f}%"

    risk_points: list[str] = []
    if pd.notna(close) and pd.notna(ma20) and float(close) < float(ma20):
        risk_points.append("跌破MA20")
    if pd.notna(close) and pd.notna(ma60) and float(close) < float(ma60):
        risk_points.append("跌破MA60")
    if pd.notna(macd_hist) and float(macd_hist) < 0:
        risk_points.append("MACD转弱")
    if pd.notna(rsi14) and float(rsi14) > 70:
        risk_points.append("RSI偏热")
    if pd.notna(rsi14) and float(rsi14) < 40:
        risk_points.append("RSI偏弱")
    if pd.notna(rs20) and float(rs20) < 0:
        risk_points.append("相对强度为负")
    risk_text = "风险点：" + ("，".join(risk_points) if risk_points else "未见明显")

    return "；".join([trend_text, vol_text, rs_text, risk_text])


def score_latest_row(df: pd.DataFrame) -> tuple[int, dict[str, float | int | str]]:
    if df.empty:
        return 0, {"reason": "empty"}

    row = df.iloc[-1]
    score = 0
    details: dict[str, float | int | str] = {}

    close = float(row["close"])
    details["close"] = close

    for ma_col, weight in [("ma20", 15), ("ma60", 15), ("ma120", 10)]:
        v = row.get(ma_col)
        if pd.notna(v) and close >= float(v):
            score += weight
        details[f"above_{ma_col}"] = int(pd.notna(v) and close >= float(v))

    for slope_col, weight in [("ma20_slope", 10), ("ma60_slope", 10)]:
        v = row.get(slope_col)
        if pd.notna(v) and float(v) > 0:
            score += weight
        details[f"{slope_col}_pos"] = int(pd.notna(v) and float(v) > 0)

    hist = row.get("macd_hist")
    if pd.notna(hist) and float(hist) > 0:
        score += 15
        details["macd_pos"] = 1
    else:
        details["macd_pos"] = 0

    rsi14 = row.get("rsi14")
    if pd.notna(rsi14):
        r = float(rsi14)
        if 50 <= r <= 70:
            score += 15
            details["rsi_zone"] = "bull"
        elif r > 70:
            score += 5
            details["rsi_zone"] = "overbought"
        elif 40 <= r < 50:
            score += 5
            details["rsi_zone"] = "neutral"
        else:
            details["rsi_zone"] = "weak"
    else:
        details["rsi_zone"] = "na"

    rs20 = row.get("relative_strength_20d")
    if pd.notna(rs20) and float(rs20) > 0:
        score += 10
        details["rs20_pos"] = 1
    else:
        details["rs20_pos"] = 0

    details["score"] = score
    details["reason"] = build_reason(row)
    return int(score), details


def score_trend_label(score: int) -> str:
    if score >= 70:
        return "强势上行"
    if score >= 50:
        return "偏强"
    if score >= 30:
        return "震荡"
    return "偏弱"
