"""Study 1 Price Bottom — Lane 2 Core 研究。

检验长期价格底部（P756 LOW）是否有统计意义上的赔率优势，
以及 MA20 / MA60 恢复作为最小市场确认（PROBE 2% 依据）是否成立。

口径（v1 固化，与用户锁定版本一致）：
- Universe：729 只 FULL ETF（本地 raw 历史 ≥ 756 交易日）
- Entry state：P756 LOW / PRICE_LOW_DD30 / MA20_RECOVERY / MA60_RECOVERY
- 前向：20/60/120 交易日，close→close，同源 close 面板
- MAE/MFE：窗口 [t, t+h] 内 close 相对事件日 close 的最小/最大变动
- 基准：同市场横截面中位前向收益（复用 event_study 口径，全离线、确定性）
- 复权：本地 ETF raw close 为东财日线，直接使用；单日 |ret|≥20% 标记 corporate_action
- 不依赖 mapping：本研究中不消费 tracking_index / valuation，纯价格状态
"""

from __future__ import annotations

from pathlib import Path

from src.common.paths import outputs_dir

STUDY_DIR = outputs_dir() / "research" / "etf_bottom"

# 价格状态参数（P756 窗口沿用 indicator_spec 的 lookback_days 口径）
P_WINDOW = 756          # 价格分位窗口（交易日）
DD30_WINDOW = 30        # 深跌窗口（主分析）
DD120_WINDOW = 120      # 深跌敏感性窗口
DD30_THRESHOLD = -0.20  # DD30 深跌门槛
P_LOW_THRESHOLD = 20.0  # P756 ≤ 20% 视为价格低位
MA20_WINDOW = 20
MA60_WINDOW = 60
CORP_ACTION_RET = 0.20  # 单日 |ret| ≥ 20% 标记份额折算/异常行情

HORIZONS = (20, 60, 120)
