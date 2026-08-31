"""August 2026 Cross-sectional Return Study.

用 2026-07-31 可获得的信息，研究 2026-08-28 截止的 A 股横截面收益，
并检验现有 theme / tier / trend / position 标签能否构造出稳定跑赢 HS300 的组合。

口径（v1 固化）：
- 特征源：2026-07-31 processed / historical_signals（trend_score/MA/RSI/momentum）
- 收益源：2026-07-31 close → raw qfq；2026-08-28 close → raw qfq
- 复权：一律 qfq（前复权），写进 provenance
- 组合构造：只用 7/31 信息排名，禁止用 8 月实际收益回选
"""

from __future__ import annotations

from pathlib import Path

from src.common.paths import outputs_dir, raw_dir, processed_dir

STUDY_DIR = outputs_dir() / "research" / "aug2026"
MARKET_DAILY_DIR = STUDY_DIR / "market_daily"
CHUNK_DIR = STUDY_DIR / "chunks"

WINDOW_START = "2026-07-31"
WINDOW_END = "2026-08-28"
ADJUST = "qfq"

# 质量标记阈值
STALE_DAYS_LIMIT = 5          # 数据滞后超过 N 交易日视为停牌/未更新
MIN_FULL_SAMPLE_DATES = 15    # 8 月至少参与 N 个交易日才算 full_month_sample
