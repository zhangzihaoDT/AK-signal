# AKsignal — 当前操作手册

更新：2026-10-24 · v0.7.1。业务架构思考与调整记录在 `config/decisions/`；工程历史与迁移过程见 `docs/archive/`，不作为当前每日运行规范。能力总览真源为 `docs/ARCHITECTURE_MAP.md`，模块来源真源为 `config/observation_sources.yaml`。

## 定位与边界

AKsignal = **Investment Decision System（投资决策系统）**，由四层组成：01 Data Foundation（数据源、历史行情、增量更新、数据质量）→ 02 Market Facts（市场日报、行业趋势、篮子 Compare）→ 03 Strategy（机会识别、信号生成、策略验证、买卖规则）→ 04 Portfolio / Position（仓位管理、持仓监控、风险暴露、调仓决策）。v0.6.0 稳定化 Data Foundation 与 Market Facts；v0.7.0 完成 Basket Compare v2、日报 RPS 三列与跨市场休市验收；v0.7.1 将分组趋势启动观察接入日报 04；Strategy 和 Portfolio / Position 尚未正式实现。

Data Foundation 对行情完整性、来源及质量负责；Market Facts 只消费标准化行情并描述市场，不感知 Strategy；Strategy 消费版本化事实，不直接维护或重算底层行情；Portfolio / Position 消费实际持仓和策略信号，不自行生成市场信号。信号不等于仓位决策，事实不可被策略或仓位建议覆盖。实际仓位、目标权重、调整量分别记录。

每日主链只发布 Market Facts，不输出买入推荐或投资 action。后续 Strategy 和 Portfolio / Position 按新架构重新设计，不恢复旧 Selection、Timing、三 Lane 或旧回测代码作为实现基础。

## 正式来源

- **个股**：仅长桥 OpenAPI / Longbridge ForwardAdjust，趋势、收益与位置共用同一快照；沪深300基准同源，000300.SH 为指数值。观察池为固定股票池与8个研究篮子并集，统一代码后69只；禾赛02525明确排除，当前68只有效。研究新增注册仅 monitor_only，不扩展主题确认权或策略准入。
- **ETF**：东方财富/新浪模块内部路由；AKShare 是适配器而非供应商。保留实际来源和价格依据，不拼接已知不同口径。ETF与个股/行业不互为备用。
- **行业**：通达信官方 .day；固定128个TDX二级研究行业，分类版本20261016，清单在 `config/tdx_research_industry_20261016.csv`。`data/sources/tdx/raw/day` 为历史基座，命中目标日零请求；缺日按官方ZIP成员做Range获取、本地仅追加。重叠历史改写必须明确报错，不静默覆盖。独立计算收益/RPS；SW2021仅历史研究，无正式备用回退。

模块独立维护历史，不强制分类映射；ETF与行业RPS横截面不同，不直接比较。来源、价格依据、分类版本、源交易日、目标交易日、生成时间及摘要保持可追溯；未知元数据不能追认为已验证。

## 日期与质量门控

统一使用最近完整交易日 `trade_date` 分区与命名；`run_date/generated_at` 只作审计。显式日期不能取代交易日历与收盘完整性验收，不使用盘中快照冒充日频收盘事实。

更新失败、目标日缺失、不完整行业横截面要标记 UNAVAILABLE，不将旧行情改名冒充新结果。历史不足为 INSUFFICIENT_HISTORY；不补造价格、收益或排名。TDX各窗口独立记录有效样本数，缺失不填0，并列排名用平均排名百分位；有效样本不足2时无RPS。

长桥公司行为算法未独立认证，price_basis_verified=false。原始OHLC异常严格拒绝；允许同源近一年有界重取，但长窗口不足仍未知并记录history_limitation。仅收盘价研究必须显式close_only，不认证或修复原始OHLC。交易日历为有界独立规则模型，不称交易所全历史认证。个股位置规则见 `docs/POSITION_OBSERVATION.md`。

## 运行、发布与报告

唯一日常入口 `make run-day`，离线用 `make run-day-offline`；`RUN_DATE=YYYYMMDD` 固定目标日。ETF更新/计算 → 长桥个股事实 → observation build（含TDX增量/计算）→ check → 统一发布。代码/配置变化后旧检查点须 `RUN_FLAGS=--restart`，不得绕过签名验证。

批次输出从空目录建立，验收后原子切换。失败保留上一发布，模块级降级遵循 PARTIAL 发布规则，`--require-complete`拒绝不完整发布。`*_latest`只在正式确认发布时更新。不得为清理而删除当前发布、恢复检查点、历史基座或被引用快照。

日报按大类资产、行业轮动、ETF、个股观察排序，每部分注明来源；趋势和位置归入各资产板块，审计放数据质量区。个股观察内的分组趋势启动提示复用本批快照，只提示阅读顺序，不构成交易动作。个股按8个研究篮子与独立高现金流分类，同股允许跨篮子，覆盖数不等于去重个股数。主题/ tier/participation是注册身份，不压为股票级单一标签。

`basket_compare` 是独立研究报告：8个研究篮子 + 高现金流参照，共9条净值/68只去重个股，长桥ForwardAdjust收盘价/同源沪深300。组间等权、组内等权 + 季度再平衡为默认结构指数，买入持有为对照；QR/BH共享同一名单、起点、快照、上市前现金规则和HS300日历，唯一差异是再平衡规则。报告同时展示RS、区间/20日参与度、集中度与重叠诊断；不含汇率/费用，不宣称真实组合收益、历史可执行策略或已验证总回报。入口及来源见 `docs/RESEARCH_BASKETS.md`。

## 工程维护

- 不修改或输出 `.env` 凭证；长桥适配器仅行情接口，不交易。
- 配置真源：主题 `theme_registry.yaml`，固定池 `selection_universe.yaml`，研究观察 `research_observations.yaml`，研究篮子 `research_baskets.yaml`。不重复维护清单；未注册主题按现有降级规则处理。
- 策略参数仍走 typed frozen loader；config_hash/universe_hash/rule_version 分清配置、资产范围、算法变化，不因取数性能参数修改策略版本。
- 对变更运行有针对性的测试；涉及来源时核对日期/口径/缺失与失败路径。报告只消费事实，不为展示反向改变事实。
- 保留原始事实和输入摘要；清理先查引用。归档材料不能被当作当前规范，历史研究数据不能充当正式日报兜底。
- 详细规范：`docs/OBSERVATION_DATA_SOURCES.md`、`docs/MARKET_FACTS_CONTRACT.md`、`docs/TDX_INDUSTRY_INTEGRATION.md`、`docs/DAILY_SCHEDULER.md`。

## 目录职责

config是业务架构与决策记录，docs是项目工程架构，data是按来源与raw/processed/audits/state组织的数据；存储说明见data/README.md。探索/验收/审计脚本分列scripts/exploration、validation、audits。Hub通信集中src/hub，生产入口不导入探索代码。正式篮子链为src/research/baskets/{comparison,nav,report}，旧研究隔离legacy。完整布局见docs/PROJECT_STRUCTURE.md。
