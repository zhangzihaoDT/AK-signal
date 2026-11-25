> 2026-10-16 行业正式集成：TDX Research Industry 固定 128 二级行业；官方 .day → 独立价格库 → 自算收益/RPS → Daily Observation。SW2021 仅历史研究，无备用回退；更新失败 UNAVAILABLE，窗口不足 INSUFFICIENT_HISTORY。详见 docs/TDX_INDUSTRY_INTEGRATION.md。

# AKsignal Canonical Architecture Map

> 当前能力的 canonical 状态图。职责收敛版本：2026-11-25；应用版本仍以 `VERSION` 为准。

## 完整定位与项目目标

**AKsignal = Financial Data Assets & Research Platform（金融数据资产与专题研究平台）。**

核心职责为 Collection、Data Assets、Serving、Research：采集金融数据，清洗/标准化/治理并长期沉淀，向下游提供可追溯、稳定、可复现的数据文件，并基于数据资产生成市场观察及专题研究。GridTrading 独立承担策略、回测、交易信号、账本和自动化；AKsignal 不建设独立策略运行体系。

```text
AKsignal
├── Collection      来源接入、增量采集、历史补齐
├── Data Assets     身份、标准化、质量 Gate、来源与快照追溯
├── Serving         本地可复现数据文件、清单与报告交付
└── Research        Market Facts、正式 Basket Compare、专题研究

GridTrading ─────── 策略开发、回测、交易信号、账本与自动化
```

数据资产与市场事实可被下游消费，但不生成或托管交易动作。Holdings Dashboard 仅观察个人资产与持仓事实，不下单、不管理交易账本。

| 职责 | 回答的问题 | 核心产出与边界 |
|---|---|---|
| Collection | 来源是否可持续、输入是否可追溯？ | Longbridge、CSIndex、CFFEX、TDX、ETF来源采集及历史补齐 |
| Data Assets | 数据身份、口径、覆盖和质量是否明确？ | 标准化行情、版本/Manifest、质量状态、来源证据和历史快照 |
| Serving | 下游如何稳定复现输入？ | 本地文件、固定路径清单、快照摘要；目前没有独立服务/API |
| Research | 数据描述了什么市场事实？ | 统一日报、Basket Compare、Holdings Dashboard 与专题研究；不输出交易 action |

**市场事实与交易策略严格分离。** GridTrading 不应依赖 AKsignal 可变内部目录；跨项目输入应通过明确的日期、口径、版本和质量契约冻结。本轮只审计并设计文件契约，不部署服务。

## 必须坚持的原则

**AKsignal 负责金融数据资产与研究事实；GridTrading 负责策略和执行。**

该边界避免重复建设、策略建议覆盖事实，或个人持仓报告被误认为交易执行系统。

## 模块数据来源更新（2026-10-16，优先于后文旧来源描述）

个股生产入口已指定长桥 OpenAPI / ForwardAdjust，趋势、收益率和价格位置共用长桥快照，沪深300基准同源，禾赛暂排除；不使用旧EM/Sina个股CSV回退。ETF继续独立使用东方财富/新浪，逐序列记录实际来源与价格口径。行业轮动指定TDX Research Industry，官方.day历史基座与增量已接通，固定128个二级行业，AKsignal独立计算收益率和RPS；申万仅作历史研究，不再由Daily执行或替代TDX。三个模块不跨来源备份，不强制分类对齐。配置及运行边界见 [OBSERVATION_DATA_SOURCES.md](OBSERVATION_DATA_SOURCES.md)。

## 当前实现阶段

当前每日主链采集/计算全市场 ETF 事实、Longbridge 固定观察池个股事实、核心指数、CFFEX 合约及 128 个 TDX 研究行业，并发布统一日报。正式 Basket Compare 提供主线结构、相对强弱、参与度与重叠诊断；Holdings Dashboard 与 CSI500/IC500 为专题观察/研究页面。独立策略、回测、交易信号、账本与自动化不属于 AKsignal 能力。

**当前正式实现限于数据资产、市场观察和专题研究。** 交易策略与组合执行由 GridTrading 负责；AKsignal 不恢复旧 Selection、Timing、ETF Rotation、SW RPS 或旧回测链。

主题和 tier 当前仅保留观察分组与注册身份；同一股票多个身份不压成单标签。

## Market Facts 的四类内部能力

- **Data Foundation｜数据基础**：①固定池股票 · ②全市场 ETF · ③全行业指数。
- **Trend｜趋势观察**：④行业轮动 · ⑤大类资产 · ⑥ETF 趋势 · ⑦个股趋势。
- **Price Position｜价格位置观察**：⑧ETF 与个股历史价格位置；这属于 Market Facts，不是 Portfolio / Position 仓位管理。
- **Group Activity｜分组启动观察**：⑨9 个研究分组的趋势启动状态、事件来源与成员线索；只提示阅读顺序，不输出交易动作。
- **Daily Observation Report**：统一日期 · 统一数据质量 · 统一发布，消费上述事实，不新增投资决策。

## 当前能力清单

| # | 能力 | 模块 / 输入 | 产物 |
|---|---|---|---|
| 1 | 固定观察池个股数据获取与整理 | market_facts.stock_daily；长桥；selection_universe.yaml观察池 | data/sources/longbridge/raw/daily；stock_update_audit_{date}.json |
| 2 | 全市场 ETF 数据获取与整理 | etf_signal 数据获取/主表/分类 + market_facts.etf_observation 横截面事实 | data/sources/etf_em_sina/raw、master、daily、diagnostics |
| 3 | 行业指数数据获取与整理 | TDX Research Industry固定128二级行业；官方.day基座+增量 | 精确目标日门控；申万仅历史研究 |
| 4 | 全行业轮动观察 | 独立收益/RPS已接通；不以申万替代 | observation/industry_rotation、industry_directions；失败显式unavailable |
| 5 | 大类资产趋势观察 | ETF 分类与横截面聚合；通过 ETF 代理观察 | observation/asset_directions |
| 6 | ETF 趋势识别与活跃池 | RPS 原值、价格 / MA20 / MA60 / 斜率 | observation/etf_trends |
| 7 | 观察池个股趋势评估 | 长桥快照 → 现有趋势纯计算，不输出action | stock_metrics/stock_metrics_{date}.parquet |
| 8 | ETF 与观察池个股底部位置判断 | ETF 复用 price_map；股票独立 stock_position_v1 | observation/etf_positions、stock_positions |
| 9 | 分组趋势启动观察 | market_facts.group_activity；Compare 分组与子组权重 + 同批长桥股票/沪深300 | observation/stock_group_activity_{date}.json；日报 04 |
| 10 | 固定核心指数观察 | market_facts.core_indices；core_indices.yaml；长桥NoAdjust / 中证官方价格指数 | observation/core_indices_{date}.json/parquet/csv；日报01固定8宽基+1风格参照 |
| 11 | 衍生品观察 | market_facts.derivatives；derivatives.yaml；中金所官方日CSV | observation/derivatives_{date}.json/parquet/csv；日报01核心宽基之后 |

核心指数观察独立于 ETF 产品池，全部注册行固定展示，缺失保持 UNAVAILABLE，模块降级进入 PARTIAL。口径与验收见 [CORE_INDEX_OBSERVATION.md](CORE_INDEX_OBSERVATION.md)。

上述 observation 文件均按 trade_date 输出 parquet + CSV。位置只描述历史收盘价区间位置，不断言未来不会继续下跌。股票口径与可靠性限制见 [POSITION_OBSERVATION.md](POSITION_OBSERVATION.md)。

ETF 活跃的观察定义：price > MA20、price > MA60、MA20_slope > 0、MA60_slope > 0；均线缺失不视为活跃。不输出 BUY / WATCH / Timing。行业阶段沿用已有 Observation 三分类。ETF 与行业 RPS 横截面不同，不直接比较。

## 每日运行与交付

唯一完整每日入口为 `make run-day`，由 src/daily_run.py 编排：

```text
etf update → etf calculate
  → observation stock-metrics（长桥个股与基准，同批位置）→ observation build → observation check
  → validate_release → 统一发布
```

`make run-day-offline` 跳过在线 update，个股仅读缓存。所有步骤固定目标 trade_date；核心股票与基准未对齐时停止发布。ETF 缺口显式列入审计。

每日只有一个报告入口：`outputs/observation/observation_{date}.html`，附同名 JSON 摘要与 Markdown。正文前置“今日市场速览”，其后按 01 市场环境（核心指数、IC）、02 行业轮动、03 ETF（趋势、位置、分类）、04 研究主线、05 数据质量组织；趋势和位置归入对应资产，各板块注明来源。默认字段、详情与审计边界由 [日报信息架构与展示契约](DAILY_INFORMATION_ARCHITECTURE.md)（`src/observation/contract.py`，`daily_display_contract_v1`）固定；个股按8个研究篮子加高现金流分类，覆盖数据质量、复权验证与来源状态集中在 05 的异常摘要，完整来源清单进入技术详情。机器事实全量落盘。ETF 活跃定义沿用均线条件，个股活跃展示趋势分≥70，完整固定池可展开；这只是展示范围，不创造推荐资格。

最终验收为 `outputs/run_day/final_check_{date}.json`，发布回执为 `outputs/run_day/publication.json`。结果包含 scope=observation_only、status、warnings、errors；不再包含投资 action。

新批次暂存目录从空目录建立，不复制上一批 outputs 中的研究报告或旧候选。验收后切换 outputs 符号链接，上一批产物留在 `.run_day/runs/{date}/{run_id}/outputs`；首次旧 outputs 目录保存在 `.run_day/legacy`。失败不替换已发布产物。检查点、互斥锁、事实摘要、恢复与代码/config 兼容性检查继续保留。代码变化须 --restart。

trade_date 为市场事实分区；run_date / generated_at 仅为审计时间。CONFIRMED / COMPLETE / PARTIAL 是来源与完整性状态，不是推荐状态。本产品不写新的 *_latest 报告别名。

SW 历史研究数据继续保存在 `data/legacy/` 并保留既有来源/日期证据；SW 采集、provisional fallback、行业 RPS 更新和确认流程已经退役，不参与 Daily，也不作为 TDX 的备用源。历史 SW 数据不得替代当前 TDX Research Industry 事实。

在线 --require-complete 人工恢复保留原 run_id，但从第 1 步重新获取数据与验收；其他模式按有效检查点恢复。失败不会自动重试，数据就绪后需面板手动恢复；代码/config 签名变化需 --restart。

模块级降级交付：ETF/固定池股票等有效观察可独立交付；TDX 行业目标日或质量门缺失时，行业事实分区为空，并在日报 summary/final_check 标记 `modules.industry_rotation.status=unavailable`、总体 `status=PARTIAL`。不混入旧日期、不以部分横截面计算 RPS，也不回退到 SW。源数据恢复后可重跑目标日并原子升级发布；Scheduler 接受 PARTIAL 发布但不自动重试。`--require-complete` 保留为显式严格门控。


### AKsignal Daily Scheduler / Run Control

AKsignal 自己的 macOS LaunchAgent（`com.aksignal.daily-scheduler`）拥有 08:30 Asia/Shanghai 定时任务；Service Scheduler 只管理 Service 域任务，不再触发 AKsignal。Scheduler 在计划分钟启动在线 Daily Run；若 Mac 错过计划时点，或批次失败/退出未发布，只记录 `MISSED` / `FAILED` 并等待 AKsignal 面板手动 Run/Resume，不自动补跑、不自动重试。每个交易日继续复用原生 Runner、互斥锁、checkpoint、代码/config 签名与正式发布门控。Scheduler 进程重启与 Daily 批次恢复是独立控制。

`src/daily_scheduler.py` 安装/管理 LaunchAgent，`src/daily_schedule.py` 分离定时检查与人工运行入口，`src/daily_hub.py` 只读提供 Scheduler 与批次状态；Hub Worker 在 AKsignal 域分别暴露 Scheduler 服务控制与 Daily Run 人工操作。首次部署仍需显式安装/启动 AKsignal LaunchAgent；源码存在或面板可见不等于生产定时已启用。字段、操作和切换步骤见 [AKsignal Daily Scheduler](DAILY_SCHEDULER.md)。

## 已退役的策略和旧数据子系统

ETF Rotation 报告、候选筛选、账户映射、候选卡、BUY_CANDIDATE 信号及独立 backtest 已从正式运行链退役。旧 SW 行业 RPS 报告、确认/贡献/结构计算和双源更新链已退役；正式行业观察只使用 TDX。Basket legacy 多源实现已删除，保留正式 Basket Compare V2。

退役不等于删除数据：SW 历史 raw/processed 文件、Manifest、来源证据和历史发布批次继续保留。旧实现由 Git 历史追溯；不可把历史报告或缓存当作当前正式输入。

## 每日展示一致性规则（布局 v10）

排序、分页与展开由 `src/observation/report.py` 统一定义，`observation build` 每日调用同一个 renderer；JSON 同步记录 report_layout_version。仅修改展示顺序，不改原始事实或候选条件。

- 大类资产：固定 CROSS_ASSET_ORDER，保证方向位置每天一致。
- 行业：RPS15降序 → RPS5降序 → 行业代码（缺少代码时名称兜底）。
- 活跃ETF：RPS15降序 → 流动性降序 → ETF代码。
- 研究主线：主线概览 → 9 组一行式速览（趋势阶段、启动状态、观察变化、线索）→ 折叠证据；新增观察线索与已有观察线索分别标识，优先级只决定阅读顺序。组内个股按趋势分 → 20日收益 → 市场/代码排列，允许同股跨篮子展示。确定性展示定义见 [日报04信息架构](DAILY_GROUP_ATTENTION.md)。
- 底部位置：有效进入/离开变化 → 有效长期低位 → 其他有效 → 未知/不适用；同组按360日位置升序，再按身份代码。数据缺口不因位置数值低而排到可靠低位之前。
- 每个面板最多默认20项，只提供一个展开区；默认行与剩余行互斥，每对象每面板只出现一次。无活跃个股时默认区明确为空，展开仍可读剩余固定池。
- 同股票跨注册身份仅在展示层按市场/代码去重；机器事实保留全部注册。跨不同观察区域的同资产允许重复，分别回答趋势与位置问题。
- 搜索覆盖该面板默认与展开的完整列表，搜索时展开剩余区；清空后恢复原展开状态。缺值排最后，同分用稳定身份键打破平局。

UI v3：页面最大宽度1120px；压缩标题、卡片和行间距。取消表格纵向高度限制与内部纵向滚动，使用页面主滚动；小屏仅保留必要的表格横向滚动。展开与排序规则保持不变。

已知身份错配的跨日保护源为 config/observation_identity_issues.json；当日审计可补充覆盖，但不依赖本地审计快照存在。

## 统一事实契约与迁移验证（2026-10-16）

`src/market_facts/` 提供快照、独立日历、试运行审计及已接入Daily的长桥个股入口。历史pilot产物保留production_eligible=false，不追认已完成独立身份/复权审计；生产接入按新的模块来源契约运行，价格依据验证状态继续独立记录。早期试运行记录是历史证据，当前来源边界以 [OBSERVATION_DATA_SOURCES.md](OBSERVATION_DATA_SOURCES.md) 为准。
