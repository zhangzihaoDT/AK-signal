"""
Run-day Summary — run-day 结论汇总（纯只读消费层）

职责：
  在 run-day 末端读取已落盘的 Layer③ Selection 与 Opportunity Radar 事实，
  汇总成一份人类可读的当日结论：
    ① 今日推荐（Layer③ Decision）
    ② 观察范围外机会（Radar Observation / Discovery）
    ③ 宽基指数值得研究（Radar Market Beta）

边界：
  纯消费者——不联网、不重算、不制造新事实、不修改任何上层产物；
  只读 outputs/selection/tradable_candidates_{date}.json 与
  outputs/opportunity_radar/opportunity_radar_{date}.json。

产物：outputs/run_summary/run_summary_{trade_date}.{json,md}
JSON 是事实源，Markdown / 终端输出只是 renderer。
"""

from __future__ import annotations

VERSION = "0.1.0"
RULE_LABEL = "RUN_DAY_SUMMARY_V1"
