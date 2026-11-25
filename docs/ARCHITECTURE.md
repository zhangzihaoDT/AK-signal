# AKsignal 项目架构

## 当前定位

AKsignal 是金融数据资产与专题研究平台，覆盖 Collection、Data Assets、Serving、Research。它负责金融数据采集、身份与口径标准化、质量治理、历史快照及来源证据、可信文件供给，以及基于数据资产的市场观察和专题研究。

GridTrading 独立负责策略开发、参数优化、策略回测、交易信号、交易账本和自动化交易运行。AKsignal 不新增或维护交易策略、选筹、账户映射、买卖信号或组合执行链。

## 数据与报告主链

```text
Longbridge / ETF EM-Sina / CSIndex / CFFEX / TDX
                         ↓
       raw 历史基座、身份、来源与质量证据
                         ↓
          Market Facts 标准化与质量 Gate
                         ↓
       Daily Observation / 专题研究 / 文件供给
```

日常正式入口为 `make run-day`：ETF 行情更新与事实计算、Longbridge 个股事实、核心指数/CFFEX/TDX 观察构建、验收和原子发布。当前行业正式来源为 TDX Research Industry；SW 历史行情和来源证据保留，但 SW RPS 更新、确认及报告链已退役。

正式事实不能被研究解释或下游策略改写。来源失败、目标日缺失、历史不足和价格口径未知必须显式保留，不能用旧数据冒充新数据，也不能把价格位置解释为交易建议。

## 核心模块

| 模块 | 职责 |
|---|---|
| `src/market_facts/` | 数据契约、来源适配、日期/质量门、个股/指数/衍生品/TDX 行业事实及 ETF 横截面事实 |
| `src/etf_signal/` | ETF 行情采集、主表、历史补齐、分类事实和日指标计算；不含 Rotation 报告或交易信号 |
| `src/observation/` | 组合已验收事实、渲染日报并校验数据/展示契约 |
| `src/research/baskets/` | 正式 Basket Compare V2 的 comparison/nav/report 链路 |
| `src/hub/` | 只读日报状态/附件与已 hash-pin 的 Basket Compare 研究摘要；不抓行情、不执行策略 |
| `dashboards/` | 本地入口页，不复制、不托管报告或数据 |

正式入口、数据来源及当前覆盖范围以 [ARCHITECTURE_MAP](ARCHITECTURE_MAP.md)、[OBSERVATION_DATA_SOURCES](OBSERVATION_DATA_SOURCES.md) 和 [PROJECT_STRUCTURE](PROJECT_STRUCTURE.md) 为准。

## 下游 Serving 边界

当前 Serving 主要是本地、版本化文件与内部 Python readers，不是稳定对外 API 服务。GridTrading 的已知读取仍包括一次性研究脚本直接读取 ETF master/cache 路径；完整跨项目契约尚待设计。本轮不新建 API、不修改 GridTrading、不引入基础设施。后续文件契约至少应绑定 schema、资产身份、trade/source date、price basis、质量状态、日历版本、来源及内容 SHA-256。

## 退役模块与历史记录

ETF Rotation/候选/账户/交易信号链、旧 SW 行业 RPS 运行链和 Basket legacy 实现不属于当前产品能力。正式 ETF/TDX 数据采集及日报依赖的事实计算继续保留。历史设计和验证记录不等于可运行入口；历史批次、SW 数据、Manifest 与官方来源证据按数据保留规则处理。
