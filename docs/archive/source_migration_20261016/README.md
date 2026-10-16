# 数据源切换历史归档

这些材料记录供应商探索、身份核对、试运行和分阶段切换，不作为当前生产规范。现行入口为 [模块来源](../../OBSERVATION_DATA_SOURCES.md)、[事实契约](../../MARKET_FACTS_CONTRACT.md)、[TDX集成](../../TDX_INDUSTRY_INTEGRATION.md)、[能力状态图](../../ARCHITECTURE_MAP.md)。

- `AKSIGNAL_DATA_SOURCE_AUDIT.md`：旧数据需求与供应商覆盖调查。
- [候选架构与可行性思考](../../../config/decisions/archive/20261016_market_data_feasibility.md)：业务决策历史，归config管理。
- `AKSIGNAL_DAILY_IMPROVEMENT_BATCHES.md`：已结束的分阶段执行记录。
- `MARKET_FACTS_CONTRACT_PILOT.md`：试运行时期契约说明。
- `AGENT_MANUAL_BEFORE_CLEANUP.md`：完整旧操作手册，仅供研究历史追溯，不是AGENTS指令文件。
- `validation/`：旧SW/Tushare/TDX身份调查、试运行与切换过程；早期脚本只作历史文本，不作为当前验证入口。

已退出运行树的试运行代码及`data/pilots`原始文件有逐文件SHA256备份。恢复位置和摘要见 [清理回执](../../validation/SOURCE_MIGRATION_CLEANUP_20261016.json)。备份存放在系统临时目录，便于本次恢复；若要永久保存，应转存该压缩包到长期备份位置。正式数据与关键验收证据继续保留在项目中。
