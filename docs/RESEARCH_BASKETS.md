# Research Basket

Research Basket 是独立于 Layer ①/②/③ 和 Portfolio Execution 的主题篮子研究模块。

## 定位

它回答：按照一组明确的资产、子组和权重规则持有一个主题篮子，历史表现如何，超额来自哪里。

它不直接生成买入建议，也不阻塞 `make run-day`。研究结论经过历史稳健性验证后，才可以被其他决策层显式消费。

## 配置

入口为 `config/research_baskets.yaml`。标的清单复用两个资产文件——主题来源读 `config/selection_universe.yaml`（themes），观察组来源读 `config/research_observations.yaml`（observation_groups）——研究配置只声明：

- 主题或观察组来源
- include/exclude 子组
- 权重方法
- 调仓方法
- 基准

当前默认篮子：

- `scania_china_watch`
- `china_auto_global`（含 OEM）
- `china_auto_global_ex_oem`（剔除 OEM）
- `ai_capex`（AI 基础设施 6 组股票代理）
- `memory_and_storage`（存储 / 记忆 5 只，monitor_only 研究迁移）
- `auto_tier1_ai_infra`（汽车热管理供应链 → AI 数据中心液冷 / Capex）
- `auto_tier1_embodied`（汽车供应链 → Physical AI 执行器 / 丝杠 / 轴承 / 控制器）

当前 `group_equal + buy_and_hold` 的含义是：组间等权、组内等权，使用前复权收盘价，不做期间调仓。

## Schema v1 稳定基线

- 三正交字段（`evidence_stage` / `revenue_evidence` / `capacity_stage`）为 **v1 稳定 schema**（`config/research_baskets.yaml` version 1.0.0），字段语义与枚举不再随主题叙事改动。
- **更新纪律**：后续只做 **evidence-driven stage update**——每次在 `config/research_observations.yaml` 修改阶段字段时，必须同时在 `config/research_stage_log.yaml` 的 `entries` 追加一条记录（日期 / 标的 / 字段 / from→to / 披露证据），不得覆盖或回填旧记录。
- **一致性由测试守护**：`test_stage_log_matches_current_config` 校验 config 当前阶段 == log（genesis + entries）推得阶段；漏记 log 会直接让测试失败。
- **预留分析**：stage-change 日志积累后，可回测每次 `VALIDATION → ORDER → SMALL_BATCH → MASS_PRODUCTION` 升级前后 20D / 60D / 120D 超额收益，回答「市场什么时候开始给产业兑现定价」（event study 尚未实现）。

## 证据阶段（evidence_stage）

观察组每个 `listed_assets` 携带三个**正交**字段，把「产业链位置 / 商业化阶段 / 收入证据 / 产能阶段」拆开，回答「AI 业务走到哪一步了」而非「有没有 AI 故事」：

| 字段 | 回答什么问题 | 枚举 |
| --- | --- | --- |
| `evidence_stage` | 产品商业化推进到哪一步 | `VALIDATION / DESIGN_WIN / ORDER / SMALL_BATCH / MASS_PRODUCTION` |
| `revenue_evidence` | 有没有确认收入 | `NONE / CONFIRMED`（空 = 未确认） |
| `capacity_stage` | 产能是否扩张 | `NONE / PLANNING / BUILDING / RAMPING`（空 = 未确认） |

**纪律：`evidence_stage` 只表达商业化阶段，`REVENUE` 不作为 stage，收入一律由 `revenue_evidence` 单独表达。** 三字段保证可以直接 `groupby("evidence_stage")` 做 stage-return attribution。

当前观察组展开后的阶段矩阵：

| Asset | Theme | Commercial stage | Revenue | Capacity |
| --- | --- | --- | --- | --- |
| 中鼎 | AI Cooling | ORDER | — | — |
| 拓普 | AI Cooling | ORDER | — | — |
| 银轮 | AI Cooling | VALIDATION | — | — |
| 飞龙 | AI Cooling | MASS_PRODUCTION | CONFIRMED | — |
| 三花 | AI Cooling | MASS_PRODUCTION | CONFIRMED | — |
| 北特 | Physical AI | SMALL_BATCH | — | BUILDING |
| 拓普 | Physical AI | SMALL_BATCH | CONFIRMED | — |
| 银轮 | Physical AI | SMALL_BATCH | CONFIRMED | — |
| 均胜 | Physical AI | MASS_PRODUCTION | CONFIRMED | — |

`evidence_stage` / `revenue_evidence` / `capacity_stage` / `note` 会随 `expand_constituents` 透传进 `{basket}_constituents.csv` 与跨篮子共同成分表，供归因审计。同一标的可在多个篮子注册（如拓普同时属 AI Infra 与 Physical AI），其证据阶段按篮子分别记录。

Physical AI 篮子的子组按**机器人价值链位置**划分（非信心程度）：`execution_hardware`（丝杠 / 关节 / 执行器 / 轴承）与 `control_stack`（大脑 / 控制器 / 域控），成熟度变化无需搬组。

## 跨篮子共同成分

`compare_report` 会额外输出 `basket_overlap.csv` 并在对比报告中渲染 **Cross-Basket 共同成分** 表：列出同时出现在多个篮子的标的及其在各方框的证据阶段、所属子组与收益贡献（pt）。

目的：当一只共同成分（如拓普）同时驱动多个篮子上涨时，避免把「单一成分拉动」误读为「两条主题同时成立」。CLI 运行后也会在终端打印 overlap 摘要。

## 命令

日常推荐用 `make`（手动按需）：

```bash
make basket-refresh                 # 联网：刷 HS300 基准 + 抓所有篮子来源行情 + 重算
make basket                         # 离线：只读已落盘价格重算（快）
make basket BASKETS=ai_capex        # 只跑指定篮子
make basket START=2025-09-23 END=2026-09-23   # 指定买入持有窗口
make basket QSTART=2024-09-10       # 指定季度净值窗口起点（默认 2024-09-10）
make basket-refresh DAYS=800        # 抓取近 800 自然日（默认，覆盖季度窗口）
```

**两个窗口是独立的**：买入持有净值（`basket_compare.html`）用 `START`/`END`；
季度调仓净值（`basket_quarterly_compare.html`）用 `QSTART`/`QEND`（留空则回退到
`START`/`END`）。`QSTART` 默认 `2024-09-10`（924 行情前的稳定基准），因此
`make basket-refresh` 的 `DAYS` 默认取 800 以覆盖该窗口；若把 `DAYS` 调小到窗口之外，
季度净值会从实际可用首日落起。

等价的底层命令：

```bash
python src/main.py research basket fetch --days 800 --baskets all --refresh-benchmark
python src/main.py research basket run --baskets all \
  --quarterly-start 2024-09-10
```

`fetch` 从 `config/research_baskets.yaml` 声明的来源出发，按 `(type, key)` 去重后抓取
（新增篮子自动纳入抓取），默认跳过主题 ETF 层（`theme_etf` / `sub_industry_etf` / `watch_etf`）。
`run` 纯离线，只读已抓取的 `*_prices.parquet`。

## 产物

默认写入 `outputs/research/baskets/`：

- `{basket}_nav.csv`：篮子和基准归一化净值
- `{basket}_metrics.json`：收益、回撤、波动、Sharpe、胜率和超额
- `{basket}_groups.csv`：子组收益与标的数量
- `{basket}_constituents.csv`：展开后的研究成分
- `{basket}_manifest.json`：来源、权重、配置 hash 和覆盖元数据
- `basket_compare.html`：多篮子交互式对比图
- `basket_quarterly_compare.html`：季度调仓后的多篮子对比图
- `{basket}_rolling.csv`：20/60/120 日滚动超额、Sharpe、回撤
- `{basket}_contributions.csv`：个股收益贡献及 Top1/Top3 标记
- `{basket}_quarterly_nav.csv`：季度首个交易日重置权重后的净值（窗口由 `--quarterly-start/--quarterly-end` 控制）

## 研究边界

- 数据刷新走 `research basket fetch`（`make basket-refresh` 会自动先跑基准刷新再 fetch 再 run），篮子计算本身纯离线。
- **窗口统一截到「最近完整交易日」**：盘中运行（15:10 前）当日未收盘 bar 一并排除，篮子与基准末日一致，避免最后一点混入盘中行情。规则见 `calculator.last_complete_trading_day`。
- 港股和 A 股交易日不完全一致，统一到 HS300 交易日并对已开始交易的标的前值填充。
- 首个有效交易日前不填充。
- 当前结果存在静态资产池和幸存者偏差，不能直接视为可交易策略回测。
- 当前季度调仓采用季度首个交易日重置组间/组内权重，边界日不计入新季度收益。
- 当前滚动指标窗口为 20D / 60D / 120D：超额收益以百分点计，Sharpe 按 252 交易日年化，回撤为窗口内最大回撤。
- 个股贡献按篮子初始权重乘区间收益计算，`top1` / `top3` 标记贡献排名，不把负贡献从分母中静默剔除。
- 当前尚未加入市值权重（`basket fetch` CLI 已提供）。
