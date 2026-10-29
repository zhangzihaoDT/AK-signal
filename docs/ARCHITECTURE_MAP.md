> 2026-10-16 行业正式集成：TDX Research Industry 固定 128 二级行业；官方 .day → 独立价格库 → 自算收益/RPS → Daily Observation。SW2021 仅历史研究，无备用回退；更新失败 UNAVAILABLE，窗口不足 INSUFFICIENT_HISTORY。详见 docs/TDX_INDUSTRY_INTEGRATION.md。

# AKsignal Canonical Architecture Map

> 当前能力的 canonical 状态图。基线版本：v0.7.2。更新：2026-10-25。

## 完整定位与项目目标

**AKsignal = Investment Decision System（投资决策系统）。**

目标是通过可追溯的市场事实、策略信号和真实组合约束，辅助管理投资组合；投资者保留最终决策权。

```text
AKsignal
├── 04 · Portfolio / Position
│   └── 仓位管理 · 持仓监控 · 风险暴露 · 调仓决策
├── 03 · Strategy
│   └── 机会识别 · 信号生成 · 策略验证 · 买卖规则
├── 02 · Market Facts
│   └── 市场日报 · 行业趋势 · 篮子 Compare
└── 01 · Data Foundation
    └── 数据源 · 历史行情 · 增量更新 · 数据质量
```

数据流自下而上：Data Foundation → Market Facts → Strategy → Portfolio / Position。持仓约束与执行反馈经明确接口传递，不回写或伪装底层市场事实。

| 层级 | 回答的问题 | 核心产出与边界 |
|---|---|---|
| Data Foundation | 数据是否完整、可信？ | 标准化行情、来源与质量证据；不生成市场信号 |
| Market Facts | 市场正在发生什么？ | 市场事实与比较结果；不感知 Strategy，不编码策略语义或输出买卖建议 |
| Strategy | 根据事实应该采取什么行动？ | 买卖信号及独立验证依据；消费版本化事实，不维护或重算行情 |
| Portfolio / Position | 结合实际持仓应该如何执行？ | 仓位建议、风险与调仓计划；消费实际持仓和策略信号，不自行生成市场信号 |

**策略信号不等于仓位决策。** Strategy 判断 ETF 出现机会后，Portfolio / Position 可根据实际持仓、行业集中度和风险约束拒绝或调整执行建议。事实、信号、实际仓位、目标仓位和调整量分别记录，互不覆盖。

## 必须坚持的原则

**AKsignal 负责发现和量化市场事实，你负责理解这些事实并作出投资决策。**

该原则约束最终投资决策的责任与事实/建议分离，不排斥 Strategy 提供量化建议。系统辅助真实组合管理；投资者保留最终决策权。

## 模块数据来源更新（2026-10-16，优先于后文旧来源描述）

个股生产入口已指定长桥 OpenAPI / ForwardAdjust，趋势、收益率和价格位置共用长桥快照，沪深300基准同源，禾赛暂排除；不使用旧EM/Sina个股CSV回退。ETF继续独立使用东方财富/新浪，逐序列记录实际来源与价格口径。行业轮动指定TDX Research Industry，官方.day历史基座与增量已接通，固定128个二级行业，AKsignal独立计算收益率和RPS；申万仅作历史研究，不再由Daily执行或替代TDX。三个模块不跨来源备份，不强制分类对齐。配置及运行边界见 [OBSERVATION_DATA_SOURCES.md](OBSERVATION_DATA_SOURCES.md)。

## 当前实现阶段

v0.6.0 完成 **Data Foundation 与 Market Facts 稳定化**；v0.7.0 完成 Basket Compare v2、日报 RPS 三列与跨市场休市验收；v0.7.1 将分组趋势启动观察接入日报 04，v0.7.2 完善为研究注意力简报。当前每日主链观察全市场 ETF、128 个 TDX 研究行业及固定池个股，并发布日报；独立 Basket Compare 提供主线结构、相对强弱、参与度与重叠诊断。Strategy 与 Portfolio / Position 尚未作为正式能力实现。

**当前正式实现限于 Data Foundation 与 Market Facts。** 后续 Strategy 将重新定义策略、信号与独立回测机制；Portfolio / Position 将接入实际持仓、风险约束与调仓决策。旧 Selection、Timing、研究或回测实现不代表新层能力已完成，也不作为新设计基础。

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
| 2 | 全市场 ETF 数据获取与整理 | etf_signal 数据获取 / 分类 / 缓存 | data/sources/etf_em_sina/raw、master、diagnostics |
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

trade_date 为信号分区；run_date / generated_at 仅为审计时间。CONFIRMED / COMPLETE / PARTIAL 是来源与完整性状态，不是推荐状态。本产品不写新的 *_latest 报告别名。

申万历史研究链的价格日期验收（2026-10-16；不再作为Daily来源）：日线主源的源端 trade_date 可作为日期证据；实时兜底必须提供对应价格字段的独立交易日期（最新价 close_trade_date / 昨收盘 prev_close_trade_date），匹配目标日后才允许落盘，并保存 source_trade_date。当前无日期的实时接口不能用于补齐目标日价格，转带日期历史源；历史源也缺数则 updater 停止并报告已核验数量。旧实时缓存、检查点及指标不能仅凭本地分区日期通过验收。更新摘要的覆盖及可用日期均排除未核验实时缓存。计算、Observation 构建与最终验收均检查日期证据；带日期行情的趋势/RPS 数值口径不变。

在线 --require-complete 人工恢复保留原 run_id，但从第 1 步重新获取数据与验收；其他模式按有效检查点恢复。失败不会自动重试，数据就绪后需面板手动恢复；代码/config 签名变化需 --restart。

模块级降级交付（2026-10-16）：ETF/固定池股票等有效观察可独立交付；TDX更新或质量门控失败时，行业事实分区为空，Daily不执行申万industry calculate，并在日报 summary/final_check 中标记 `modules.industry_rotation.status=unavailable`、总体 `status=PARTIAL`、`data_complete=false`。不混入旧日期、不以部分行业横截面计算 RPS。手动重跑 PARTIAL 日期可在源数据恢复后原子升级；Scheduler 接受 PARTIAL 发布但不自动重试。`--require-complete` 保留为显式严格门控。


### AKsignal Daily Scheduler / Run Control

AKsignal 自己的 macOS LaunchAgent（`com.aksignal.daily-scheduler`）拥有 08:30 Asia/Shanghai 定时任务；Service Scheduler 只管理 Service 域任务，不再触发 AKsignal。Scheduler 在计划分钟启动在线 Daily Run；若 Mac 错过计划时点，或批次失败/退出未发布，只记录 `MISSED` / `FAILED` 并等待 AKsignal 面板手动 Run/Resume，不自动补跑、不自动重试。每个交易日继续复用原生 Runner、互斥锁、checkpoint、代码/config 签名与正式发布门控。Scheduler 进程重启与 Daily 批次恢复是独立控制。

`src/daily_scheduler.py` 安装/管理 LaunchAgent，`src/daily_schedule.py` 分离定时检查与人工运行入口，`src/daily_hub.py` 只读提供 Scheduler 与批次状态；Hub Worker 在 AKsignal 域分别暴露 Scheduler 服务控制与 Daily Run 人工操作。首次部署仍需显式安装/启动 AKsignal LaunchAgent；源码存在或面板可见不等于生产定时已启用。字段、操作和切换步骤见 [AKsignal Daily Scheduler](DAILY_SCHEDULER.md)。

## 退出核心产品的能力

主题 Tier 确认、Selection 推荐与 Timing、行业结构穿透、独立 Radar、三 Lane 合成、迁移生命周期状态机、历史赔率与策略回测已退出当前产品实现并清理对应入口。独立篮子比较研究仍保留；`price_map` 及位置契约继续由 Observation 复用，不产生投资建议。

`docs/ARCHITECTURE.md` 与 AGENTS.md 中旧 Layer / Lane 叙述为历史背景，当前范围以本状态图为准。

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
