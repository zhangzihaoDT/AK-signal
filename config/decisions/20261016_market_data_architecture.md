# 2026-10-16 · 独立模块数据源与观察结构

状态：Accepted，用户明确指定。替代探索阶段的“候选主源、跨供应商备用、申万仍为正式基准”。

## 产品定位与层级边界

AKsignal 的长期定位为 **Investment Decision System（投资决策系统）**，分为四层：01 Data Foundation（行情来源、历史、增量与质量）→ 02 Market Facts（日报、行业趋势、篮子 Compare）→ 03 Strategy（机会、信号、策略验证与买卖规则）→ 04 Portfolio / Position（持仓、风险暴露与调仓决策）。

Facts 不知道 Strategy 的存在，也不编码策略语义；Strategy 不直接维护底层行情；Position 不生成市场信号。策略信号不等于仓位决策，持仓约束与执行反馈不能篡改市场事实。v0.6.0 的建设重点为 Data Foundation 与 Market Facts 稳定化；后续 Strategy 和 Portfolio / Position 按新边界重新设计。

## 选择与原因

| 业务模块 | 来源与口径 | 选择理由 |
|---|---|---|
| 个股观察 | 长桥OpenAPI / ForwardAdjust | 同一供应商序列计算趋势、收益、位置，基准也同源；不与旧东财/新浪拼接 |
| ETF与大类资产 | 东方财富/新浪现有模块路由 | 保留现有ETF运行能力，各序列实际来源与口径清楚记录 |
| 行业轮动 | TDX Research Industry 881xxx，128个二级行业 | 同一分类版本、官方.day价格、自算收益与RPS，避免与申万横截面混合 |

三模块独立维护历史，不相互备份，不强制分类映射。AKShare只是适配工具，不是供应商。SW2021退出正式日报，保留历史研究，不作失败兜底。TDX允许直接正式集成，不把尚未完成的连续五日稳定性观察当成硬性切换前置；实际失败仍严格门控。

## 观察范围与呈现

个股范围为固定股票池加8个研究篮子成分并集，代码规范化去重后69只；禾赛02525暂排除，当前68只有效。新增研究身份只作monitor_only，不赋予策略资格。主题可交叉，不把主题计数相加当成股票数量。

日报顺序：大类资产、行业轮动、ETF、个股观察；每部分说明来源，趋势与位置归入各自资产。个股按8个研究主题加高现金流分类。独立篮子比较同样使用9类/68只，固定初始权重后买入持有，不冒充真实组合。

## 明确承担的限制

更新失败为UNAVAILABLE，不拿旧行情冒充新结果；历史不足为INSUFFICIENT_HISTORY，缺失不填0。供应商复权算法未独立认证，不能声称已验证总回报。指标横截面分别定义，不跨ETF/行业直接比较。

机器真源：`observation_sources.yaml`、`tdx_research_industry_20261016.csv`、`selection_universe.yaml`、`research_observations.yaml`、`research_baskets.yaml`。实现与数据布局分别见 `docs/PROJECT_STRUCTURE.md`、`data/README.md`。
