# AKsignal Canonical Architecture Map

> 当前能力的 canonical 状态图。基线版本：v0.5.0。更新：2026-10-13。

## 完整定位与项目目标

**AKsignal = Market Intelligence & Portfolio Strategy System。**

目标是通过量化方法辅助管理一个真实的投资组合，而不是只生成市场观察报告。

```text
AKsignal
├── Facts
│   ├── Market Facts       市场事实
│   └── Portfolio State    组合状态
└── Strategy
    ├── Selection          选筹
    ├── Entry              买入建议
    ├── Allocation         单项资产仓位建议
    ├── Exit               卖出建议
    └── Portfolio Advice   整个组合的综合调整建议
```

| 部分 | 回答的问题 | 边界 |
|---|---|---|
| Market Facts | 市场发生了什么？ | 行情、趋势、位置等量化事实，不把策略结论写成事实 |
| Portfolio State | 目前实际持有什么、现金和风险暴露是多少？ | 实际持仓、数量、现金、估值与暴露；派生量标注价格时点、来源与计算口径，不包含建议仓位 |
| Selection | 应该买什么？ | 将事实压缩为候选标的及选择依据 |
| Entry | 何时买？ | 给出买入条件、触发与当前是否满足的建议 |
| Allocation | 单项资产建议配置多少？ | 输出单项资产目标仓位建议，显式说明占组合净资产等分母及假设 |
| Exit | 何时卖？ | 给出退出、减持条件与依据 |
| Portfolio Advice | 整个组合应该如何调整？ | 消费市场事实、实际组合与各项策略建议，综合现金、集中度、主题暴露等约束，给出组合层面的最终调整建议 |

**Allocation 与 Portfolio Advice 不混同。** 例如 Allocation 建议某资产目标权重10%，Portfolio Advice 结合该资产已有仓位和同主题暴露，可以建议本次只增加3个百分点，或暂不增持。目标权重、当前权重、建议调整量、调整后权重必须分别记录，不能用一个 weight 字段混合表达。Portfolio Advice 保留原始单项建议与约束原因，不覆盖它们或篡改实际持仓；建议调整不等于已发生交易。真实交易/账户记录确认后，才更新 Portfolio State。

建议数据流：Market Facts + Portfolio State → 各项 Strategy 建议 → Portfolio Advice 综合协调 → 人工决策。策略可读取组合约束；五项能力不是必须严格串行的流水线。Strategy 消费版本化事实快照，不自行联网制造行情事实；不同策略可消费同一 Facts 给出不同建议。

## 必须坚持的原则

**AKsignal 负责发现和量化市场事实，你负责理解这些事实并作出投资决策。**

该原则约束最终投资决策的责任与事实/建议分离，不排斥 Strategy 提供量化建议。系统辅助真实组合管理；投资者保留最终决策权。

## 当前实现阶段

当前每日主链实现的是 **Market Facts** 的八项基础能力：观察全市场 ETF、全行业及固定池个股的趋势与底部位置。每日报告是事实交付入口，不是整个项目的最终产品定位。

**Portfolio State 与新 Strategy 架构尚未作为正式核心能力实现。** 旧 Selection、Timing、研究代码的存在不等于这五项策略能力已完成；目前仍不由 daily-run 自动输出买入推荐。后续建设应基于这棵整体架构，而不是恢复已退出主链的旧功能拼装。

主题和 tier 当前仅保留观察分组与注册身份；同一股票多个身份不压成单标签。

## Market Facts 的三类内部能力

- **Data Foundation｜数据基础**：①固定池股票 · ②全市场 ETF · ③全行业指数。
- **Trend｜趋势观察**：④行业轮动 · ⑤大类资产 · ⑥ETF 趋势 · ⑦个股趋势。
- **Position｜位置观察**：⑧ETF 与个股底部位置。
- **Daily Observation Report**：统一日期 · 统一数据质量 · 统一发布，消费上述事实，不新增投资决策。

## 保留的八项能力

| # | 能力 | 模块 / 输入 | 产物 |
|---|---|---|---|
| 1 | 固定观察池个股数据获取与整理 | trend_engine；selection_universe.yaml 作为现有观察池配置 | data/raw/{market}_{symbol}.csv；stock_update_audit_{date}.json |
| 2 | 全市场 ETF 数据获取与整理 | etf_signal 数据获取 / 分类 / 缓存 | data/etf_signal/raw、master、diagnostics |
| 3 | 全行业指数数据获取与整理 | sw_industry_rps 数据获取 / 兜底 / 缓存 | data/processed/sw_industry |
| 4 | 全行业轮动观察 | 行业 metrics，保留 RPS / delta / 趋势阶段 | observation/industry_rotation、industry_directions |
| 5 | 大类资产趋势观察 | ETF 分类与横截面聚合；通过 ETF 代理观察 | observation/asset_directions |
| 6 | ETF 趋势识别与活跃池 | RPS 原值、价格 / MA20 / MA60 / 斜率 | observation/etf_trends |
| 7 | 观察池个股趋势评估 | stock-metrics，只发布趋势事实，移除 action / watch_level | stock_metrics/stock_metrics_{date}.parquet |
| 8 | ETF 与观察池个股底部位置判断 | ETF 复用 price_map；股票独立 stock_position_v1 | observation/etf_positions、stock_positions |

上述 observation 文件均按 trade_date 输出 parquet + CSV。位置只描述历史收盘价区间位置，不断言未来不会继续下跌。股票口径与可靠性限制见 [POSITION_OBSERVATION.md](POSITION_OBSERVATION.md)。

ETF 活跃的观察定义：price > MA20、price > MA60、MA20_slope > 0、MA60_slope > 0；均线缺失不视为活跃。不输出 BUY / WATCH / Timing。行业阶段沿用已有 Observation 三分类。ETF 与行业 RPS 横截面不同，不直接比较。

## 每日运行与交付

唯一完整每日入口为 `make run-day`，由 src/daily_run.py 编排：

```text
etf update → industry update → etf calculate → industry calculate
  → observation stock-metrics → observation build → observation check
  → validate_release → 统一发布
```

`make run-day-offline` 跳过在线 update，个股仅读缓存。所有步骤固定目标 trade_date；核心股票与基准未对齐时停止发布。ETF 缺口显式列入审计。

每日只有一个报告入口：`outputs/observation/observation_{date}.html`，附同名 JSON 摘要与 Markdown。正文只保留三个主要观察区域：**市场方向**（大类资产与行业轮动）、**趋势活跃**（ETF 与固定池个股）、**底部位置**（ETF 与固定池个股的状态、变化、窗口可靠性）。数据覆盖、复权验证、缓存与来源状态集中在独立的数据质量区域；不占用投资观察正文。机器事实全量落盘。ETF 活跃定义沿用均线条件，个股活跃展示趋势分≥70，完整固定池可展开；这只是展示范围，不创造推荐资格。

最终验收为 `outputs/run_day/final_check_{date}.json`，发布回执为 `outputs/run_day/publication.json`。结果包含 scope=observation_only、status、warnings、errors；不再包含投资 action。

新批次暂存目录从空目录建立，不复制上一批 outputs 中的研究报告或旧候选。验收后切换 outputs 符号链接，上一批产物留在 `.run_day/runs/{date}/{run_id}/outputs`；首次旧 outputs 目录保存在 `.run_day/legacy`。失败不替换已发布产物。检查点、互斥锁、事实摘要、恢复与代码/config 兼容性检查继续保留。代码变化须 --restart。

trade_date 为信号分区；run_date / generated_at 仅为审计时间。CONFIRMED / COMPLETE / PARTIAL 是来源与完整性状态，不是推荐状态。本产品不写新的 *_latest 报告别名。

### AKsignal Daily Scheduler / Run Control

AKsignal 自己的 macOS LaunchAgent（`com.aksignal.daily-scheduler`）拥有 08:30 Asia/Shanghai 定时任务；Service Scheduler 只管理 Service 域任务，不再触发 AKsignal。Scheduler 在计划分钟启动离线 Daily Run；若 Mac 错过计划时点，或批次失败/退出未发布，只记录 `MISSED` / `FAILED` 并等待 AKsignal 面板手动 Run/Resume，不自动补跑、不自动重试。每个交易日继续复用原生 Runner、互斥锁、checkpoint、代码/config 签名与正式发布门控。Scheduler 进程重启与 Daily 批次恢复是独立控制。

`src/daily_scheduler.py` 安装/管理 LaunchAgent，`src/daily_schedule.py` 分离定时检查与人工运行入口，`src/daily_hub.py` 只读提供 Scheduler 与批次状态；Hub Worker 在 AKsignal 域分别暴露 Scheduler 服务控制与 Daily Run 人工操作。首次部署仍需显式安装/启动 AKsignal LaunchAgent；源码存在或面板可见不等于生产定时已启用。字段、操作和切换步骤见 [AKsignal Daily Scheduler](DAILY_SCHEDULER.md)。

## 退出核心产品的能力

主题 Tier 确认、Selection 推荐与 Timing、行业结构穿透、独立 Radar、三 Lane 合成、迁移生命周期状态机、历史赔率研究均已从每日主链和发布验收移除。旧模块与显式研究/legacy 命令暂保留供历史重放；其输出不属于当前核心产品，不由每日自动执行。price_map 的基础位置算法继续被 Observation 使用。

`docs/ARCHITECTURE.md` 与 AGENTS.md 中旧 Layer / Lane 叙述为历史背景，当前范围以本状态图为准。

## 每日展示一致性规则（UI v3）

排序、分页与展开由 `src/observation/report.py` 统一定义，`observation build` 每日调用同一个 renderer；JSON 同步记录 report_layout_version。仅修改展示顺序，不改原始事实或候选条件。

- 大类资产：固定 CROSS_ASSET_ORDER，保证方向位置每天一致。
- 行业：RPS15降序 → RPS5降序 → 行业代码（缺少代码时名称兜底）。
- 活跃ETF：RPS15降序 → 流动性降序 → ETF代码。
- 固定池股票：趋势分降序 → 20日收益降序 → 市场/代码。默认展示分数≥70的前20项；其余活跃及未达门槛股票在同一个“其余固定池个股”展开区，不再复制完整池。
- 底部位置：有效进入/离开变化 → 有效长期低位 → 其他有效 → 未知/不适用；同组按360日位置升序，再按身份代码。数据缺口不因位置数值低而排到可靠低位之前。
- 每个面板最多默认20项，只提供一个展开区；默认行与剩余行互斥，每对象每面板只出现一次。无活跃个股时默认区明确为空，展开仍可读剩余固定池。
- 同股票跨注册身份仅在展示层按市场/代码去重；机器事实保留全部注册。跨不同观察区域的同资产允许重复，分别回答趋势与位置问题。
- 搜索覆盖该面板默认与展开的完整列表，搜索时展开剩余区；清空后恢复原展开状态。缺值排最后，同分用稳定身份键打破平局。

UI v3：页面最大宽度1120px；压缩标题、卡片和行间距。取消表格纵向高度限制与内部纵向滚动，使用页面主滚动；小屏仅保留必要的表格横向滚动。展开与排序规则保持不变。

已知身份错配的跨日保护源为 config/observation_identity_issues.json；当日审计可补充覆盖，但不依赖本地审计快照存在。
