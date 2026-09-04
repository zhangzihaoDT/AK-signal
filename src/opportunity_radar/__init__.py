"""
Opportunity Radar V1 — Theme 外机会发现（Discovery / Observation）

独立于 Selection V2：消费已落盘的 Layer① / Lane2 / Lane3 事实与
config/theme_registry.yaml 的唯一 Theme 事实源，发现当前 Theme Registry
尚未覆盖的强势 ETF 方向。不联网、不重算指标、不制造新事实、
不修改 Selection recommended/action。

产物：outputs/opportunity_radar/opportunity_radar_{trade_date}.{json,html}
JSON 是事实源，HTML 只是 renderer。
"""

from __future__ import annotations

VERSION = "0.1.0"
RULE_LABEL = "OPPORTUNITY_RADAR_V1"
