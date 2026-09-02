"""Lane 3 — Trend Transition（趋势切换）。

核心研究问题：
    一个资产从长期弱势/底部状态第一次向上切换时（first exit），能否在当时识别
    这次切换会继续演化成趋势，而不是重新退回底部？

三条 Lane 职责：
    Lane 1 · Leadership       谁已经是强者？      趋势延续
    Lane 2 · Bottom / Repair  哪里有底部赔率？    均值回归
    Lane 3 · Trend Transition 谁正在从弱势切换成趋势？  状态转换

本包当前实现 Study 3A · Post-924 Bottom-to-Trend Transition：
    验证「底部 → 非底部」的切换结构（transition phenomenon）在 2024-09-24 附近
    是否发生断点式变化。Escape / Retest 是 transition 之后的**结果标签**，不是
    研究对象本身。

口径（用户锁定，详见 AGENTS.md / 对话讨论）：
  - RIGHT_CENSOR 用「全市场交易日历」，不用单只 ETF 自身报价行数；
    停牌/缺失不拉长窗口，另保留 forward_data_complete 记录个体 OHLCV 完整度。
  - event-weighted estimate（每 exit 一票）与 date-block bootstrap inference
    （按交易日块重抽样估计 CI/p-value）统计语义分开，不混为一个 bootstrap。
  - 3A 的 market-controlled PASS 是可执行 checklist（P1-P5），不做经济阈值。
  - trajectory 核心字段 = days_to_first_retest + escape_*/right_censored_*；
    ESCAPE/RAPID_RETEST/... 只是 report-friendly 派生标签，不是互斥上帝视角分类。
  - 3B target 契约：y = ESCAPE_120D，仅 right_censored_120d == False 样本。

本包只消费 Lane 2 的 v1_signal_daily.parquet 作为输入事实数据 + data/etf_signal/raw/。
不修改、不读取、不覆盖任何既有 Lane 1/Lane 2 研究决策逻辑。
"""

from __future__ import annotations

from pathlib import Path

from src.common.paths import outputs_dir

STUDY_DIR = outputs_dir() / "research" / "trend_transition"

# Study 3A 参数（用户锁定）
HORIZONS = (20, 60, 120, 250)        # 生存曲线评估点（交易日）
CLEAN_ESCAPE_HORIZON = 120           # 主业务标签：离开后连续 120 交易日未重入
PERSISTENCE_PRIMARY = 3              # 主口径：连续 N 交易日确认才翻转
PERSISTENCE_ROBUST = 5               # robustness 口径
BREAK_DATE = "2024-09-24"            # 主假设断点（精确交易日）
BREAK_WINDOWS = (40, 63, 90)         # 断点周边局部窗口（交易日，主=63）

# collision 守卫：0 与 False 区分（persistence=0 表示 raw，不做连续日确认）
PERSISTENCE_RAW = 0
