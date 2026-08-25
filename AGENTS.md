# AKsignal — Agent 操作手册

## 分层原则（最值得坚持的一点）

- **Observation Layer（制造事实，不做决策）**：Layer① ETF Rotation、Layer② Theme Confirmation——产出客观观察（RPS/确认状态），不做买入判断；ETF RPS 相对全市场 ETF 横截面、行业 RPS 相对 124 申万行业横截面，**标尺不同不可直接对比**
- **Decision Layer（消费事实，做决策）**：Layer③ Selection——只读 Layer①/② 已确认、已对齐、已落盘的事实，**禁止联网/重算（v0.4.3 固化）**，不制造新事实
- 核心：**Observation 不做决策；Decision 不制造事实**。改 Observation 层规则必须过 Parity；Decision 层改动不得引入新数据源
- **Fact 不可变**：Layer①/② 的数字是事实，Layer③ 只做 Policy 决策（筛选/拒绝/打分），**禁止覆盖、重算或冒充 Observation 原始字段**。产物保留事实原值（如 ETF rps15），Policy 的接受/拒绝单独标注（recommended/reason）。基于行业弱势拒绝 ETF（如 `if industry_rps < 60: reject etf`）是**合法 Policy**，但必须保留原始 ETF RPS 并显式记录拒绝规则；真正禁止的是把「策略拒绝」伪装成「事实修正」（如把 rps15 置空或暗示 Layer① 的 91 不存在）。不同策略可对同一事实给出不同 Policy（多策略兼容），均不改变共享事实；若某规则要改变「事实」，必须上移为 Observation 层规则并过 Parity


## 信号日期语义（v0.4.3）

- **统一约定**：Layer1（ETF）、Layer2（SW-RPS 确认）、Layer ③（Selection）一律以 **trade_date（最近完整交易日）** 作为信号分区日期与文件名日期。
- 盘中运行时（16:30 前 / SW 15:10 前），trade_date 自动锚定到上一完整交易日（周一回溯到上周五）；收盘后取当日。
- `run_date`（运行日）与 `generated_at`（生成时间）**仅承担审计语义**，写入 parquet 元数据，不参与文件命名与消费对齐。
- 每个产物 parquet 内带元数据列：`trade_date` / `run_date` / `generated_at` / `data_status`（confirmed/provisional）/ `source`（ETF=em；SW=sw_daily / sw_realtime+ths_enrichment / sw_hist）。
- Selection 消费逻辑：读取各层**元数据 trade_date** 做共同对齐，输出 `alignment`（aligned / stale_industry / stale_etf + industry_lag_days）。ETF 与 SW 不同步时显式标记滞后，不静默组合。
- `select run --date YYYYMMDD` 的 `--date` 语义 = **目标 trade_date**，字面按 `rotation_{date} / account_candidates_{date} / confirmation_{date}` 精确加载、缺文件不降级。
- 盘中快照与日频收盘信号分离：日频 run-day 只输出完整交易日横截面；盘中版本（snapshot_type=intraday）为独立模式，未启用。

## Latest Alias Policy（统一产物别名规范）

- **`*_latest.*` 仅在 CONFIRMED 发布时更新**；PROVISIONAL 仅生成带日期归档文件（如 `{name}_{date}_provisional.html`），**不写 / 不更新 `*_latest*`**。latest 永远代表当前最新正式发布版本。
- Layer①/②/③ 一律遵守：CONFIRMED 主报告落盘后同步复制为 `{name}_latest.*`；PROVISIONAL 只落带日期文件。
- 实现要点：`cmd_report` 仅非 provisional 分支调用 `save_latest_html`；provisional 分支只发布主报告。已落实于 Layer②，Layer①/③ 若引入 `_latest` 别名须同样遵守（详见 docs/ARCHITECTURE.md）。

## 多主题框架（v0.4.3，两方向）

- **单一事实源**：`config/themes_two_directions.yaml` 定义 bucket → theme → 申万二级行业焦点组 + ETF 关键词。Layer ①②③ 共同消费，不再硬编码。
  - Core（核心，长期收益）：**AI 基础设施**（注意不是 AI 应用，不含软件开发/IT 服务）
  - Quality（质量，高现金流防守）：**高现金流资产**（电力（火电·水电·核电）/ 三大运营商 / 公用事业（高速·公路·港口））
- **ETF 归属**：按 `themes_two_directions.yaml` 的 `etf_keywords` 匹配（bucket 顺序优先），不再依赖单一 is_tech 焦点组。`is_tech` 列保留作向后兼容。
- **Layer ②**：确认输出按 bucket/theme 分层，confirmation parquet 新增 `bucket` / `bucket_label` 列，报告按 Bucket → Theme 展示。
- **Layer ② 行业轮动脉冲（v0.7.1）**：Layer② 是全市场行业轮动观察的唯一主场（不做 ETF 产品层面判断）。metrics 与 confirmation 新增 `RPS1`（今日热度，today_window=1）、`delta_rps15_5d`（Δ5RPS15，velocity_window=5，RPS15 今日 − RPS15 5 交易日前）两列（`sw_industry_rps.yaml rps.today_window/velocity_window`），均为 Observation 展示，**不参与确认（确认仍只看 RPS15≥observe_threshold）**，也不进入 Layer③。主行业报告首表改为「行业轮动概览（今日强度榜）」，新增「行业轮动状态」四类观察分类（`classify_rotation_state`：强势延续 / 加速启动 / 高位休整 / 一日脉冲 / 走弱，仅描述今日轮动到哪、是延续还是启动，不改确认 Policy）；confirmation 报告证据表加 RPS1/Δ5RPS15/轮动状态，主题共振表加「③ 主题视角」（中位 RPS1 今日热度 / 中位 RPS5 近期轮动 / 轮动状态分布）。积累一段历史后，才验证短周期 RPS 是否进入确认 Policy。
- **Layer ① ETF 三问三答（v0.7.1）**：Layer① 主语是 ETF 产品，报告只回答三问——① 大类资产往哪里动（跨资产方向：A股宽基/行业主题/港股/海外/债券/商品黄金/现金，`cross_asset_direction`）；② 趋势活跃 ETF（`active_etf_representatives` 按方向去重、每方向一只流动性最好代表，排序 STRONG_WATCH→BUY_CANDIDATE→WATCH→RPS15→流动性，新增/退出活跃简报，完整名单在 `watchlist_active_{date}.csv`）；③ 我的主题 ETF（config/stock_universe.yaml 固定主题池 theme_etf+sub_industry_etf）。RPS1/ΔRPS15/流动性作为单只 ETF 补充信息保留在②③，不做「今日热点主题/加速主题」宏观判断（`market_pulse`/`leader_lists` 已移除）。数据覆盖/异常数量等审计信息只保留在页脚一行，不进正文。全市场行业脉搏整体归 Layer②。
- **Layer ② 三问三答镜像（v0.8.0/0.8.1）**：Layer② 与 Layer① 同构镜像——① 行业轮动往哪里动（`metrics.cross_industry_direction` 按 `parent_industry` 申万一级方向聚合，强度 median_rps15 × 速度 median_delta_rps15_5d × 广度 active_ratio，Top5 强势 + Bottom3 弱势 + 一句结构总结，全量折叠；`_industry_direction_state` 独立阈值不复制 ETF 口径）；② 哪些行业形成趋势（含驱动模式，按轮动状态语义筛选分组而非固定 Top40，矩阵与状态变化降级折叠）；③ 我的主题获得哪些行业支撑（主题概览：状态+结论+RPS15+驱动模式最小证据 + `<details>` 完整确认证据：主题判断/证据明细/龙头vs广泛/背离，删主题热图，未确认差值只在主题级一句话体现）。主报告 `sw_industry_rps_{date}.html` 一个 HTML 入口，confirmation 事实并入第三问，独立 confirmation HTML 已删除。
  - **驱动模式展示层分离（v0.8.2）**：`contribution_structure`（贡献集中度：单核/集中/多龙头/分散）与 `breadth_structure`（参与广度：广泛/中度/少数/分化）是**机器事实**（data/structure parquet 原样保留，`drive_pattern`/`format_structures` 的 `×` 双维拼接不变，Layer③ 透传不受影响）；人类可读文案由**独立展示模块** `src/sw_industry_rps/drive_labels.py` 映射——`composite_drive_label(cs,bs)` 把 4×4 组合合成一句话综合语义（如「单核主导×广泛上涨」→「龙头拉动普涨」），`drive_detail(...)` 输出双维+数值。主报告/概览只显示综合标签，详情页显示双维+数值；unknown/missing 一律显式 fallback「驱动信息不足」。该展示模块不依赖 confirmation Policy（避免「全市场报告为显示文案反向依赖主题确认」的耦合）。
  - **第二问趋势阶段三分类（v0.8.3）**：第二问从「算法状态分类（强势延续/加速启动/高位休整…）」改造成**语义三阶段** `classify_trend_stage`（Observation，不改确认 Policy）——A **已形成趋势**（RPS15 站稳观察区且持续，展示 RPS15/Δ5/驱动模式/参与率）· B **正在启动**（RPS5 快速领先而 RPS15 未跟上，如元件 RPS5=100/RPS15=2，展示 RPS5/RPS15/Δ5，不展示驱动因趋势未确立）· C **正在退潮**（falling_out 或 RPS15 高但 RPS5 明显回落，原强势降温/跌出）。三分类回答「强是刚开始强，还是已持续很强」；驱动模式（v0.8.2）回答「龙头拉还是全行业涨」。折叠区语义化为「▶ 20日轮动矩阵 · 状态变化详情」「▶ 全 124 行业」；第三问详情段对齐为 ①确认判断/②行业证据/③内部结构/④跨层对照。
  - **日报精简「一句话 + 一张表」×3（v0.8.4）**：Layer② 日报收敛为「① 一句话+Top5 产业方向表 · ② 一句话+单张行业表（行业/阶段/RPS15/RPS5/驱动）· ③ 每主题一行表（主题/状态/支撑/最接近确认/判断）」，一个屏幕读完核心。**直接从日报删除**（非折叠）：31 个一级全表、20日轮动矩阵、6 组状态变化、全 124 行业表、Theme Confirmation 四段完整证据、HHI/Top1/Top3 结构明细、ETF-行业对照、市场宽度卡——全部保留在 `metrics/structure/confirmation` parquet + CSV，研究/审计/调试时读取。第二问阶段统一定义：趋势=RPS15≥80 · 启动=RPS15<80 且 RPS5≥80（按 RPS5 排）· 退潮=高 RPS15 短期明显回落；structure（Enrichment，可选）未跑时驱动列整列隐藏，meta 提示「结构穿透：未完成（Enrichment，可选）」而非红色异常。第三问支撑列仅在有确认行业时显示，未确认主题支撑显示 —。
  - **Structure 定位 Core/Enrichment 分离（v0.8.4）**：Layer② 分两层——**Core Facts**（RPS / rotation / theme confirmation）必须每日稳定生成，决定报告能否发布；**Enrichment**（industry structure / drive pattern）尽力生成、可降级，决定报告解释得有多好。run-day 在 calculate 与 report 之间插入 **offline-only Structure（soft-fail）**：缓存够 → 生成 `sw_industry_structure_{date}.parquet` 驱动列出现；缓存不够 → 记 unavailable/insufficient，日报照常发布。**绝不因 Structure 缺数据在 run-day 自动联网**；联网补数走独立入口 `industry structure --allow-online-fetch`（Structure Cache Refresh / Enrichment，可手动/定时/收盘后跑，非主链路）。`compute_drilldown` 新增 `offline` 参数：offline 时跳过 `fetch_cn_daily` 网络回退，仅用缓存 + legulegu 成分股涨幅列。
  - **收尾修正（v0.8.4）**：① 第一问一句话按**表内位置**生成（核心=表前3，正在快速增强=表第4-5名），与 Top5 表严格一一对应，杜绝「下面为什么没有 XX」的疑惑；③ 第三问「判断」列从重复计数（「1 个进入观察区」）改造成**人话判断**（`_theme_judgment`：已确认→「X 已确认支撑，Y 距观察门还差 N」；接近→「最强 X 接近观察门，差 N」；未确认→「焦点行业均未进入观察区，最强 X，尚未形成行业共振」）。
  - **统一 Tier Gate 确认（v0.9.1/v0.9.2）**：三个主题（AI 基础设施 / 中国汽车全球化 / 高现金流资产）确认从「申万行业 Gate」统一升级为 **Theme → Tier basket → 个股趋势 → Theme confirmation → 申万行业 Evidence**。`config/themes_two_directions.yaml` 每主题下新增 `tiers` 定义段（每个 Tier 的 `universe_tiers` 映射 stock_universe.yaml 成分股归属，不重复维护股票清单）；`indicators.yaml` 新增 `tier_confirmation` 阈值（tier_gate_strong=70 / tier_gate_observe=55 / broad_fraction=0.5 / strong_trend_min=70）。每个 Tier 自算：**Tier Strength**（加权复合分 0.5×median(trend_score)+0.3×上涨比例+0.2×强趋势占比，0-100 非横截面 RPS）/ 上涨比例 / Trend Score 中位数 / 强趋势股票数量 / 龙头贡献度。产物 `data/processed/sw_industry/tier_confirmation_{date}.parquet`（含 trade_date/run_date/generated_at/data_status/source 元数据，全部配置了 tiers 的主题统一落此文件，兼容旧命名 `ai_tier_confirmation` 读取），主报告第三问改为「每主题独立区块」（头部状态 + Tier 表（Tier/状态/Strength/上涨比例/Trend中位/强趋势/驱动）+ 判断 + 申万交叉证据）。**申万行业保留为 Evidence，不再是主题确认 Gate**；Layer③ `evaluate_themes` 对配置了 tiers 的主题统一改用 Tier 门控（主题确认 = ≥1 个 Tier 进入确认门，BROAD = ≥broad_fraction），无 tiers 配置的主题（如未来新主题）维持原行业 Gate。run-day 顺序调整：`stock-metrics(-online)` 提前到 `sw-rps-confirm` 之前（Tier 确认消费个股趋势产物）；个股数据缺失时 Tier 确认降级 unavailable、主题回退行业 Gate，不阻塞发布。rule_version v0.7.0→v0.8.0。
  - **状态 taxonomy（v0.9.2）**：分层状态机，`WATCH` 不再承担「接近确认」语义：
    - **Tier 层**（观察单元）：`STRONG 强势 / CONFIRMED 已确认 / WATCH 观察 / UNCONFIRMED 未确认 / UNAVAILABLE 数据不可用`。WATCH 只表示「值得观察但尚未满足确认条件」，**为什么观察由 `reason_code` 表达**（`near_threshold 接近确认 / breadth_insufficient breadth不足 / trend_emerging 趋势启动 / single_name_only 单点驱动`），展示层组合成「观察 · breadth不足」等，不把业务含义塞进 state。
    - **Theme 层**（投资主题确认广度）：`BROAD_CONFIRMED / CONFIRMED / NARROW_CONFIRMED / UNCONFIRMED / UNAVAILABLE`，**不使用 WATCH**——即使多个 Tier 处于观察，Theme 仍是 `UNCONFIRMED`，报告以「未确认 · N 个 Tier 进入观察」呈现；观察中 Tier 数（`n_watch_tiers`）单独输出供展示。
    - **申万行业**：只作 Evidence（展示「最接近观察门」等描述），不再决定 Theme status。
    - **Selection 个股/ETF 的 WATCH**（`STOCK_STATE_WATCH`、`STRONG_WATCH`）是候选资格状态，与确认状态体系不同标尺，保持不变。
- **Layer ③**：候选对象 JSON 结构升级为 `layer3.buckets[].themes[]`（含 confirmed/expression/core_etf/sub_industry_etf/stock_watchlist/stock_candidates）。
- 主题资产池：`config/stock_universe.yaml` 保持 theme → tier → assets；bucket 归属由 `config/themes_two_directions.yaml` 推导，不在两个配置重复维护。
- **跨主题资产语义**：同一资产可在多个 theme 注册（如 通信ETF 同时属 ai_infrastructure 与 high_cashflow）。
  - 动态 ETF 候选按 `themes_two_directions.yaml` 关键词**首个命中**归属单一主题（bucket 顺序优先，不做跨主题复制）；
  - 固定池资产跨主题注册时，`recommended_actions` 按 (asset_type, code) **去重**，保留首个 bucket 的 primary 归属；
  - Position 权重归属属于 Layer 4（v0.4.3 不做），跨主题清单由 `select run` 输出 `config_issues.cross_theme_assets` 暴露。
- **配置降级**：asset pool 存在未注册 theme（不在 themes_two_directions.yaml）时，其资产不进入任何候选。默认告警并标记 `degraded` 继续发布（报告顶部显示配置降级提示）；`select run --strict` 可中止发布。
- **Future Themes（Not Enabled）**：Resource Cycle（有色/钢铁/煤炭）、High-end Equipment（高端装备）、Aerospace/Shipping（航空航天/船舶）等不在当前两方向，仅是「未启用」而非被否定，可在 `themes_two_directions.yaml` 重新打开；若启用商品类 theme，表达的是「权益/ETF 代理」（ETF + 申万有色行业 + 资源股），非商品期货趋势系统，须标 `maturity: PARTIAL`，不输出增配信号。

## 每日运行（run-day）

- **唯一入口**：`make run-day`（etf-update → sw-rps-update → etf-calculate → sw-rps-calculate → etf-pipeline → **stock-metrics-online** → sw-rps-confirm → sw-rps-report → **select** → **run-day-check**）——v0.9.1 起个股行情构建提前到 sw-rps-confirm 之前（Tier 确认消费个股趋势产物）
- **run-day 默认含 Layer ③**：selection 是 run-day 默认流程的固定环节；`make select` / `make select-offline` 保留为独立执行入口
- **离线的是决策，不是每日数据生产**：run-day 的 Observation 构建（ETF/行业/**个股行情**）默认允许联网更新（`stock-metrics-online` 自动增量抓取，不依赖手工补数）；Selection（Decision）阶段禁止联网。`make run-day-offline` 用于 CI/重放（个股仅读缓存，stale 降级兜底）；严格历史重放用 `research replay`
- **末端 Final Validation**（`make run-day-check` → `python src/main.py final-check`）：汇总 Layer①/②/③ 产物与 run 警告，输出最终结果
  - 成功：`Run completed successfully` + `trade_date / status / action / warnings`
  - 失败（产物缺失等）：`Run completed with errors` + errors 明细，退出码 1
  - `status`：各层均 confirmed → `CONFIRMED`；任一 provisional → `PROVISIONAL`；缺失/不一致 → `UNKNOWN`
  - `action`：取 selection `layer3.action.level`（BUY / OBSERVE / WAIT）
  - `warnings`：读 `outputs/run_warnings_{trade_date}.json`（confirm 的 drilldown 个股抓取失败等）+ 层对齐异常 + 配置降级
- **数据发布时点（实测 2026-07-31）**：
  - ETF 行情（东财 spot）：盘中即可用，当日 07:30 前已覆盖前一交易日
  - SW-RPS 行业行情（swsresearch/legulegu）：**T+1 上午约 10:00 前后发布**。09:25 探测仍为空，10:08 已确认
- **provisional 兜底（2026-08-03 起）**：申万日报晚发布时，`industry update` 走 **L2 provisional = realtime 基底（覆盖全部 124 申万二级，真实申万指数值）+ 同花顺 90 板块增强（78 个映射行业的成交额/量）**。报告标记 `_provisional`；次日申万确认后重算覆盖并转 confirmed（RPS 用全横截面重算）
- **结论**：上午 10:00 后运行最理想；若申万发布延迟（如周一），run-day 会输出 provisional 报告而非停在 T-1
- 若已跑过 `run-day`，申万确认数据发布后重跑一次 `make run-day` 即可补齐并转 confirmed，系统支持免重复发布

## 数据源新鲜度门控（SW-RPS）

- L1 `index_analysis_daily_sw`：单次批量接口，优先使用；目标日期无数据时返回空（KeyError '发布日期'）
- L2 provisional：`index_realtime_sw` 做全部 124 行业基底 close（上午 target=T-1 用 昨收盘、收盘后 target=T 用 最新价）；`stock_board_industry_index_ths`（同花顺 90 板块日线）增强成交额/量，映射见 `src/sw_industry_rps/ths_mapping.py`（78 映射 + 12 语义歧义跳过）
- L3 `index_hist_sw`：逐行业回退路径，极慢（约 5 分钟/124 行业）且凌晨/早上拿不到 T+1 数据，尽量避免触发
- 目标日判定：15:10 CST 前 = 上一交易日（工作日回溯，周一自动回周五），15:10 后 = 当日
- provisional 部分覆盖（≥60% active）时 calculate/report 放行并标记 `_provisional`；`industry run-day --require-confirmed` 可恢复严格模式
- 探测机制已内置在 `industry update` 中，正常情况无需手动干预

## 国金账户可交易池（黑名单机制）

- **默认全部可交易**，仅在实际交易中发现无法交易后加入黑名单（`config/guojin_tradable_blacklist.csv`）
- 原白名单（116 只人工验证）已归档至 `config/_legacy/guojin_tradable_verified_backup.csv`，仅作历史参考，不参与判定
- 实战维护命令：
  ```bash
  python src/main.py etf account-blacklist add --code 588000 --reason "科创板权限未开通"
  python src/main.py etf account-blacklist remove --code 588000
  python src/main.py etf account-blacklist list
  ```

## Layer ③ 交易标的筛选（selection）

- **定位**：执行对象压缩层——把 Layer①/② 结论压缩成「这个已确认主题用哪只 ETF、哪类股票交易」；不回答买多少/何时买卖（Layer 4）
- **双层架构（v0.5.0）**：
  - **Selection Engine**（`selection.py build_candidates`，纯算法）：制造事实 + Policy 决策（主题确认/ETF 候选/个股状态/表达方式/primary），输出候选对象结构（core_etf/sub_industry_etf/stock_watchlist/stock_candidates/etf_pool/observing_industries/...）
  - **Recommendation Builder**（`recommendation.py build_recommendation`，纯排版）：**不制造任何新事实**，把引擎输出按「投资决策顺序」重组为推荐结构——顶层 action + 每主题 5 块：① `today` 今天怎么做（action/expression/确认状态）→ ② `why` 为什么（观察区行业 + 参与率/HHI/Top3，未确认给「还差多少」距离）→ ③ `recommendation` 买什么（推荐 ETF + 个股）→ ④ `rationale` 为什么选它（ETF=RPS15+流动性；个股=趋势分）→ ⑤ `watchlist` 观察（未入选 + 逐个原因：流动性不足/未达趋势门/同类去重落选/风险警戒/数据滞后）。只重排版，不重算 RPS、不联网、不新筛选
- **核心输出是结构化推荐对象**（JSON，`role: recommendation` / `version: 0.5.0`，含 `engine.version` 溯源），HTML 只是可视化
- **多主题结构**：`layer3.buckets[].themes[]`（Core → AI基础设施 / Quality → 高现金流资产），逐主题独立确认与表达决策
- **表达方式决策**基于 Layer② 上涨结构：广泛上涨→ETF、龙头主导→龙头个股、扩散→ETF核心+龙头卫星、未确认→仅观察
- **确认机制显式化**：主题 confirmed = 任一焦点行业进入确认状态（`strength_level ∈ stock_selection.theme_confirm_states`，默认观察/强势）。为避免「20% 行业转强为何整主题确认」的误读，每主题输出 `confirmation_state`（BROAD_CONFIRMED / NARROW_CONFIRMED / WATCH / UNCONFIRMED）+ `confirm_evidence`（依据行业及 RPS15）+ `confirmation_breadth`（广泛/窄幅确认）+ `observing_industries`（进入观察区行业明细）；广度阈值（broad_fraction=0.5 / watch_proximity=70）在 config/indicators.yaml。**NARROW_CONFIRMED 语义**：主题仍开放整个资产池（存在性判定，不做子主题拆解），但表达决策显式标注「仅 X/N 行业支撑，宜观察」并压低表达强度（见 docs/STRATEGY_SPEC.md §7.1）
- **个股准入与主题门控（Policy）**：`stock_selection` 参数在 config/strategies.yaml（v0.9.0 起四段嵌套：`trend.qualified_score=70` / `trend.allowed_trend_states=[S,A]` / `trend.rps15_min=80` / `theme_confirm_states=[观察,强势]`）；`indicators.yaml` 只保留生成 strength_level 的 observe_threshold（Observation）——策略可调整「哪些状态算确认」，但不能改阈值定义
- **四段选筹（v0.9.0）**：Layer③ 个股/ETF 统一四段——**① trend**（趋势门，无趋势不进入买入候选）→ **② leadership**（主题内相对地位：个股按 score_trend 排名，LEADER=Top leader_rank_max / CORE=≤core_rank_max / NON_CORE；ETF 按 selection_score 排名，core_rank_max=1→LEADER、satellite_rank_max=3→CORE）→ **③ position**（历史价格分位 756 交易日，≤30% LOW / ≤70% MID / >70% HIGH；**历史低位只提高赔率，不产生趋势**）→ **④ signal**（`signal_policy` 规则顺序匹配：LEADER×LOW→STRONG_BUY、LEADER×MID→BUY、CORE×LOW→BUY、CORE×MID→WATCH、position HIGH→HOLD、fallback WAIT；ETF 复用同一词汇表）。`recommended` 由信号门控：主题确认 ∧ signal∈{STRONG_BUY,BUY}（HIGH 位不追高、CORE×MID 不推荐）；`state`（WATCH/QUALIFIED/RECOMMENDED）仍是趋势+主题资格状态，与信号分开放置，字段输出 `leadership_level / theme_rank / position_level / position_pct / position_lookback_days / signal`。position 价格历史离线读取（个股=processed CSV / ETF=raw parquet），按 trade_date 截断防 look-ahead；数据不足按中性 MID 匹配（position_level=UNKNOWN 显式保留）
- **策略语义分层**：`strategies.{ai_20,ai_ma,hc_20}` 回答「入场后持有多久/怎么退出」（回测/组合层），与四段选筹「选哪个标的」是两件事，互不重叠
- ETF 候选动态从 Layer① rotation 全市场按 `themes_two_directions.yaml` 主题关键词选（趋势门控 + 流动性 + 评分 + 去重）；`etf_pool` 为全部关键词命中池（含未达趋势门/流动性不足/同类去重落选，供「⑤ 观察」展示未入选原因）
- **个股趋势读取预计算产物**：`outputs/stock_metrics/stock_metrics_{trade_date}.parquet`（统一 schema：asset_id/trade_date/close/return_5d/return_20d/trend_score/score_trend/watch_level/action/risk_flags/volatility_20d/drawdown_20d/source/data_status/source_trade_date/lag_days）
- **Selection 默认禁止联网（v0.4.3）**：Layer③ 是纯消费/纯决策层，只读 Layer① ETF rotation + Layer② confirmation + 预计算个股趋势；缺个股输入不自动重试，按 `data_status=missing / selection_status=unavailable / reason=stock_trend_input_missing` 局部降级，不阻塞整体
- **个股行情由 run-day 自动更新**：`make run-day` 的 `stock-metrics-online` 自动增量抓取个股行情（Observation 构建，不依赖手工补数）；`make run-day-offline` 或 `stock-metrics` 仅读缓存。曾出现个股缓存停更（如长江电力 08-03→08-05 下跌被旧数据判成 100 分），stale 降级兜底后需重跑 run-day 自动补数
- **stale 降级（Policy）**：`data_status=stale` 时个股不给出 RECOMMENDED/QUALIFIED，降为 WATCH 并标记 `reason_codes=["stale_data"]`、reason「数据滞后 N 天，信号降级」——分数（事实原值）保留但推荐被抑制
- **覆盖率报告**：selection JSON/HTML 带 `coverage`（etf_reused / stock_inputs_loaded / selection_coverage / selection_coverage_pct / degraded_assets / online_fetches）
- **在线补数仅显式**：`select run --allow-online-fetch` 或 `stock-metrics --allow-online-fetch`（轻量重试：初试+1 次、缓存优先、无缓存记 missing）；run-day 始终离线
- 个股趋势按 `as_of_date = trade_date` 截断，避免使用目标日期之后的盘中/最新数据（look-ahead）
- 分层资产池：`config/stock_universe.yaml`（theme → tier → assets，bucket 由 themes_two_directions.yaml 推导），已废弃扁平 `stock_pool.csv`
- **Parity 兼容**：replay 读引擎内存输出（结构未变）；parity `_selection_entity_map` 兼容新（recommendation/watchlist/monitoring）与旧（core_etf/stock_watchlist）两种 JSON 结构
- 命令：
  ```bash
  make stock-metrics   # 构建个股趋势指标产物（Observation 层，离线读缓存，确定性）
  make select          # 构建交易候选（读 Layer①/② + 预计算个股趋势，默认禁止联网）
  make select-offline  # 强制离线
  python src/main.py select run --date 20260731   # 按目标 trade_date 精确回放
  python src/main.py select run --allow-online-fetch  # 手工在线补数
  python src/main.py select stock-metrics --allow-online-fetch  # 手工补数并落盘产物
  python src/main.py select universe   # 查看分层池
  ```

## 常用命令

```bash
make run-day          # 每日全流程
make etf-pipeline     # 仅 ETF 发现链路
make sw-rps-run-day   # SW-RPS 全流程：update(含probe)→calculate→report→confirm
make sw-rps-structure # [Layer ②] Enrichment 行业内部结构（offline 读缓存 soft-fail；--allow-online-fetch 做 Cache Refresh）
make stock-metrics     # 构建个股趋势指标产物（Observation 层，离线读缓存）
make stock-metrics-online  # 个股行情在线补数（run-day 离线不抓个股）
make select            # Layer ③ 交易候选（读预计算趋势，默认禁止联网）
make replay-single    # v0.5 单日期历史信号重放（DATE=YYYYMMDD）
make replay-parity    # v0.5 重放 + 与正式产物一致性校验
make replay-range     # v0.5 区间重放（START/END=YYYYMMDD, LAYERS=123|12）
make event-study      # v0.5.1 事件研究（SIGNALS=信号parquet）
make backtest-trades  # v0.5.2 交易层逐笔模拟（THEME=主题, EXIT=退出策略）
make backtest-sensitivity  # v0.5.2 退出规则稳健性扫描
make backtest-matrix  # v0.5.2 四组对比矩阵
make backtest-portfolio  # v0.6 共享账户组合模拟
make test             # 全部测试
```

## 产物

- ETF：`outputs/etf_signal/etf_rotation_{date}.html`（Layer ① 标题「① A股全市场 ETF轮动」，认知路径「大类资产 → 趋势ETF → 我的主题ETF」，替换原 funnel_report）+ `candidate_cards_{date}.json`
- ETF 轮动数据：`data/etf_signal/daily/rotation_{trade_date}.parquet`（全市场横截面 RPS15/20/60 + 5日排名变动，含 trade_date/run_date/data_status/source 元数据）
- 账户候选：`data/etf_signal/signals/account_candidates_{trade_date}.parquet`（趋势池 ∩ 账户池，含元数据列）
- SW-RPS 主报告（Layer ② 日报精简，v0.8.4）：`outputs/sw_industry_rps/sw_industry_rps_{date}.html`（+ `_latest.html` 指向最新，标题「② A股全市场 行业轮动」，认知路径「产业方向 → 趋势行业 → 我的主题支撑」）——「一句话 + 一张表」×3：① 一句话+Top10 产业方向表 · ② 一句话+单张行业表（行业/阶段/RPS15/RPS5/驱动）· ③ 每主题独立区块（v0.9.1：全部主题 = 头部状态 + Tier 表 + 判断 + 申万交叉证据；无 Tier 主题回退 = 头部状态 + 最强行业）。完整证据（矩阵/状态变化/124全表/确认四段/结构明细）保留在 parquet+CSV，不进日报。
- Layer ② 主题确认事实：`data/processed/sw_industry/confirmation_{trade_date}.parquet`（仅焦点行业主题确认事实，含 data_status/source/coverage/generated_at；v0.7.1 起含 RPS1/Δ5RPS15/rotation_state；v0.9.1 起为各主题的 Evidence，不再是 Gate；由主报告第三问消费渲染）
- Layer ② 统一 Tier basket 确认：`data/processed/sw_industry/tier_confirmation_{trade_date}.parquet`（v0.9.1，三个主题确认 Gate 事实：每 Tier 的 Tier Strength/上涨比例/Trend Score 中位/强趋势数/龙头贡献度 + trade_date/run_date/generated_at/data_status/source 元数据；兼容旧命名 `ai_tier_confirmation` 读取）
- Layer ② 行业内部结构产物（Enrichment）：`data/processed/sw_industry/sw_industry_structure_{trade_date}.parquet`（范围 = 趋势行业 ∪ 主题焦点行业，每行含 participation_rate/hhi/top1_share/top3_share/driver_mode/结构字段 + `structure_status` = available/insufficient/failed/not_in_scope 可审计，不静默空值）——独立于 confirmation，避免把非焦点行业混入「主题确认事实」；第二问驱动模式只消费此产物。run-day offline soft-fail 生成，联网补数走 `industry structure --allow-online-fetch`
- Layer ③ 个股趋势指标：`outputs/stock_metrics/stock_metrics_{trade_date}.parquet`（预计算趋势指标，Observation 层产物，Layer② Tier 确认 与 Layer③ Selection 共同消费）
- Layer ③ 交易候选：`outputs/selection/tradable_candidates_{date}.json`（结构化推荐对象，`role: recommendation`，`layer3.buckets[].themes[]` 每主题 5 块：today/why/recommendation/rationale/watchlist）+ `.html`（标题「③ 今日投资建议」，认知路径「策略判断 → ETF/个股选择 → 今日行动」）

## v0.5 Research（src/research/）

- **定位**：历史信号研究链路（v0.5.0）——用共享规则函数对指定 trade_date 纯离线重放 Layer①/②/③ 信号，产出 `historical_signals_{date}.parquet`（统一 schema：trade_date/entity_type/entity_code/theme/layer/rps15/trend_score/trend_state/confirmation_status/selection_status/recommended_action/signal_reason/source_trade_date/data_status/rule_version/config_hash/signal_origin）
- **子包**：`replay/`（engine + cli）、`signals/`（schema/config_hash）、`validation/`（parity）；`backtest/`（v0.5.2）与 event_study/datasets 待实现时新建
- **与 daily pipeline 分离**：只调用纯计算函数（rotation / confirmation / selection），跳过 drilldown、报表、网络
- **Parity 验收**：`research replay parity --date` 把重放结果与正式产物逐字段对比——数值（rps15 等）容差、状态字段（trend_state/confirmation_status/selection_status/recommended_action）必须完全一致；缺正式产物的层标记 not_checked
- **区间重放**：`research replay range --start --end [--layers 123|12] [--no-resume]` 逐交易日重放，输出 `historical_signals_{start}_{end}.parquet` + manifest（含每日状态 completed/degraded/failed/skipped、横截面覆盖 eligible/priced/coverage_rate）；通过 cache 预加载输入避免逐日读盘；Layer3 为慢路径可用 `--layers 12` 跳过
  - **resume**：默认跳过已完成且 rule_version+config_hash 一致的日期（每日产物存 `outputs/research/daily/`）；单日失败不终止区间（status=failed 记入 manifest）；汇总按 (trade_date, layer, entity_type, entity_code) 去重
  - **无副作用**：只写 `outputs/research/`，不覆盖正式 daily pipeline 产物（个股趋势 persist=False，不写 processed CSV）
- **已通过**：20260803（Layer1 1259/1259、Layer2 12/12、Layer3 25/25）、20260731（Layer1 1255/1255、Layer2 12/12，无正式 selection）；区间重放在已有正式日期与单日期重放完全一致
- 命令：`python src/main.py research replay single|parity|range --date/--start/--end YYYYMMDD`（`replay` 为兼容别名）

## v0.5.1 Event Study（src/research/event_study/）

- **定位**：状态转换事件的前向收益研究——验证「信号出现后资产是否倾向上涨」，不涉及交易账户
- **事件 = 状态转换，非每日快照**：Entry（off→on）/ Exit（on→off），按层定义信号态：
  - Layer1 ETF `trend_state`∈{BUY_CANDIDATE, STRONG_WATCH}；Layer2 行业 `confirmation_status`∈{观察, 强势}；Layer3 `selection_status`=RECOMMENDED
- **指标**：5/10/20/60 日前向收益、基准超额、MFE/MAE、胜率、均值/中位数；区分 entity_type/theme
- **基准**：同实体宇宙横截面中位前向收益（行业=124 行业、ETF=全市场、个股=universe；离线、全历史，替代过期 HS300 缓存）
- **非重叠样本**：同实体相邻事件间隔 ≥ horizon 交易日计数
- 命令：`research event-study --signals <parquet> [--start --end] [--layers 123] [--horizons 5,10,20,60]`

## v0.10 Expression Regime Event Study（src/research/expression_regime/）

- **定位**：验证 Layer③ 表达方式结构判断的预测力——「ETF_PRIORITY / LEADER_PRIORITY / CORE_PLUS_LEADER 这套市场结构判断，是否真的能预测下一阶段哪种表达方式更优」
- **事件 = 主题确认日 + 结构判定日**（confirmed 且 expression ≠ WATCHLIST_ONLY），对每事件同时计算三种表达的 counterfactual 前向收益（核心 ETF / 龙头个股 / 0.5+0.5 等权组合）
- **核心指标**：`hit_rate`（判定表达事后 ≥ 事后最优的比例）、`delta_best`（判定 − 事后最优，0=完美预测）、`etf_vs_stock`（组内 ETF 相对龙头超额）、`combo_vs_single`
- **结构输入源可插拔**（`structure.py`）：
  - `TierStructureInput`（默认）：历史重放，universe 个股价格 2018+ → tier 篮子结构（上涨比例 → broad、龙头贡献 → leader-dominated）近似行业结构；**历史可用但语义是「个股篮子」，非行业内部结构**
  - `IndustryStructureInput`：生产 confirmation 行业结构（participation/HHI/Top3，Enrichment），当前仅几周覆盖，用于近期冒烟/生产对照
  - 映射阈值集中在 `ExpressionRegimeSpec`（默认 broad≥0.6 参与率 / leader HHI≥0.15 或 Top3≥0.60 / Tier 上涨比例≥0.6 或龙头贡献≥0.5），与生产 `decide_expression` 语义对齐
- **个股趋势历史**（`history.py`）：对 universe 股票 processed CSV 预计算逐日 score/watch_level/action（`score_row` 与 production `score_latest_row` 逻辑一致、去掉 reason 字符串），事件日查表 O(1)——rolling 指标向后计算，截断到任意日期无 look-ahead
- **2024-01→2026-02 实测（Tier 近似，717 事件）**：总体命中率 20D 43% / 60D 39% / 120D 37%，判错代价随 horizon 扩大（-3.8% / -7.7% / -12.4%）→ **结构判断整体预测力弱**；**ETF_PRIORITY 判定有效**（n=126，命中 57-64%，组内 etf_vs_stock 恒为正）；**LEADER_PRIORITY 判定方向存疑**（n=544 占 76%，etf_vs_stock 多为正 → 这些日子 ETF 事后更优但系统判了龙头，Tier 近似 leader_contribution≥0.5 门槛过低导致过度判定）；CORE_PLUS 组合表达几乎从不事后最优（hit=0）
- 产物：`outputs/research/expression_regime/expression_regime_{start}_{end}.json/.html`；命令：`research expression-regime --start --end [--structure tier|industry] [--themes ...] [--horizons 20,60,120]`
- **生产联动修复**：`evaluate_themes` Tier Gate 分支原先把结构字段置空 → `decide_expression` 在 Tier 确认下恒塌缩 CORE_PLUS_LEADER；现补算结构字段（Enrichment 可用时恢复区分度，缺失时 soft-fail 塌缩）；顺带加 `_col_series` 容错空 confirmation 数据（研究重放发现 focus 空日 KeyError）

## v0.5.2 Trade Simulation（src/backtest/trade/）

- **定位**：逐笔交易模拟（第一轮）——独立等名义本金（每笔 1 单位，无共享现金账户），只回答「同一入场规则下，哪种退出策略更有效」
- **模块**：`strategy/entry.py`（入场：首次进入趋势信号态 + 主题行业确认成立）、`strategy/exit.py`（signal_exit / ma20_exit / fixed_horizon）、`execution/next_open.py`（T+1 开盘）、`trades.py`、`metrics.py`、`cli.py`（顶层 backtest）
- **冻结约束**：持仓期间再次 entry 不重复开仓（按上一笔持仓了结日比较）；Exit 是 Strategy Policy 动作非 Selection SELL；信号日无下一交易日价格 → 订单标记 `unfilled`；停牌/缺失开盘价显式记录；fixed_horizon 按交易日；MA20 只用判断日当日及以前数据；手续费/滑点保留配置字段（默认 0）
- **产物**：`outputs/research/backtest/trades_{theme}_{entity}_{label}.parquet` + `metrics_*.json` + `backtest_*.html`
- **实盘（2024-01..2026-05，AI 基础设施 ETF）**：fixed_horizon(20d) 852 笔 胜率 53.2% 均值 +3.23% 最强；ma20_exit 1003 笔 胜率 42.7% +2.82%；signal_exit 1657 笔 胜率 41.3% +0.56% 最弱（中位持有 3 日，过早退出）
- **第二轮稳健性**（`backtest sensitivity`，参数化 exit_configs）：
  - 固定持有期单调上升（5→60 日），非 10-30 平台——20 日非孤立尖峰但无平台区间
  - MA 扫描：ma20/ma30 PF≈2.27 最高、中位为负、大盈利贡献 ~40%（典型趋势策略特征）
  - 分年份：**2024 三种策略均负**（AI 弱势年）；排除最强年后 fixed_20 +2.08%、ma20 +1.94% 仍成立，signal_exit 转负 -0.43%
  - 分 ETF：fixed/ma 的 Top5 贡献仅 ~19%（74 只分散）；signal_exit ~40%
  - 成本：fixed_20 在 20bp 仍 +2.83% 稳健；signal_exit 边收益几乎被成本吃光（0.56%→0.16%）——确认其高换手劣势
- **Universe 范围**（`--universe-mode`，产物记录 universe_mode/universe_size/universe_config_hash）：
  - `configured`（默认）= config/stock_universe.yaml 资产池（AI 8 / HC 6）；`theme-matched` = 全市场关键词（AI 74 / HC 19）
  - 默认：backtest trades/sensitivity → configured；event-study → theme-matched（信号普适性）
- **四组矩阵（fixed_20，2024-01..2026-05）**：
  | 组 | n | 胜率 | 均值 | PF | 排除最强年 | Top5占比 |
  |---|---|---|---|---|---|---|
  | AI theme-matched | 852 | 53.2% | +3.23% | 1.98 | +2.08% | 19% |
  | AI configured | 121 | 50.4% | +2.69% | 1.75 | +1.78% | 91% |
  | HC theme-matched | 143 | 62.9% | +1.98% | 2.59 | +0.85% | 55% |
  | HC configured | 52 | 65.4% | +2.03% | 2.54 | +1.08% | 98% |
  - **高现金流是最稳健主题**：三年全正（2024/2025/2026 ✓）、胜率 65%、PF 2.5；AI 两模式 2024 均为负
  - 固定资产池捕获大部分信号（AI 池 2.69% vs 全市场 3.23%；HC 池内外接近），但 ETF 贡献高度集中（AI 91% / HC 98%），幸存者偏差风险高
  - HC 的 fixed_20 显著优于 ma20（2.0% vs 0.2-0.4%）；AI 两者接近（ma20 PF 略高）
- 命令：`python src/main.py backtest trades|sensitivity --signals <parquet> --theme ai_infrastructure --entity-type etf [--universe-mode configured|theme-matched]`

## v0.6 Portfolio Simulation（src/backtest/portfolio/）

- **定位**：共享现金账户模拟（组合层）——有限资金下整个策略组合的表现；回答「整个账户好不好」，与 v0.5.2 Trade（一笔交易好不好）分层
- **模块**：`engine.py`（账户引擎：逐日撮合/盯市）、`allocation.py`（仓位分配：equal-weight/max_positions/max_weight_per_asset）、`nav.py`（NAV+绩效+基准）、`metrics.py`（报告）、`simulate.py`（编排）
- **主题级策略配置**：`config/strategies.yaml`（策略规则按主题配置，不共用全局 Entry/Exit）——AI 主策略 fixed_20（MA20 作对照）、HC 主策略 fixed_20
- **资金规则**：initial_capital / max_positions / equal-weight / max_weight_per_asset / no_leverage / no_pyramiding / next_open / fee+slippage；不做 ATR
- **Portfolio Construction 实验**（`backtest construction`，不改入场/出场规则，只改组合构建）——单维度扫描，基线=等权/max5/AI50/cash100：
  - **Top-N 排名**（3/5/10）：实体越多越好，缩减宇宙只会更差（top_10=18.4% > top_3=8.0%）
  - **等权 vs Score-Weight**（RPS15 加权）：score-weight 总收益翻倍（18.4%→30.1%）、超额 -23.6%→-11.9%，但回撤扩大（-17.7%）
  - **Max Position**（3/5/8）：**max_8 最优**（+21.6%、回撤 -8.0%、Sharpe 0.88、Calmar 1.1）
  - **AI 比例**（50/60/70%）：越高收益越高但 Sharpe 降（0.66→0.59），ai50 风险调整最优
  - **现金比例**（100/80/60%）：满仓收益最高，降现金只降收益不降 Sharpe
  - **结论**：5 组组合优化后**全部仍跑输 HS300**（最佳超额 -11.9%）→ 本区间问题在 Strategy 而非 Portfolio；但 max_8 + score-weight 是风险调整与收益的最优组合方向
- **第一轮五条净值线**：AI-20 / AI-MA / HC-20 / Core+Quality-A（统一等权）/ Core+Quality-B（AI60%+HC40%）
- **实盘（2024-01..2026-05，1M 初始，max 5 仓，5bp 费+5bp 滑点单边）**：
  | 组合 | 成交 | 总收益 | 最大回撤 | Sharpe |
  |---|---|---|---|---|
  | AI-20 | 121 | +21.5% | -13.3% | 1.22 |
  | AI-MA | 146 | +35.5% | -13.5% | 1.49 |
  | HC-20 | 52 | +7.3% | **-3.9%** | **2.47** |
  | Core+Quality-A | 173 | **+41.4%** | -17.6% | 1.35 |
  | Core+Quality-B | 173 | +23.0% | **-11.2%** | 1.18 |
  - HC-20 风险调整最优（Sharpe 2.47、回撤最小）；AI-MA 在共享账户因再投资复利总收益高于 AI-20（对照实验保留）
  - Core+Quality-A 总收益最高但回撤最大（AI 权重高）；Mode B（60/40）以更平滑换取更低收益
- **指标**：累计/年化收益、最大回撤、Sharpe、**Calmar**、**相对 HS300（默认 sh000300 真指数）超额**、**主题收益贡献（AI vs HC）**；SVG 净值曲线含基准虚线
  - **完整日频 NAV（修复）**：统一交易日历（562 天），无交易日也记录现金/持仓市值/权益/日收益/总敞口/持仓数；年化按实际自然日跨度；Sharpe/波动基于完整日收益；最大回撤含峰值/谷底/恢复日
  - 相对 HS300：本区间（2024.1-2026.4）sh000300 累计约 +42%，多数主题策略累计跑输（AI-20 超额 -26%、HC-20 -35%、AI-MA -16%），仅 Core+Quality-A 最接近（-12%）——策略价值在风险调整（HC Sharpe 0.78、Calmar 0.70）与回撤控制，非原始超额
  - **基准切换**：默认 `--benchmark sh000300` 真指数缓存（覆盖检查，`--benchmark-fallback 510300` 仅显式回退，`--no-benchmark-fallback` 禁止静默切换）；`data benchmark refresh` 可独立在线刷新（回测本身离线）
  - **修正后指标（此前稀疏 NAV 高估了年化/Sharpe/Calmar）**：AI-20 累计 +15.9% 年化 6.5% Sharpe 0.49 Calmar 0.44；AI-MA +26.3% 10.6% 0.74 0.71；HC-20 +6.8% 2.9% 0.78 0.70（回撤 -4.1% 最小）；Core+Quality-A +30.1% 12.0% 0.66 0.56；B +19.8% 8.1% 0.62 0.62
  - 主题贡献（Core+Quality-B）：AI 18.6%（70 笔）+ HC 4.9%（33 笔）≈ 总收益 23%
- 命令：`python src/main.py backtest portfolio --signals <parquet> [--benchmark sh000300] [--modes A,B] [--fee 0.05 --slippage 0.05]`；`python src/main.py data benchmark refresh --symbol sh000300`

## v0.6.1 Strategy Specification（src/common/spec/）

- **定位**：统一策略规格事实源——「代码定义规则、配置定义策略、产物记录来源、回测验证变化」
- **Strategy = Strategy Specification + Rule Implementation + Execution Semantics + Validation Evidence**；config 单独不等于完整 Strategy（见 `docs/STRATEGY_SPEC.md`）
- **配置分层**：`themes_two_directions.yaml`（主题） / `stock_universe.yaml`（资产池） / `strategies.yaml`（主题级 entry/exit + strategy_id） / `indicators.yaml`（RPS 窗口/信号门限/确认阈值） / `execution.yaml`（fee/slippage/model） / `portfolio.yaml`（资金/持仓/权重）
- **统一 Loader**（业务代码不直接读 YAML，frozen typed + Schema 校验，生产路径无隐藏默认值）：`load_strategy_spec / load_indicator_spec / load_execution_spec / load_portfolio_spec`
- **Hash 边界**：`config_hash`=全部策略配置（order-independent）；`universe_hash`=实际资产集合（排序）；`rule_version`=v0.7.0（算法变化才改）
- **Provenance**：trades 带 `strategy_id / universe_hash / universe_config_hash / entry_score`；portfolio 资金参数来自 config（fee 5bp/slippage 5bp）
- **已迁移的硬编码**：ETF 趋势门（signal.py 80/60）、RPS 窗口（rotation.py 15/20/60）、Selection 门限（qualified 70 / gate states / min amount）、confirmation 90/80/60 → 均从 indicators.yaml 读取；backtest entry.rps15_min / portfolio 资金参数 → 从 strategies/portfolio/execution.yaml 读取
- **Parity 已验证**：Daily（20260803/20260731）、Trade（AI fixed_20 n=121 win 50.4%）、Portfolio（5 条 NAV 线）与迁移前完全一致
- 命令：`python src/main.py ...` 行为不变；改策略参数只改 config、跑 Parity 验证

## v0.7 Layer① 观察指标与三问三答报告

- **四个观察指标**（ETF 横截面，全部为 Observation 事实，保留在 `rotation_{trade_date}.parquet` 供单只 ETF 补充展示）：
  - `Trend（趋势）` = **rps15**（15 日收益横截面百分位），仍是主排序与主指标
  - `Today（今日）` = **rps1**（最新 1 日收益横截面百分位，`indicators.yaml rps.today_window`，默认 1）
  - `Velocity（动量）` = **delta_rps15**（rps15 今日 − rps15 N 个交易日前，`indicators.yaml rps.velocity_window`，默认 5）
  - `Liquidity（流动性）` = **liquidity**（最近 5 日均成交额横截面百分位；Selection 的 amount_score 口径独立不受影响）
- **仅展示，不参与排序/选择**：主排序仍按 rps15 降序；RPS1 / ΔRPS15 / liquidity 不进 Selection（Decision 仍只看 rps15 / TrendState / Amount）
- **数据质量（P0-2）**：回溯 `data_quality.flag_window`（默认 60 交易日）内任一日 |收益| ≥ `max_single_day_return`（默认 20%）→ 判定异常（份额折算/除权/异常行情）。异常资产**不参与对应窗口的 RPS 横截面排名**，原值保留并标记 `data_quality_flag=corporate_action`。`rotation` 含 `return_1d` 列
- **产物**：`rotation_{trade_date}.parquet` 含 `rps1 / delta_rps15 / liquidity / return_1d / data_quality_flag`；`daily_indicators.parquet` 同步合并；`watchlist / account_candidates / candidate_cards` 链路携带 rps20 / rps1 / delta_rps15
- **数据口径（P0-3）**：`rotation.coverage` 输出 `master_count / price_current_count / rps_eligible_count / trend_active_count`，仅供日志/审计，不进正文
- **HTML 布局（etf_rotation_{date}.html，v0.7.1 三问三答）**：
  - ① 大类资产往哪里动：`cross_asset_direction` 按跨资产方向聚合（A股宽基/A股行业主题/港股/海外权益/债券/商品黄金/现金货币），每行 RPS15中位/5日变化/趋势活跃占比/代表ETF/当前方向
  - ② 趋势活跃 ETF：`active_etf_representatives` 按方向去重、每方向一只流动性最好代表，排序 STRONG_WATCH→BUY_CANDIDATE→WATCH→RPS15→流动性，正文展示 top 40 + 新增/退出活跃简报，完整名单 `watchlist_active_{date}.csv`
  - ③ 我的主题 ETF：config/stock_universe.yaml 固定主题池（theme_etf+sub_industry_etf），按主题分组，RPS15/20/60 + RPS1/Δ + 流动性 + 状态
  - 页脚：日期｜数据状态｜异常数量（审计信息只进页脚一行）
- **计算位置**：`rotation.compute_rotation_metrics`（rps1/delta_rps15/liquidity/return_1d/data_quality_flag）、`rotation.cross_asset_direction`（①）、`rotation.active_etf_representatives`（②）、`rotation._direction_key`（方向去重键）；`rotation.coverage`（口径）。报告纯排版消费
- **Event Study 前置**：rps1 / delta_rps15 随每日 `rotation_{trade_date}.parquet` 累积落盘；运行满 1 个月后可用 `research replay range` + `research event-study` 验证「RPS1>95 / ΔRPS>20 之后前向收益是否有统计优势」，**确认有优势才考虑进入 Layer③**（当前不进入）
- **rule_version**：v0.7.1（报告消费/排版重写，算法与 parquet 不变）
- 命令：`make etf-calculate` / `make etf-pipeline` 行为不变，产物自动含新列与新报告
