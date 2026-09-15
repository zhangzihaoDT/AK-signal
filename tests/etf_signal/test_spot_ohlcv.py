"""EM 全市场 spot 快照的占位 bar 过滤（2026-09-03 / 09-15 事故根因回归）。

盘前/盘中 spot 会把当日所有 ETF 以 "-"（NaN）占位返回；若原样合并入库，会形成
「行存在但 close 为 NaN」的占位空 bar，经 rolling 毒化 Lane2/Lane3。此过滤是
raw 写入边界「不得落 NaN bar」守卫的第一道。
"""

import pandas as pd

from src.etf_signal import data_source


class _FakeAkshare:
    def __init__(self, spot: pd.DataFrame):
        self._spot = spot

    def fund_etf_spot_em(self) -> pd.DataFrame:
        return self._spot


def _patch_ak(monkeypatch, spot: pd.DataFrame) -> None:
    # data_source.ak 是 _ensure_akshare() 里惰性导入的模块级全局；直接注入 fake
    monkeypatch.setattr(data_source, "ak", _FakeAkshare(spot), raising=False)
    monkeypatch.setattr(data_source, "_ensure_akshare", lambda: True)


def test_spot_skips_placeholder_nan_bars(monkeypatch) -> None:
    # 盘前：两行都只有日期、价格/量为 "-"（占位）→ 全部跳过
    spot = pd.DataFrame({
        "代码": ["510300", "159611"],
        "开盘价": ["-", "-"],
        "最高价": ["-", "-"],
        "最低价": ["-", "-"],
        "最新价": ["-", "-"],
        "成交量": ["-", "-"],
        "成交额": ["-", "-"],
        "数据日期": ["2026-09-15", "2026-09-15"],
    })
    _patch_ak(monkeypatch, spot)
    out = data_source.fetch_ohlcv_from_spot()
    assert out.empty


def test_spot_keeps_valid_rows_only(monkeypatch) -> None:
    # 混合：510300 有真实 close，159611 仍为占位 → 只保留有效行
    spot = pd.DataFrame({
        "代码": ["510300", "159611"],
        "开盘价": ["4.55", "-"],
        "最高价": ["4.57", "-"],
        "最低价": ["4.53", "-"],
        "最新价": ["4.552", "-"],
        "成交量": ["6291774", "-"],
        "成交额": ["2.86e9", "-"],
        "数据日期": ["2026-09-14", "2026-09-15"],
    })
    _patch_ak(monkeypatch, spot)
    out = data_source.fetch_ohlcv_from_spot()
    assert list(out["fund_code"]) == ["510300"]
    assert out["close"].notna().all()
