> 历史归档：已由正式来源架构替代，仅供追溯，不作为当前运行规范。

# AKsignal｜数据需求与供应商覆盖审计

> 审计基准：2026-10-16。范围为仓库当前代码、配置、缓存、正式发布物和已落盘运行记录。只读检查；未调用外部行情接口、未执行 Daily Pipeline、未重算或发布日报。
> 当前事实基线以 `docs/ARCHITECTURE_MAP.md`、`src/daily_run.py` 和实际产物为准。旧 Layer/Selection/Radar 设计文档不作为当前每日运行事实。

> **时间口径**：覆盖数字与 10/15 未发布状态是改进批次编码前的运行快照；后续模块级降级与事实质量元数据变更见 [`AKSIGNAL_DAILY_IMPROVEMENT_BATCHES.md`](AKSIGNAL_DAILY_IMPROVEMENT_BATCHES.md)。这些代码变更尚未通过新的正式 Daily 发布验证。

## 1. Executive Summary

### 结论

AKsignal 当前 Daily Pipeline 是**市场事实观察系统**，每日生成趋势、相对强度和价格位置观察；不运行 Selection 买卖建议，也没有 Portfolio State、实际持仓、成本或交易流水输入。核心依赖是：

1. **全市场场内 ETF 收盘行情**：ETF 行情主路由为东方财富（经 AKShare），失败时回落新浪；本地每只 ETF 一个 Parquet 缓存。
2. **申万行业指数日线与行业身份**：当前 124 个活跃二级行业；代码/名称/父行业 Master 来自 Legulegu 初始化接口并本地缓存，收盘价主源为 SWSResearch。当前没有已验证、真正独立的申万行业价格备用源。
3. **固定池股票 OHLCV**：A 股经 AKShare 访问东方财富/新浪/腾讯多源路由；10 月 14 日实际 56 个唯一 A 股样本全部由新浪成功补齐。港股 2 个唯一标的走 `stock_hk_daily`，实际上游无法由代码/审计记录确认。
4. **沪深 300 收盘序列**：用于固定池个股相对强弱及日期门控；本地缓存 `data/raw/_benchmark_sh000300.csv`，在线读取东方财富指数接口并有 `stock_zh_index_daily` 回退；回退的最终 provider 未由运行审计确认。
5. **A 股交易日历**：`config/a_share_calendar.json` 保存上交所公告日历；它是日期锚点，不是行情供应商。

### 最新正式日报与运行状态

- 最新正式 Daily Observation Report 为 **trade_date=2026-10-14**：1,331 只 ETF 趋势观察样本、212 只均线条件活跃 ETF、124 个行业、58 只唯一股票；正式回执 `CONFIRMED`，但 `data_complete=false`。证据：`outputs/observation/observation_20261014.json`、`outputs/run_day/final_check_20261014.json`、`outputs/run_day/publication.json`。
- 2026-10-14 ETF Master 为 1,554 只；该日 raw 缓存有 1,551 只目标日行情，缺 159210、512390、560650。rotation 中 1,501 只有 RPS15，1,500 只有 ΔRPS15；报告仍注明 3 只 ETF 未更新。
- 审计时读取到 2026-10-15 批次尚无正式发布回执。运行日志记录行业 10 月 15 日价格目标覆盖 **0/124**，`update_status.json` 为 `waiting_for_source`；不能将本地存在的 10 月 15 日分区记录当作已核验数据。ETF `source_audit_20261015.json` 也为 `partial`，两个残余代码 512390、560650 未取到目标行情。
- 因此，健康日有一定的价格采集能力，但**每日完整性当前受申万行业供应链单点制约**；即使供应商接通，股票复权与证券身份、ETF 分类和跟踪标的元数据仍未达到可信组合决策的质量门槛。

### 最小数据供应商组合

如限于当前已实现业务，建议保留最少的**三类市场数据分发端点**：

| 保留 | 用途 | 判断 |
|---|---|---|
| 东方财富（EM） | ETF Master / 行情首选；A 股股票与沪深 300 候选主路由 | 保留；近期稳定性不足，不能单独承担全部更新 |
| 新浪（Sina） | ETF 历史行情备用；A 股股票实际常用回退 | 保留为运行备用；与 EM 的平台不同，但最终原始上游是否真正独立尚 UNKNOWN |
| 申万研究（SWSResearch） | canonical 申万指数身份及行情 | 当前无法替换；必须建立一条**经验证的独立申万指数价格备用路径**，现阶段没有可计入的备用供应商 |

同花顺只在 ETF Master 初始化时做代码清单交叉核验、行业板块行情最多用于成交量/成交额增强；它没有申万指数身份，不是行业价格备用源。Legulegu 是当前缓存行业身份 Master 的初始化来源，但没有独立日线行情，也不能将其成分网页当作当前每日结构分析。**不建议现在新增宏观、外汇、铜铝现货/期货供应商或增加第三个 ETF 行情源。**

## 2. Business Coverage

当前唯一完整每日入口是 `make run-day`，实际步骤为：ETF 更新 → 行业更新 → ETF 计算 → 行业计算 → 个股指标 → Observation 构建 → 检查 → 统一发布。见 `Makefile`、`src/daily_run.py`。日报正文是市场方向、趋势活跃、底部位置三个观察区域，见 `src/observation/report.py`。

| 业务域 | 当前每天实际观察什么 | 当前状态 | 主要边界/空缺 |
|---|---|---|---|
| 大类资产 | 用场内 ETF 价格代理 A 股宽基、行业/主题、港股、海外权益、商品/黄金、现金/货币及未分类“其他”方向；按 RPS15、5 日排名变化和活跃比例汇总 | **已实现，代理观察** | 代码虽定义债券分类，但 10/14 Master / 日报没有实际债券方向行；不是宏观因子系统，不直接观测利率、信用、汇率、库存、商品期货曲线；当前未形成单独风险偏好信号 |
| ETF 趋势 | 场内 ETF Master / OHLCV；RPS1/15/20/60、RPS 动量、成交额流动性、价格相对 MA20/60 及斜率；报告活跃样本 1,331、活跃 212 | **已实现** | 当日有 3 个 ETF 缺目标日；ETF 主题/资产分类靠名称关键词，563/1,331 样本未分类；Master 的跟踪指数/代码为空 |
| 行业轮动 | 124 个申万二级代码；行业收盘价 → RPS5/10/15、RPS1、Δ5RPS15、阶段；再聚合至 31 个申万一级方向 | **已实现；对上游可用性高敏感** | 2026-10-15 已知验证为目标日 0/124；真正备用缺失。当前 Daily 报告不运行行业内部成分贡献/结构穿透 |
| 个股与主题 | 当前唯一股票样本为 A 股 56、港股 2；主题/层级从 `selection_universe.yaml` 注册；个股日线趋势分、5/20 日收益、技术诊断、60/120/360 日价格位置 | **已实现观察** | 固定池不是全市场个股扫描；港股上游未确认；全池复权未统一；主题强弱是观察分组，不等于主题 Gate 或推荐 |
| 投资组合与交易决策 | Daily 只交付事实，不读取持仓、现金、成本价、成交/委托或交易流水，不输出买入/持有/卖出建议 | **规划中的 Portfolio 未上线** | 旧 Selection 和交易/组合回测代码存在但不进入当前 Daily；回测假设不是实际账户状态 |
| 宏观与商品 | 不采集宏观时间序列；利率债、商品等只在能识别到的场内 ETF 价格类别中作为代理 | **无独立宏观/商品行情能力** | 无国债收益率、利率、汇率、货币信用、铜铝现货/期货、海外指数原始基准；当前并非 Daily 必需项 |

### 当前范围与未来规划的区分

- **Daily 已运行**：ETF/申万行业/固定池股票的 Data Foundation、趋势与价格位置观察；统一 trade_date、个股/行业日期核验和日报发布。
- **代码已实现但不在 Daily**：旧 Selection、机会扫描、Lane/赔率、Research Basket、逐笔与组合回测等研究/legacy 模块。它们不能证明每日已运行推荐或账户管理。
- **尚未正式实现**：`docs/ARCHITECTURE_MAP.md` 定义的 Portfolio State、Entry、Allocation、Exit、Portfolio Advice。`config/portfolio.yaml` 和 `config/execution.yaml` 是回测构建/撮合参数，不是实际持仓数据或生产账户。

## 3. Data Requirements

状态标记：**VERIFIED**=本地运行记录/产物直接证明；**PARTIAL**=有部分覆盖但有未核验范围或口径；**UNKNOWN**=仓库证据不能确认；**FAILED**=有明确失败证据。状态只表示本审计判断，不把 UNKNOWN 当作失败，也不把一次成功外推为长期稳定。

| Business Question | Output | Required Data | Asset Scope | Frequency | Current Source | Upstream Provider | Quality Requirement | Failure Impact | Status |
|---|---|---|---|---|---|---|---|---|---|
| 大类资产方向在变化吗？ | `asset_directions_{date}`、日报“市场方向” | ETF 代码/名称/分类；日收盘价；由收盘价派生 RPS15、5 日排名变化、MA20/60 活跃状态 | 场内 A 股宽基、行业/主题、QDII 海外/港股、商品/黄金、现金 ETF、未分类其他；债券 bucket 在配置中定义但本批次无观察行 | 日频、最近完整 A 股交易日 | EM ETF Master/行情；Sina ETF 历史回退；分类用 `classifier.py` 名称规则 | EM / Sina 为分发端；基金净值/成分与交易所原始上游 UNKNOWN；AKShare 是接口适配器 | 代码/简称有效；close 有目标日；分类明确；跨资产方向的代理含义及标的可复核 | 缺数据会少样本或扭曲方向中位数；分类空会落入其他/未分类 | PARTIAL |
| 全市场哪些 ETF 趋势活跃？ | `etf_trends_{date}`；日报活跃 ETF 表 | OHLCV 中 close、成交额；RPS1/15/20/60；1/5/10/15/20/60 日收益；MA20/60 与斜率；异常跳变标记 | 1,554 ETF master；观察产物 1,331 样本 | 日频 | EM 为主、Sina 为回退；raw `data/etf_signal/raw/{code}.parquet` | Eastmoney / Sina 分发；具体交易所行情原始提供方与两平台共同上游 UNKNOWN | target trade_date、有效 close；横截面分母一致；异常公司行动隔离；缓存源与调价口径可追溯 | 缺行情影响 RPS 样本/活跃榜；过期值可能导致跨日错读 | PARTIAL |
| ETF 长周期价格位置如何？ | `etf_positions_{date}`，60/120/360 日位置及可靠性 | 至少 360 个有效报价日的 close、日期、窗口高低；窗口内异常跳变 | 有足够历史的 ETF | 日频 | 同一 ETF raw 缓存；`research.etf_bottom.price_map` 计算 | 价格原始提供端同上；位置是 AKsignal 派生事实 | 日期截到目标日；不足/缺口/异常时位置为 unknown，不允许数值兜底 | unknown 增多时底部状态和前后变化不可判断 | PARTIAL（位置机制有明确 unknown 合约；样本有大量未知变化） |
| 124 个申万二级行业谁在走强/衰减？ | `industry_rotation_{date}`、`industry_directions_{date}`、日报行业表 | canonical 申万行业代码/名称/父行业；至少 close 与源端 trade_date；衍生 5/10/15 日收益、RPS、RPS1、Δ5RPS15 | Master 131 个，活跃 124 个二级行业；31 个一级方向 | 日频；目标日按 A 股交易日 | 缓存行业身份 Master（Legulegu 来源）；`index_analysis_daily_sw` 主源；`index_hist_sw` 与实时接口属于同一申万体系 | 行业身份来自 Legulegu 页面发布；行情来自 SWSResearch；更上游报价/指数计算授权链 UNKNOWN | 每个代码的源端价格日期必须等于目标日；行业版次/身份固定；124/124 无重复；价格源、源状态可审计 | 核心输入不足即停止更新/发布；行业短缺会破坏全横截面 RPS | 10/14 VERIFIED；10/15 FAILED（0/124 日期已核验） |
| 行业内部参与/成分支持趋势吗？ | **当前 Daily 无此输出**；旧代码/缓存有结构与成分研究产物 | 成分股代码、行业归属/权重、个股 OHLCV、公司行动 | SW 二级行业成员 | 日/按需 | Legulegu 成分网页与缓存可见于 `constituents.py`；当前主链不调用 | Legulegu 为发布/分发端；成员数据真实生产/更新时间 UNKNOWN | 成分时点版本、代码身份与权重一致；不能将同花顺自有行业当作申万 | 当前不影响 Daily RPS；不能据此声称已每日穿透行业结构 | UNKNOWN（非当前 Daily 能力） |
| 固定池公司是否形成趋势？ | `stock_metrics_{date}`、`stock_positions_{date}`、日报股票区 | 股票代码/市场/名称；OHLCV；close、high、low、volume；A 股相对强弱所需沪深300 close；复权/日期证据 | 56 CN + 2 HK 唯一代码；60 个注册身份（存在跨主题重复身份） | 日频 | A 股 AKShare EM→Sina→Tencent 回退；港股 `stock_hk_daily`；HS300 EM 指数接口及缓存 | A 股平台标识可见，最终原始提供方 UNKNOWN；港股 provider UNKNOWN；SSE/SZSE/SEHK 身份参照未完整入链 | 每条行情 target 对齐；OHLCV 同一调整口径；代码-公司映射准确；证券状态/停牌审计 | 滞后可拦截发布；错误身份/复权会改变趋势和位置解释 | 日期 10/14 VERIFIED；身份/复权 PARTIAL 或 FAILED 个案 |
| 哪些标的可以买/应持有/应卖？ | 当前 Daily 不生成该输出；最终日报 `scope=observation_only` | 若未来上线，需策略事实 + 实际持仓/现金/数量/成本/交易流水、价格/复权/费用及账户映射 | 账户真实持仓与候选资产 | 事件/日频、按成交及对账时点 | 无实际账户源；只有回测配置/假设 | UNKNOWN（尚无账户/经纪商接入） | 账面持仓与成交对账；净值和价格时点一致；成本、未成交订单与 corporate action 可审计 | 缺失会使组合 advice 不成立；当前没有实际持仓决策信号可受影响 | 未实现（规划） |
| 利率、汇率、信用、宏观和商品现货发生什么变化？ | 当前 Daily 无直接宏观/现货输出 | 国债收益率/政策利率、汇率、货币信用、铜铝金现货/期货、海外指数及相应时点 | 目前未纳入的宏观和商品市场 | 按业务规划，非当前 Daily 必需 | 无 | UNKNOWN（未接入） | 应先定义投资问题、发布时间、基准和可复核历史，再确定供应商 | 不影响当前观察日报；不能将 ETF proxy 解释为直接宏观/期货结论 | 当前未实现；P2 研究候选 |

### 主题、Core/Quality/Tactical 与地区范围

- `config/theme_registry.yaml` 当前注册三个主题：AI 基础设施、中国汽车全球化、高现金流资产；`high_cashflow` 标记 `maturity: PARTIAL`。高端装备、周期资源等是配置注释中的未来/未启用方向，不是当前 Daily 策略主题。
- 当前 Daily 将主题和 Tier 用作观察资产身份，不做 Tier 确认、Selection 或 Action。Core/Quality 是旧投资意图配置，不是新的 Portfolio Allocation；Daily 输出没有 Tactical 决策层。
- 港股仅通过固定池中 2 个个股代码进入 10/14 股票行情；观察组 `research_observations.yaml` 中额外的港股资源标的属于研究篮子/观察，不等于当前 Daily 股票样本。美股缓存/API 支持代码存在，但当前 Daily 固定池没有美股有效样本。

## 4. Supplier Inventory

### 供应商 / 工具 / 本地缓存边界

| 名称 | 在链路中的角色 | 当前 Daily 使用 | 真实上游与证据边界 |
|---|---|---|---|
| 东方财富 / Eastmoney | ETF Master、ETF 历史行情；A 股历史行情；沪深300指数行情的接口路由 | 是 | 是行情分发平台；交易所/基金公司原始行情的具体合同/共同底层未见凭证，记 **UNKNOWN**。10/14 日志证明平台路由被请求，不证明其长期稳定 |
| 新浪 / Sina | ETF historical fallback；A 股股票行情回退 | 是 | 不同于 EM 的接口/分发平台；其价格生产与 EM 上游是否独立未核验，记 **UNKNOWN** |
| 申万研究 / SWSResearch | 申万指数分析日线、历史日线与实时行情 | 是，关键价格源 | 代码通过 AKShare `index_analysis_daily_sw`、`index_hist_sw`、`index_realtime_sw`。API 归属 SWSResearch 可由接口/日志确认；其原始市场数据采购及服务 SLA 未知 |
| 同花顺 / THS | ETF 列表初始化交叉核验；行业板块成交额/量的 best-effort 增强 | 有限/非核心 | ETF Master 的 `cross_validated` 标记有 1,551/1,554 为 true；行业板块是 THS 自有体系，仅用于增强字段，不能提供申万 canonical close |
| AKShare | Python 数据访问 SDK/接口适配层 | 是 | **不是已确认的原始数据生产方**。`ak.*` 函数名称不能单独证明最终 upstream、授权、SLA 或独立性 |
| AKsignal 本地缓存 | 数据副本/加工中间层 | 是 | `data/etf_signal/raw/`、`data/raw/`、`data/processed/`、`outputs/stock_metrics/` 等。缓存不会成为独立供应商；部分旧行情行没有可随行核验的 source/adjustment provenance |
| 上交所（SSE） | 公告交易日历来源 | 日期控制使用 | `config/a_share_calendar.json` 记录交易日日期、公告来源 URL 与覆盖期；不是 OHLCV 行情源 |
| Legulegu | 申万行业代码、名称、父行业、成分数等 Master 初始化；另有行业成分网页/缓存 | Daily 读取本地 Master；当前 Daily 不消费逐行业成分股名单 | `fetch_industry_master()` 使用 AKShare `sw_index_second_info`；现存 `industry_master.csv` 有 131 行。既有审计将页面来源识别为 Legulegu；行业版次、刷新频率、数据更上游及 SLA UNKNOWN |
| AKShare `stock_hk_daily` | 港股历史行情适配函数 | 10/14 固定池使用 2 个 HK 代码 | 记录为 `source=default`，代码不带明示 provider/adjust 参数，真实上游 **UNKNOWN** |
| Tushare Pro | 用户提供的已结束候选验证 | 未纳入 | Token 权限不足，属于候选路径当前 **FAILED（权限）**，不是现行供应商；不复测 |
| 通达信研究行业 / pytdx | 不同行业分类系统的候选或工具 | 未纳入 | 用户已给定其与申万属于不同体系；不能冒充申万指数数据。pytdx/yfinance 未发现为当前 Daily 直接来源 |

### 当前供应商质量评估

下表列序为：**覆盖 / 准确身份与口径 / 时效 / 历史连续性 / 近期稳定性 / 成本与权限 / 上游独立性 / 自动化**。状态按“VERIFIED、PARTIAL、UNKNOWN、FAILED”解释；UNKNOWN 不等于不可用。

| 分发源 | 证据与状态评价 |
|---|---|
| EM（ETF/股票/HS300） | **覆盖 PARTIAL**：ETF 10/14 目标日成功 1,551/1,554；CN 个股当日 EM 3 次失败。**准确 PARTIAL**：代码与名称可取，但 ETF Master 中 1,554 个 `tracking_index`/`tracking_index_code` 均空，股票复权历史有不一致。**时效 PARTIAL**：能返日线，但存在失败与回退。**连续性 PARTIAL**：本地历史广泛存在，未证明所有标的持续无缺口。**稳定 FAILED/PARTIAL**：10/14 ETF EM 10 次失败、0 成功并触发熔断；个股 3/3 失败。**成本/授权 UNKNOWN**：没有账单/合同记录，不能据公开接口推断免费且可商用。**独立性 UNKNOWN**：与新浪平台不同但上游无证明。**自动化 PARTIAL**：有重试、熔断，仍有缺口 |
| Sina（ETF/股票） | **覆盖 PARTIAL**：10/14 ETF 1,551/1,554 有更新；股票审计记录 56 个 CN 标的成功。**准确 PARTIAL**：能返回 OHLCV，但未证明全历史调整口径一致。**时效 VERIFIED（样本日）/ PARTIAL（长期）**：目标日成功案例明确，不代表 SLA。**连续性 PARTIAL**：缓存历史存在，新增/边缘代码存在失败。**稳定 PARTIAL**：在 EM 熔断后承接 1,544 只并发 ETF 请求，最终 1,551/1,554 目标日覆盖；另有 6 次请求失败。**成本/权限 UNKNOWN**。**独立性 UNKNOWN**。**自动化 PARTIAL**：有限并发和重试存在 |
| SWSResearch（申万） | **覆盖 VERIFIED 10/14；FAILED 10/15**：前一交易日 124/124；后续目标日 0/124。**身份 PARTIAL**：代码以 `801xxx.SI` 落地，Master 有 131 代码/124 active，但未存明确“申万2021版”版次字段。**时效 FAILED（10/15 实际证据）**：目标日主源无数据，实时无可独立核验价格日期，历史补取仍 0/124。**历史 PARTIAL**：行业 CSV 覆盖多年，但无全体缺口与版次审计。**稳定性 FAILED（该目标日）**。**费用/权限 UNKNOWN**。**独立性 FAILED（现有回退链）**：主、历史、实时路由均在申万体系，不能视为互相独立供应商。**自动化 PARTIAL**：失败会停更/阻止发布，而非静默通过 |
| THS | **覆盖 PARTIAL**：ETF Master 初始核验有记录；行业量额映射只覆盖部分行业（代码注释中约 78/124）。**准确性 PARTIAL**：其 ETF 清单可做 cross-check；THS 行业不是 SW 行业。**时效/历史 UNKNOWN**（仅保留当前运行需要的证据）。**稳定性 UNKNOWN**。**成本/权限 UNKNOWN**。**独立性 PARTIAL**：平台不同，但行业品种体系不同；不可作为 SW 价格备份。**自动化 PARTIAL**：best-effort 增强不阻塞核心价格 |
| Legulegu（行业 Master） | **覆盖 PARTIAL**：本地缓存含 131 条行业 Master，124 条 active。**准确性 PARTIAL**：代码/名称/父行业存在，但版次字段和生效日不明确。**时效 UNKNOWN**：Daily 消费缓存，不做每日 Master 刷新。**历史 UNKNOWN/不适用**：当前不是 OHLCV 历史序列来源。**稳定性 UNKNOWN**：未见近期独立抓取日志。**成本/授权 UNKNOWN**。**独立性 PARTIAL**：行业身份发布端与 SWS 行情端不同，但不能作为价格备源。**自动化 PARTIAL**：接口通过 AKShare 初始化，后续依赖本地缓存 |
| HK provider（通过 AKShare） | **覆盖 PARTIAL**：10/14 两个 HK 行情代码请求成功，但其中一个代码身份错误。**准确 FAILED（02510 错配）/UNKNOWN（02525 新代码）**。**日期 VERIFIED（10/14 样本）**；**历史/稳定性/成本/独立性 UNKNOWN**。**自动化 PARTIAL**：能被读取，但没有显式 provider 与复权参数审计 |

## 5. Coverage & Quality

### 5.1 最新正式批次覆盖

| 数据域 | 分母与观察值 | 结论 |
|---|---|---|
| ETF Universe | `etf_master.parquet`：1,554 个唯一 ETF 代码；THS/EM cross-validation 标志 1,551 true | 代码清单有一次交叉核验痕迹，不等于逐日行情/身份/跟踪标的持续双源校验 |
| ETF 目标日行情 | 10/14 raw：1,551/1,554 有目标日价格；3 缺失 | 99.8% 左右的分区覆盖，正式报告保留缺失警告；这不等于所有 1,551 个价格口径通过核验 |
| ETF RPS | `rotation_20261014.parquet`：1,551 行，RPS15 有效 1,501、RPS1 有效 1,501、ΔRPS15 有效 1,500；20 行标记 `corporate_action` | 与报告 1,331 “ETF 趋势样本”口径不同：后者是已合并到 Observation 趋势/均线的样本，不能把 1,331 当 Master 分母 |
| ETF 活跃榜 | 正式摘要 1,331 样本、212 活跃 | 已实现的定义为价格>MA20/MA60 且两个斜率为正；不是买入推荐 |
| 跨资产代理方向 | 10/14 `asset_directions`：A股宽基 166、A股行业/主题 375、港股 142、海外权益 20、商品/黄金 15、现金/货币 50、其他 563；**债券 0 行** | 以上为 ETF 产品分类数，不是相应基础资产的覆盖数量；“其他”占 563/1,331，方向解释存在明显未知类 |
| ETF 位置 | 正式产物 `etf_positions_20261014.parquet` 1,331 行；`bottom_change=UNKNOWN` 441 行 | 大量位置变化不可比，不能将其解释为状态未变化 |
| 申万行业 | 10/14 日志 `analysis_daily` 124/124，正式 `industry_rotation` 124 代码且 source 为 `swsresearch_analysis` | 这一日来源清晰、全覆盖；不能外推到下一日 |
| 申万后续目标日 | 10/15 freshness probe 无主源数据；realtime 价格缺独立交易日期；历史源 124/124 未补成。日志最终 0/124 verified target | **明确已验证的运行阻断**；磁盘可能存在带 10/15 分区标签的旧/实时行，但不满足源日期验收，不能计为覆盖 |
| 固定池个股趋势 | 10/14 stock metrics 60 个注册身份、58 个唯一代码，60/60 `current`；CN 56 + HK 2 | 日期覆盖通过不等于身份和复权通过；同一股票跨主题身份重复注册，行情请求按唯一股票去重 |
| 股票复权审计 | 10/12 对 58 只做缓存与当前参考序列比对：55 `MISMATCH_OR_INCOMPLETE`、1 `MATCH_ALTERNATE_QFQ`、1 `MATCH_UNADJUSTED_NOT_QFQ`、1 `MATCH_CURRENT_QFQ` | 只有 1 只被该报告标为当前 QFQ 匹配，不能将全池行情视为已统一验证。全体 58 只长期低位结论未改变仅是该次样本结论，不抵销价格口径风险 |

### 5.2 时效性、日期与失败语义

- 10/14 正式日报由 10/15 上午生成，申万主源记录 10/14 交易日，行业更新 4.8 秒内保存 124/124；这证明该批次可完成，不证明 SWSResearch 每日按时。
- 10/15 批次在 10/16 上午拉取时，申万主源没有目标日期，realtime fallback 不带其价格的独立源端日期；改过的验收逻辑不再用本地赋值日期冒充源日期，而转入历史源，最终仍缺 124/124。用户已给定的 AKShare/Tushare/通达信验证结果本报告直接沿用，不再重跑。
- `src/daily_run.py` 对行业目标日期 124/124、股票 `source_trade_date` 和 HS300 benchmark 日期做严格验收；缺行业日期将停止批次。这个门控是正确的可信度保护，但当前上游使日常服务可用性不足。
- 10/14 股票 audit 中记录 3 次 EM 失败后由 Sina 成功；HK 两个代码记录为 `default`。股票数值列不保留完整 provider/adjustment-anchor provenance，审计 JSON 才保留本次抓取来源。

### 5.3 身份、分类和价格基准

1. **ETF 跟踪身份未落地**：ETF Master 的 `tracking_index` 和 `tracking_index_code` 填充值均为 0/1,554。日报用基金名称和关键词判断宽基/行业/海外/商品/货币等，不能确认 ETF 实际跟踪指数、指数编制版本或持仓暴露。Master 有 650 只 `asset_bucket` 为空；正式趋势样本有 563/1,331 未分类。跨资产报告中的海外权益、商品/黄金等已分类产品是 ETF proxy，不是完整市场暴露覆盖；10/14 报告没有债券方向行。
2. **ETF 行情是基金二级市场收盘价**：系统用 ETF 自身 close 算 RPS、趋势与价格位置，不直接读取指数现货/NAV、跟踪误差或持仓。买卖报价、IOPV/折溢价等有 Master 字段但并未作为现有观察主指标。
3. **申万行业版次没有显式字段**：行业代码以 `.SI` 形式落盘，active snapshot 为 124 个代码；Master 元数据由 Legulegu 取得/缓存，行业价格来自 SWSResearch，但 schema 没有明确的 edition/version 与可审计同步日期。不能仅由接口名推断已被机器强制固定为某一申万版本。
4. **个股复权是已验证风险**：A 股三个适配路由请求 `qfq`，但增量抓取会把供应商当前返回段并入旧序列；缓存没有 adjustment factor、复权基准锚点或历史重标记证据。港股 `stock_hk_daily` 调用未显式传 adjustment 参数。10/12 审计明确指出这些限制，并且正确禾赛 HK 02525 的历史仍待核。
5. **证券身份错误已经发生**：配置曾将 HK 02510 当作禾赛，实际为德翔海运；10/15 配置已改为 HK 02525，但原缓存/10/14 行情不是禾赛价格，正确代码的价格覆盖/历史复权还未验证。10/14 正式报告已将错配行排除正文并警告。
6. **行业成分/结构不构成当前 Daily 输入**：`constituents.py` 有 Legulegu 抓取与本地缓存；当前 `daily_run` 不调用它。THS 的行业成交额/量只属于可选增强，不能生成申万指数 close 或 SW 组成映射。

### 5.4 重复、共享和供应商稳定性

- **重复分发，不等于独立原始数据**：ETF 行情有 EM 主、Sina 备；股票接口有 EM/Sina/Tencent 路由。不同平台是合理故障回退，但真实数据是否来自独立交易所直连、是否共享底层数据供应链，目前均 UNKNOWN。
- **申万价格单点**：`index_analysis_daily_sw`、`index_hist_sw`、`index_realtime_sw` 是不同接口形态，仍处于 SWSResearch/申万来源族。THS 提供的是自有行业分类增强；Tushare 权限失败，通达信行业体系不同。现有回退数量多，但对“申万2021指数价格”的真实冗余为 **1 条**。
- **非核心源不应重复维护**：AKShare 是多个源的 Python 适配器；Legulegu 成分抓取和 `common.market_data` 的 CN 多源函数不是当前 Daily 价格主链的新供应商。无需为了形式上的多源而纳入 Daily。
- **ETF 失败证据**：`source_audit_20261014.json` 记录 EM 10/10 失败并开熔断，Sina 1,551 成功 / 6 失败，最终 1,551/1,554；同日 ETF update 日志显示 1,544 个残余代码切 Sina 8-worker 批量。该样本说明回退有效，也说明“曾经跑通”不能视作长期 SLA。
- **行业源稳定性证据**：相邻正式交易日的 124/124 与 0/124 对比，以及 10/15 updater 状态，显示 SWS 发布时点/可达性是实际脆弱点，而非理论上的供应商风险。

## 6. Critical Gaps

| 优先级 | 缺口 | 影响范围 | 依据/判断 |
|---|---|---|---|
| **P0** | 申万2021口径行业价格缺少真正独立的目标日备用源 | Daily 依赖 124 个行业事实、RPS 与行业方向发布 | 10/15 已核实 0/124；所有现存 SW 价格路由仍依赖同一申万来源族。THS、通达信不能直接替代 |
| **P0** | 申万目标日数据不可用时，124 个行业观察事实无法交付；该日只能以 `PARTIAL` 发布其他模块 | 行业轮动观察连续性 | 10/15 日志显示 0/124；改进批次将缺行业模块显式置 unavailable，不使用旧日期或部分横截面。独立价格备用仍未建立 |
| **P1** | 固定股票池复权序列无统一且可追溯的调整锚点 | 股票收益、趋势分、历史位置 | 10/12 58 只审计中 55 行情被标为 mismatch/incomplete；代码按增量拼接，adjustment factor/anchor 未保存 |
| **P1** | 股票证券身份映射曾经错误，正确港股代码尚未验价 | 港股固定池个股趋势与底部位置 | HK 02510→02525 修正证据；旧缓存不能当新代码价格。现有报告已排除旧身份，但新代码未完成覆盖与历史审计 |
| **P1** | ETF 分类覆盖与产品身份数据不足 | 大类资产方向、ETF 主题映射与代理解释 | Master 650/1,554 未分桶；10/14 趋势样本 563/1,331 未分类；Master 0/1,554 有跟踪指数名称/代码 |
| **P1** | 不同平台的日线回退可能混合口径，缓存缺 source/adjustment provenance | ETF/股票复权与跨日收益的可信度 | HK 未显式 adjustment；A 股历史增量拼接无重标定证据；ETF fallback 确认日期但不证明历史复权一致 |
| **P1** | ETF 报价不代表跟踪指数/NAV，海外/商品/债券只是产品代理 | 将“资产方向”理解为资产市场表现时 | `etf_trends` 用 ETF close；tracking_index 全空，未观察 NAV、基差或跟踪误差 |
| **P2** | 直接宏观/外汇/商品和组合账户状态缺席 | 未来宏观配置、商品周期、实际组合建议 | 当前未接入且不属于 Daily 主链刚需；不要扩张成 P0 |

## 7. Target Source Portfolio

原则：**每类金融事实只设一个正式口径；备用源只在身份、版次、日期、复权全部可对齐时纳入。AKShare 不能作为供应商计数。**

| 能力 | 是否独立需要 | 现有源是否够 | 推荐主源 | 备用源 / 是否独立 | 付费与现在投入 |
|---|---|---|---|---|---|
| 市场行情：A 股 ETF/股票、沪深300 | 需要价格能力；ETF/股票可共用少数行情平台，但应区分 asset class 的复权合同 | 覆盖广、日线回退已实际使用；但有 10/14 缺口、股票历史复权差异 | 暂保留 EM 为首选，按资产类型固定单一历史调整口径；将 Sina 作为运行回退 | Sina 是不同分发平台、操作上可回退；**底层上游独立性 UNKNOWN**，不能对外宣称已证明双供应商独立 | 当前没有必须增加供应商的证据；商业授权/SLA UNKNOWN。优先投入数据口径与来源审计，不加第三路 |
| 行业基准：申万行业指数及成分 | **需要与股票行情分开定义**：行业信号必须保持申万指数身份 | 主源可在 10/14 覆盖；10/15 已失效；当前不够满足连续可靠运行 | 价格保留 SWSResearch canonical 主源；身份 Master 暂用 Legulegu 缓存并补齐版次/生效日 | **必须找一条对同一 2021 申万指数代码/版次真正独立的价格备用**。当前无合格源；THS/通达信均不算。Tushare 权限不足不是可运行备源 | 这是当前最值得投入的外部源审计/权限采购；价格、许可、历史长度与 SLA UNKNOWN，先验证后签约/上线 |
| 宏观经济 | 目前不需要独立源 | 不适用；Daily 无宏观字段 | 暂不引入 | 无 | 不值得现在投入。只有确定利率/汇率/信用指标将如何改变策略问题后再评估 |
| 商品价格 | 当前趋势观察通过商品/黄金 ETF 产品代理已足够描述权益市场对商品的交易 | 对商品现货/期货供需、期限结构、库存不够；但这些不是当前 Daily 问题 | 继续使用现有 ETF 行情作为产品代理，并在报告/数据字典标注“ETF proxy” | 暂无 | 不新增铜、铝、金现货或期货订阅；扩展需另立研究需求与预算 |
| 证券主数据：代码、身份、分类、成分关系 | 当前需要最小的可审计映射，不需另搭完整主数据平台 | ETF 名称分类覆盖不足；HK 错码已发生；industry edition 未显式持久化 | 近期维持固定池配置 + EM ETF Master；申万身份沿用 Legulegu cached Master 并标注版次/有效日；将交易所/发行人公告作为身份核验依据 | THS 可继续做 ETF Universe 差异提示，不承担标准主数据；Legulegu 成分表仅在需要行业穿透时再审时点/用途 | 先修复身份核验及 tracking index 元数据质量；暂不引入大型主数据产品，付费需求 UNKNOWN |
| 投资组合：持仓、成本、交易 | **未来 Portfolio State 上线后必须独立于价格源**，因为真实账户状态不能从行情推断 | 当前完全没有实际持仓/成本/交易流水接入 | 暂无 Daily 主源；未来优先经纪商结单/导出与人工对账的可审计账户事实 | 备用不是行情供应商，而是成交回单/账单核对流程 | **现在不接入**，因为 Daily 尚无 Portfolio Advice；在 Portfolio State 需求与权限边界定稿后另行采购/实现 |

### 最少供应商的明确回答

1. **当前实际最少保留**：Eastmoney + Sina + SWSResearch 三个平台路由；交易日历保留上交所公告来源。SSE 是静态日历事实，不应与行情供应商数混在一起。
2. **必须建立真正备用**：SWSResearch 的 2021 申万行业价格路径。目标应是独立授权的数据提供者提供同一行业身份、带源交易日期、可回放历史；候选商与费用尚未验证，不能提前点名或将失败候选算作备源。
3. **市场行情备用**：运行上保留 Sina 已有回退；对“独立上游”只给 UNKNOWN。若之后尽调证明 EM/Sina 共用上游，再补充不同源，但不先增加供应商数量。
4. **不需要现在新增**：THS 行业行情不能替代 SW；Tushare 权限现状不允许纳入；通达信行业口径不等价；pytdx/yfinance 不是当前 Daily 供应商；Legulegu、宏观、铜铝现货/期货和账户数据源均不应因“可用”而进入当前主链。

## 8. Prioritized Actions

### P0｜保住已上线 Daily 的行业事实可信度

1. **先解决行业独立备用能力**：定义“申万2021版 124 二级行业”精确 code/name/version 合约，再对候选供应商验证 10/15 这种目标日、带源交易日期、全 124 行业、历史连续性和停牌处理。验证通过前保持现有拒绝发布门，不允许把无日期 realtime 或旧缓存提升为 target-day。
2. **核验主源实时服务窗口与交付 SLA**：记录 SWSResearch 每日首次出现目标日期、全行业完整覆盖时间、失败类型与恢复时间。10/14 的成功和 10/15 的失败分别保留，按多交易日衡量，不按一次调用成功判断稳定。
3. **为正式 industry master 固定版次**：保存生效日期、industry edition、active code set 的版本/哈希；行业代码改变时明确差异审阅，避免旧 code 继续作为同一横截面样本。

### P1｜影响行情解释和事实可信度

1. **固定股票价格基准合同**：逐市场声明 raw/unadjusted、前复权算法及调整基准时点；增量拼接时验证历史调整重标定，保存 source、adjustment、anchor、source_trade_date。未有可复核凭证前，股票 `price_basis_verified` 维持 false。
2. **收尾港股身份修复**：移除旧 HK 02510 作为禾赛事实的消费资格；对 HK 02525 做目标日期、公司名称、上市主体和历史连续性核验。当前 10/14 报告中的 02510 数据不得用来说明禾赛走势。
3. **补齐 ETF 身份/分类可观测性**：先处理 650 个空分类和 563 个日报样本未分类；将 Master 的 tracking index 名称/代码补成可追溯事实（标注来源/核验时间），把产品名称关键词匹配作为未核验推断，不当作交易所/基金主数据。
4. **隔离历史序列来源切换**：主/备供应商的日线拼接至少对 OHLC、trade_date、复权口径、重复日期和大幅跳变做同一验收；在 source provenance 未落缓存前，不能据“当前缓存日期齐全”认定历史可信。
5. **对外清晰标识 proxy**：资产方向报告将 ETF 产品二级市场价格明确为“ETF proxy”，不表述为底层指数/NAV/商品现货。主链当前的行业结构/成分穿透不在 Daily；不要把旧代码产物写成当天已完成。

### P2｜仅在业务范围扩展后评估

1. 只有策略明确要使用利率、信用、外汇、经济指标或商品期货状态时，再建立单独业务数据需求、数据时点和来源评估；当前不采购。
2. 只有 Portfolio State/Advice 进入正式开发范围后，再建立持仓/现金/成本/成交回报源和对账流程；现有 `portfolio.yaml` 资金假设及回测持仓不得代替实际账户数据。
3. 宏观、商品或海外市场更丰富的独立指数数据属于研究增强能力，不是当前 Daily 的 P0/P1 修复项。

## 审计证据索引

- 当前范围与架构：`docs/ARCHITECTURE_MAP.md`（尤其 §§“当前实现阶段”“保留的八项能力”“每日运行与交付”）。
- Daily 编排、门控：`Makefile`；`src/daily_run.py`；`src/observation/cli.py`；`src/observation/report.py`。
- 最新正式日报与验收：`outputs/observation/observation_20261014.json`；`outputs/run_day/final_check_20261014.json`；`outputs/run_day/publication.json`。
- 2026-10-14 行业主源记录：`.run_day/runs/20261014/dbb8ef58b6654946b5277c427e2ed00d/logs/02-attempt-1.log`。
- 2026-10-15 申万源缺失/0/124：`.run_day/runs/20261015/efe7529233134ecb978e434c1396184a/logs/02-attempt-1.log`；`data/raw/sw_industry/update_status.json`；`data/raw/sw_industry/update_checkpoint.json`。
- ETF source failure/fallback：`data/etf_signal/diagnostics/source_audit_20261014.json`、`source_audit_20261015.json`；对应 10/14 batch `01-attempt-1.log`。
- 个股来源与复权：`outputs/stock_metrics/stock_update_audit_20261014.json`；`data/audits/stock_price_basis/20261012/{summary.json,report.md,reconciled.json,identity_issues.json}`；`config/observation_identity_issues.json`。
- 代码与配置：`src/etf_signal/data_source.py`、`src/common/market_data.py`、`src/trend_engine/data_provider.py`、`src/trend_engine/fetch_data.py`、`src/sw_industry_rps/data_source.py`、`src/sw_industry_rps/source_status.py`、`config/market_data.yaml`、`config/industry_data.yaml`、`config/etf_classification.yaml`、`config/theme_registry.yaml`、`config/selection_universe.yaml`、`config/a_share_calendar.json`；行业 Master 来源参考既有 [SW data-source audit](../../SW_INDUSTRY_RPS_DATA_SOURCE_AUDIT.md)，其在线验证日期早于本报告，故不作为当前可用性证明。
