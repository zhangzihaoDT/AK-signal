from __future__ import annotations

import pandas as pd
import pytest

from src.sw_industry_rps import ths_mapping
from src.sw_industry_rps.cli import _build_realtime_provisional, _fetch_ths_enrichment

# 同花顺 90 行业全量名单（与 stock_board_industry_name_ths 输出一致）
ALL_THS_BOARDS = [
    "半导体", "白酒", "白色家电", "保险", "包装印刷", "厨卫电器", "电池", "电机", "电力",
    "电网设备", "多元金融", "电子化学品", "房地产", "风电设备", "非金属材料", "服装家纺",
    "纺织制造", "工程机械", "光伏设备", "贵金属", "轨交设备", "港口航运", "公路铁路运输",
    "钢铁", "光学光电子", "工业金属", "环保设备", "环境治理", "互联网电商", "黑色家电",
    "化学纤维", "化学原料", "化学制品", "化学制药", "IT服务", "机场航运", "军工电子",
    "军工装备", "家居用品", "计算机设备", "金属新材料", "教育", "建筑材料", "建筑装饰",
    "零售", "旅游及酒店", "美容护理", "煤炭开采加工", "贸易", "农产品加工", "农化制品",
    "能源金属", "汽车服务及其他", "汽车零部件", "汽车整车", "其他电源设备", "其他电子",
    "其他社会服务", "软件开发", "燃气", "塑料制品", "食品加工制造", "生物制品", "石油加工贸易",
    "通信服务", "通信设备", "通用设备", "文化传媒", "物流", "消费电子", "小家电", "小金属",
    "橡胶制品", "元件", "医疗服务", "医疗器械", "饮料制造", "油气开采及服务", "影视院线",
    "游戏", "银行", "医药商业", "养殖业", "自动化设备", "综合", "证券", "中药", "专用设备",
    "造纸", "种植业与林业",
]


def test_mapping_covers_all_ths_boards():
    mapped = set(ths_mapping.THS_TO_SW.keys())
    unmapped = set(ths_mapping.THS_UNMAPPED)
    assert not (mapped & unmapped), "同一板块不能同时映射与跳过"
    missing = set(ALL_THS_BOARDS) - mapped - unmapped
    assert not missing, f"未覆盖的同花顺板块: {sorted(missing)}"


def test_mapped_codes_are_valid_si_format():
    for code in ths_mapping.mapped_sw_codes():
        assert code.endswith(".SI")


def test_lookup_known_board():
    assert ths_mapping.lookup_sw_code("半导体") == "801081.SI"


def test_lookup_unknown_board_returns_none():
    assert ths_mapping.lookup_sw_code("不存在的板块") is None


def test_lookup_unmapped_returns_none():
    assert ths_mapping.lookup_sw_code("银行") is None


def _sw_raw_df():
    return pd.DataFrame({
        "trade_date": pd.to_datetime(["2026-07-28", "2026-07-29", "2026-07-30"]),
        "close": [980.0, 990.0, 1000.0],
    })


def _rt_df():
    return pd.DataFrame({
        "指数代码": ["801081", "801125", "801999"],
        "指数名称": ["半导体", "白酒Ⅱ", "不存在行业"],
        "昨收盘": [1100.0, 500.0, 300.0],
        "今开盘": [1090.0, 495.0, 299.0],
        "最新价": [1110.0, 505.0, 301.0],
        "成交额": [1e6, 2e6, 3e6],
        "成交量": [100, 200, 300],
    })


def test_build_realtime_provisional_morning_uses_prev_close(tmp_raw_dir):
    # 目标 07-31 < 今天(08-03) → 用 昨收盘
    from src.sw_industry_rps import storage
    storage.save_industry_raw(_sw_raw_df(), tmp_raw_dir, "801081.SI")
    from datetime import datetime
    df = _build_realtime_provisional(_rt_df(), tmp_raw_dir, ["801081.SI", "801125.SI"], "20260731",
                                     now=datetime(2026, 8, 3, 10, 0))
    assert len(df) == 1
    row = df.iloc[0]
    assert row["industry_code"] == "801081.SI"
    assert row["trade_date"] == pd.Timestamp("2026-07-31").date()
    assert row["close"] == pytest.approx(1100.0)  # 昨收盘
    assert row["pct_chg"] == pytest.approx((1100 / 1000 - 1) * 100)
    assert row["data_status"] == "provisional"
    assert row["source"] == "realtime"


def test_build_realtime_provisional_evening_uses_latest(tmp_raw_dir):
    from src.sw_industry_rps import storage
    storage.save_industry_raw(_sw_raw_df(), tmp_raw_dir, "801081.SI")
    from datetime import datetime
    df = _build_realtime_provisional(_rt_df(), tmp_raw_dir, ["801081.SI"], "20260803",
                                     now=datetime(2026, 8, 3, 15, 30))
    assert len(df) == 1
    assert df.iloc[0]["close"] == pytest.approx(1110.0)  # 最新价


def test_build_realtime_provisional_skips_inactive(monkeypatch, tmp_raw_dir):
    from src.sw_industry_rps import storage
    storage.save_industry_raw(_sw_raw_df(), tmp_raw_dir, "801081.SI")
    # active 列表不含 801125 → 只生成 801081
    df = _build_realtime_provisional(_rt_df(), tmp_raw_dir, ["801081.SI"], "20260731")
    assert set(df["industry_code"]) == {"801081.SI"}


def test_fetch_ths_enrichment_returns_volume_amount(monkeypatch, tmp_raw_dir):
    ths_df = pd.DataFrame({
        "trade_date": pd.to_datetime(["2026-07-30", "2026-07-31"]),
        "close": [100.0, 110.0],
        "volume": [1000, 1200],
        "amount": [1e6, 1.2e6],
        "source": "ths_board",
    })

    def fake_fetch(board, start_date=None, end_date=None, max_retries=3):
        return ths_df if board == "半导体" else pd.DataFrame()

    monkeypatch.setattr("src.sw_industry_rps.cli.data_source.fetch_board_industry_index_ths", fake_fetch)
    enr = _fetch_ths_enrichment(tmp_raw_dir, ["801081.SI", "801125.SI"], "20260731")
    assert len(enr) == 1
    row = enr.iloc[0]
    assert row["industry_code"] == "801081.SI"
    assert row["volume"] == 1200
    assert row["amount"] == pytest.approx(1.2e6)


def test_fetch_ths_enrichment_skips_missing_target(monkeypatch, tmp_raw_dir):
    ths_df = pd.DataFrame({
        "trade_date": pd.to_datetime(["2026-07-29", "2026-07-30"]),
        "close": [90.0, 95.0],
        "volume": [1, 1],
        "amount": [1.0, 1.0],
        "source": "ths_board",
    })
    monkeypatch.setattr(
        "src.sw_industry_rps.cli.data_source.fetch_board_industry_index_ths",
        lambda board, start_date=None, end_date=None, max_retries=3: ths_df,
    )
    enr = _fetch_ths_enrichment(tmp_raw_dir, ["801081.SI"], "20260731")
    assert enr.empty
