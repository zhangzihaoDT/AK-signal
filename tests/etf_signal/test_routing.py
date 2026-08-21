"""Symbol routing 单元测试。

锁定新浪接口的交易所前缀映射矩阵——上次 200 只回填失败就是因为
520/530/560/588/589 被错误路由到 sz。以后改坏 routing 立即失败。
"""

import pytest

from src.etf_signal import data_source

# 交易所映射矩阵：代码段 → 新浪前缀
ROUTING_MATRIX = [
    # SZSE（深交所）：代码 1 开头（15/16/18）
    ("159915", "sz"),   # 159xxx
    ("159082", "sz"),
    ("162411", "sz"),
    ("160415", "sz"),
    # SSE（上交所）：宽基/行业/主题 51x
    ("510050", "sh"),   # 510xxx
    ("510300", "sh"),
    ("511010", "sh"),
    ("511970", "sh"),
    ("512100", "sh"),
    ("513100", "sh"),
    ("515790", "sh"),
    ("516160", "sh"),
    ("517520", "sh"),
    ("518880", "sh"),
    # SSE：新代码 52x/53x/56x
    ("520950", "sh"),   # 520xxx
    ("526030", "sh"),   # 526xxx
    ("530300", "sh"),   # 530xxx
    ("560650", "sh"),   # 560xxx
    ("561990", "sh"),
    ("562990", "sh"),
    ("563000", "sh"),
    # SSE：科创板 588/589
    ("588000", "sh"),   # 588xxx
    ("588220", "sh"),
    ("589150", "sh"),   # 589xxx
    ("589460", "sh"),
]


@pytest.mark.parametrize("code,expected_exchange", ROUTING_MATRIX)
def test_sina_exchange_matrix(code: str, expected_exchange: str) -> None:
    assert data_source.sina_exchange(code) == expected_exchange


@pytest.mark.parametrize("code,expected_exchange", ROUTING_MATRIX)
def test_sina_symbol_prefix(code: str, expected_exchange: str) -> None:
    symbol = data_source.sina_symbol(code)
    assert symbol == f"{expected_exchange}{code}"


def test_sina_symbol_exact() -> None:
    assert data_source.sina_symbol("588220") == "sh588220"
    assert data_source.sina_symbol("159915") == "sz159915"
    assert data_source.sina_symbol("560650") == "sh560650"


def test_sina_viable_covers_all_segments() -> None:
    for code, _ in ROUTING_MATRIX:
        assert data_source.is_sina_viable(code) is True


def test_is_kcb() -> None:
    assert data_source.is_kcb("588000") is True
    assert data_source.is_kcb("589150") is True
    assert data_source.is_kcb("510050") is False
    assert data_source.is_kcb("159915") is False
