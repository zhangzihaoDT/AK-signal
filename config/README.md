# config/ — 数据来源、观察范围与研究定义

AKsignal 的配置真源描述金融数据资产如何采集、识别、分类和质量验收，以及研究专题的对象范围。此目录不再定义交易策略、买卖信号、执行模型或组合分配规则；这些能力由 GridTrading 承担。

| 文件 / 目录 | 职责 | 主要消费者 |
|---|---|---|
| `observation_sources.yaml` | 股票、ETF、核心指数、衍生品和行业的正式来源、价格口径、回退边界 | `src/market_facts/`、日报验收 |
| `market_data.yaml` | ETF 获取、限速、熔断及增量更新参数 | ETF 数据采集 |
| `selection_universe.yaml` | 固定观察资产身份/分组来源；不是交易候选池或买入授权 | Observation、Basket Compare、资产身份读取 |
| `research_observations.yaml` | 专题研究观察组与分组身份 | Observation、Research Basket |
| `theme_registry.yaml` | 市场观察/专题研究的主题及行业/ETF 线索 | Observation 与研究页面，不产生交易信号 |
| `etf_classification.yaml` | ETF 投资市场/方向证据分类规则与待核实边界 | ETF 事实分类维护 |
| `etf_classification_facts.json` | 已发布逐基金分类、状态和证据引用 | 日报批次只读消费 |
| `etf_classification_versions/` | 不可变分类版本及其审计 | 分类发布与历史复核 |
| `indicator_spec.yaml` | 正式 RPS/MA 窗口、横截面和数据质量参数 | ETF 市场事实计算 |
| `core_indices.yaml` | 固定核心指数观察清单及来源映射 | 核心指数 Market Facts |
| `derivatives.yaml` | 具体 CFFEX 合约观察定义 | 衍生品 Market Facts |
| `tdx_research_industry_20261016.csv` | 固定 128 个 TDX Research Industry 及分类版本 | TDX 行业采集/计算 |
| `market_calendars/`、`a_share_calendar.json` | 交易日历与版本摘要 | 日期门控及观察验收 |
| `research_baskets.yaml` | 正式 Basket Compare 的研究篮子、权重和基准 | `src/research/baskets/` |
| `research_stage_log.yaml` | 研究观察组商业化阶段变更审计 | Research Basket |
| `observation_snapshots.json` | 已发布行业事实快照登记 | Daily Build / Validate / Publish |
| `decisions/` | 项目边界与数据/研究取舍记录 | 维护者复核 |

## 退役配置

ETF Selection/Strategy、Execution、Portfolio 和旧 SW RPS 运行配置已退役。旧 SW 历史行情及加工结果仍保存在 `data/legacy/`；数据留存不代表旧 SW CLI 或报告链继续受支持。旧配置和实现由 Git 历史追溯，不在当前运行链重载。

## 维护规则

- 数据源、分类、交易日、调整口径、样本覆盖及缺失状态必须显式可追溯；名称相似不构成身份或来源证据。
- 正式观察按 `trade_date` 与已验收数据生成；`run_date` / `generated_at` 仅为审计时间。
- 事实展示不能回写底层事实；研究报告不生成买卖动作。
- 详细字段与运行契约见 `docs/OBSERVATION_DATA_SOURCES.md`、`docs/MARKET_FACTS_CONTRACT.md`、`docs/DAILY_INFORMATION_ARCHITECTURE.md`。
