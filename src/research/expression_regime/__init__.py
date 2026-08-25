"""
Expression Regime Event Study（v0.10 研究链路）— 验证「表达方式结构判断」的预测力。

核心问题：Layer③ 的 ETF_PRIORITY / LEADER_PRIORITY / CORE_PLUS_LEADER 市场结构判断，
是否真的能预测下一阶段哪种表达方式（ETF / 龙头个股 / 混合）更优？

研究设计（混合方案，结构输入源可插拔）：
  事件 = 主题确认日 + 结构判定日（confirmed 且 expression != WATCHLIST_ONLY）
  对每个事件日同时计算三种表达的 counterfactual 前向收益（ETF / Leader / 等权组合）
  评估「系统判定的表达是否事后最优 / 不低于次优」——命中率即结构判断预测力的直接度量

结构输入源：
  TierStructureInput     历史重放（universe 个股价格 2018+），Tier 篮子结构近似
  IndustryStructureInput 生产 confirmation 行业结构（participation/HHI/Top3，Enrichment）
  两者输出同一 broad / leader_dominated → expression 映射，可插拔切换。

产物：outputs/research/expression_regime/
  events_{start}_{end}.parquet   事件级明细（含三种表达前向收益）
  summary_{start}_{end}.parquet  分组汇总
  expression_regime_{start}_{end}.json / .html
"""
