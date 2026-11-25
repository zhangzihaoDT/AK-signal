# 业务职责收敛：数据资产与专题研究平台

日期：2026-11-25。状态：用户批准并执行。

## 决策

AKsignal 正式定位为 **Financial Data Assets & Research Platform**，承担 Collection、Data Assets、Serving、Research 四项职责。GridTrading 独立承担策略开发、参数优化、回测、交易信号、交易账本与自动化。AKsignal 不再建设独立交易策略运行体系，也不把退役代码迁移到 GridTrading。

保留的正式能力：

- 数据采集与资产沉淀：Longbridge、CFFEX、CSIndex、TDX、ETF 来源、历史补齐、身份、日历、标准化、质量 Gate、Manifest、来源证据和历史快照。
- 市场观察与专题：Market Facts 日报、Holdings Dashboard、CSI500 Price Channel、正式 Basket Compare V2、IC500 全历史/期限结构、A-Shape V2 历史形态研究。
- A-Shape V2 仅做历史价格形态量化研究，不是交易系统或信号生成器。

退役的能力：

- ETF Rotation 报告、Watchlist、账户映射、候选卡、BUY_CANDIDATE 信号、独立 backtest 和 ETF 独立 run-day；保留 ETF 数据采集、主表、分类输入、历史回补和事实指标计算。
- SW 行业 RPS 的更新、计算、确认、结构、THS provisional 和 HTML 报告；正式行业来源固定为 TDX Research Industry。SW 历史原始行情、加工数据、来源证据和 Manifest 永久保留。
- Basket legacy 多源计算/抓取/报告；正式 Basket Compare V2 保留。
- Strategy/Execution/Portfolio 配置与模型、`final_validation` 旧 Action 验收 CLI。
- 一次性 Group Attention 预览、CSIndex 接口探测、A-Shape V1。

## 边界与理由

- 数据资产优先于报告产物；退役报告不等于删除其依赖的历史行情、分类、来源证据和快照。
- 市场事实与交易策略严格分离；事实不因下游策略需要而改写，展示解释不反向改变计算。
- 不重复建设 GridTrading 已承担的策略和执行能力；不恢复旧 Selection、Timing、三 Lane、Radar 或旧回测。
- 不新增跨项目 API 或基础设施；现有 GridTrading 读取 Gap 与文件契约设计见 `docs/DATA_SERVING_CONTRACT.md`。

## 验收

执行记录、文件级删除清单、数据资产保留清单、前后量化对比及正式日链一致性见 `docs/validation/RETIREMENT_20261125.md`。同日正式日报在治理前后的事实 parquet/摘要/final_check 逐列一致；`make run-day-offline` 正常发布，全量测试 677 项通过。
