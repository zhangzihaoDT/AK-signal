> 当前正式basket_compare（2026-10）：8个研究篮子 + 高现金流参照，共9条净值/68只去重个股，禾赛排除。个股与沪深300仅长桥，ForwardAdjust收盘价/NoAdjust指数；季度再平衡为默认结构指数，买入持有为对照。入口为 `python src/main.py research basket compare --start YYYY-MM-DD --end YYYY-MM-DD --snapshot <目录> --output <目录>`，联网需显式 `--allow-online-fetch`。旧legacy-run/legacy-fetch仅历史研究，不能作为正式报告备用。当前口径见本文“主线观察指标”和文末验证记录；旧[切换记录](validation/BASKET_COMPARE_LONGBRIDGE_20261016.md)保留作历史。下文保留研究配置和证据阶段定义。

# Research Basket

Research Basket 是独立于 Layer ①/②/③ 和 Portfolio Execution 的主题篮子研究模块。

## 定位

Compare 是投资主线的结构化观察系统，而非投资组合回测系统。其核心目标是识别主线的趋势、相对强弱和上涨广度，并通过集中度与重叠诊断解释结果。

它不直接生成买入建议，也不阻塞 `make run-day`。研究结论经过历史稳健性验证后，才可以被其他决策层显式消费。

日报 04 的[分组趋势启动观察](STOCK_GROUP_ACTIVITY.md)复用同一分组注册、子组权重及 QR 算法，以本批日报长桥股票/沪深300快照重建结构指数、RS20Δ 和参与度；不读取 Compare 产物或改变其结果。单日异动只作启动线索，Compare 仍保持独立研究入口。

默认结构口径是“组间等权 + 组内等权 + 季度再平衡”；买入持有净值只作为对照，用来观察权重漂移的影响。净值差异不能脱离同一观察对象解释：QR 与 BH 共用股票名单、起点、价格快照、上市前现金规则和 HS300 交易日历，唯一差异是季度再平衡。

## 配置

入口为 `config/research_baskets.yaml`。标的清单复用两个资产文件——主题来源读 `config/selection_universe.yaml`（themes），观察组来源读 `config/research_observations.yaml`（observation_groups）——研究配置只声明：

- 主题或观察组来源
- include/exclude 子组
- 权重方法
- 调仓方法
- 基准

当前默认篮子（8 条研究主线）：

- `hk_resources_shipping`（AZ 仓位观察）
- `scania_china_watch`
- `china_auto_global`（含 OEM）
- `china_auto_global_ex_oem`（剔除 OEM）
- `ai_capex`（AI 基础设施 6 组股票代理）
- `memory_and_storage`（存储 / 记忆 5 只，monitor_only 研究迁移）
- `auto_tier1_ai_infra`（汽车热管理供应链 → AI 数据中心液冷 / Capex）
- `auto_tier1_embodied`（汽车供应链 → Physical AI 执行器 / 丝杠 / 轴承 / 控制器）

另有 `high_cashflow`（高现金流资产）作为独立参照，不参与 8 条主线的排名。

当前默认 `group_equal + quarterly` 的含义是：组间等权、组内等权，使用前复权收盘价；只在 HS300 日历确认的自然季度最后交易日收盘重置到目标权重，下一交易日生效。数据只到季度中途时，最后一个数据日不是再平衡日。买入持有（`buy_and_hold`）保留为对照。

## 主线观察指标

- **主题净值**：季度再平衡结构指数，区间起点=100；买入持有为对照。区间切换只重新归一化同一路径，不重新等权。
- **相对强度 RS**：`篮子净值 / 沪深300净值`，区间起点=100；`超额 = RS − 100`（百分点）。`RS20Δ = 100 × (RS_t / RS_{t−20} − 1)`，使用 20 个 HS300 交易日，与区间起点无关；取不到 `t−20` 时显示缺失值。
- **上涨参与度**：区间内正收益股票数 / 有效股票数，以及最近 20 个 HS300 交易日的正收益股票数 / 有效股票数。按股票数量等权，不使用组合权重；晚上市或历史不足的股票从分母剔除，停牌沿用最近价格，平盘计为非上涨。
- **集中度**：最大目标权重、有效股票数 `1 / Σw_i²`、单票子组数。使用完整观察起点的股票目标权重，剔除现金袖套后归一化；现金袖套单独列示，不进入股票 HHI。
- **重叠诊断**：同一股票在不同篮子中分别计入；报告共享股票表、独有股票权重和两两重叠矩阵。重叠只用于解释主线相关性，不调整权重。
- **状态标签**：`强且广 / 强但窄 / 弱但广 / 弱且窄`，由区间超额和区间参与度共同决定，不作为买卖信号或综合评分。

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
make basket START=2025-09-23 END=2026-09-23   # 指定窗口
make basket-refresh DAYS=800        # 抓取近 800 自然日（默认）
```

季度再平衡净值是默认主路径，买入持有净值作为对照，两条路径**共用同一窗口**（`START`/`END`）和同一观察对象。报告表格同时展示 QR 与 BH 的区间收益，并给出 QR−BH 差异，便于把差异归因于季度再平衡而不是选股或数据差异。

等价的底层命令：

```bash
python src/main.py research basket fetch --days 800 --baskets all --refresh-benchmark
python src/main.py research basket run --baskets all
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
- `basket_compare.html`（主产物）：净值对比图 + **指标汇总表**（收益/超额/年化/最大回撤/Sharpe/波动/胜率/Top1-Top3 贡献/季度收益·季度超额）+ **每篮子明细**（滚动 20/60/120D 超额·Sharpe·回撤 / 个股贡献 Top-N+尾部负贡献 / 子组收益）+ Cross-Basket 共同成分
- `{basket}_rolling.csv`：20/60/120 日滚动超额、Sharpe、回撤
- `{basket}_contributions.csv`：个股收益贡献及 Top1/Top3 标记
- `{basket}_quarterly_nav.csv`：季度首个交易日重置权重后的净值（供指标表的季度收益/超额列）

## 研究边界（legacy 与通用约束）

以下部分保留旧 research basket 研究口径；正式 Compare 的 QR/BH、RS、参与度、集中度和重叠口径见上文“主线观察指标”和“可比走势图”。两者不能混用。

### 2026-10-15 数据补齐与身份修正

八个篮子的 54 只唯一股票已对齐至 2026-10-14，研究窗口从 2024-07-16 开始；上市较晚的联芸科技、禾赛只保留真实上市后数据。沪深300缓存同样覆盖至该日。覆盖核验、源数据哈希、刷新脚本和检查 notebook 位于 `data/audits/research_baskets/20261014/`（本地数据产物，不纳入 Git）。

禾赛当前配置由 HK 02510 更正为 HK 02525，原错误代码缓存和历史发布保留作审计；新篮子长表不再包含 02510。禾赛 2026-07-10 至 2026-07-23 原柜台缺失的十条日线，以同公司临时柜台 02983 的真实行情补充，只取原柜台缺失日期；并行交易重叠日不拼接成交量。补充依据和原始参考表见 `hesai_counter_supplement.json`、`references/`。

**数据补齐不等于可直接计算可比收益。** 禾赛于 2026-07-10 一拆八，原始价格保留拆股断点；收益比较前必须处理股份数量/价格调整，并明确晚上市成分的加入规则。现有通用 fetch 不自动处理临时柜台，重新在线全量抓取禾赛后需重新核验这十日，不能静默前值填充。普冉股份十个缺失交易日对应披露停牌期，未补造原始 bar。窗口内东岳集团一条、东方海外国际两条 OHLC 异常保留在源数据和审计中；全池复权依据仍未标为已验证。

- 数据刷新走 `research basket fetch`（`make basket-refresh` 会自动先跑基准刷新再 fetch 再 run），篮子计算本身纯离线。
- **窗口统一截到「最近完整交易日」**：盘中运行（15:10 前）当日未收盘 bar 一并排除，篮子与基准末日一致，避免最后一点混入盘中行情。规则见 `calculator.last_complete_trading_day`。
- 港股和 A 股交易日不完全一致，统一到 HS300 交易日并对已开始交易的标的前值填充。
- 首个有效交易日前不填充。
- 当前结果存在静态资产池和幸存者偏差，不能直接视为可交易策略回测。
- 旧 quarterly 研究采用季度首个交易日重置组间/组内权重，边界日不计入新季度收益；正式 Compare 改为自然季度最后确认交易日收盘重置，下一交易日生效。
- 正式 Compare 的 QR/BH 贡献按逐日 P&L 相加，与净值变动对账；旧版按篮子初始权重乘区间收益计算，`top1` / `top3` 标记贡献排名，不把负贡献从分母中静默剔除。
- 当前尚未加入市值权重（`basket fetch` CLI 已提供）。

## 可比走势图（独立核验价格快照）

旧 `basket run` 保留原研究语义；新增离线 `basket compare` 单独消费核验后的前复权价格快照，避免将原始港股拆股断点或历史缓存的价格依据混入可比收益。计算不改原始行情，也不影响每日 Market Facts。

```bash
python src/main.py research basket compare \
  --snapshot data/audits/research_baskets/20261014/adjusted_references \
  --output outputs/research/baskets/comparable \
  --start 2024-07-16 --end 2026-10-14
```

- 快照需有完整 `manifest.json`、逐标的 CSV 和 SHA256；缺失、哈希不符、末日滞后或缺少主缓存中已知交易 bar 时拒绝生成。
- 价格使用当前供应商 CN 前复权日线、HK 原始价乘前复权因子；逐标的记录实际来源。禾赛拆股日前因子0.125、拆股日后1，临时柜台补充价采用对应日期因子。不称为独立验证的含息总回报或历史时点回测；本币、不计汇率与成本。
- 固定初始组间/组内目标权重。晚上市成分的资金留作零收益现金，在首个真实收盘价投入；QR 在季度末把现金袖套调回目标权重，BH 不调仓；不会因成分数量变化重新平均净值。任一指定成分缺行情时不静默丢弃。
- 沪深300交易日作为展示日历，港股独有交易日必须先进入估值时间并集，再前值估值到展示日。上市前的1代表现金资产净值，不是回填股价。
- 单文件 `basket_compare.html` 内嵌图表运行库，可离线打开。含9线总图（8条研究主线+高现金流参照）、8张主线小图+1张参照小图、季度再平衡/买入持有切换、主线强弱表、四象限定位图、结构与集中度、跨篮子重叠和成分权重明细。区间按钮将同一净值路径在所选起点归一化，不重新等权；表格和散点按季度再平衡口径，BH 仅作对照。
- `comparison_nav.csv`（QR默认）、`comparison_nav_bh.csv`、`comparison_manifest.json`、`basket_metrics.csv`、`basket_participation.csv`、`basket_overlap.csv`、`basket_shared_stocks.csv`、各篮子 `_nav.csv` / `_nav_bh.csv` / `_weights.csv` 为可复核计算产物。该报告目前为独立研究入口，不加入日报正式发布链；Hub 接入需消费同一快照，不能自行重算。

## 正式 Compare 验证记录（2026-10-20 长桥快照）

- 9 条净值：8 条研究主线 + 高现金流参照；68 只去重股票；禾赛 02525 按观察政策排除。
- 季度再平衡共 9 次，最后一次为 2026-09-30；数据截至 2026-10-20 属于 Q4 中途，2026-10-20 未被误判为季度末。
- QR/BH 共享同一名单、起点、价格快照、上市前现金规则和 HS300 交易日历；`comparison_control` 校验通过，唯一差异为再平衡规则。
- RS20Δ 在完整区间/近一年/YTD/近三个月四个窗口上逐篮子一致；取不到 t−20 时返回缺失。
- 半导体存储的联芸科技在观察起点尚未上市，现金袖套为 20%；集中度按股票口径剔除该袖套后归一化，最大目标权重 25%、有效股票数 4.0，现金袖套单独披露。
- 跨篮子重叠诊断识别到银轮股份、拓普集团各出现在 3 个篮子，中鼎股份、三花智控、均胜电子、伯特利各出现在 2 个篮子；重叠只解释相关性，不调整权重。
- 全量测试通过；正式 Hub 预览仍沿用上一份已核验报告，待下一次 `research basket compare` / `hub_update` 后切换。
