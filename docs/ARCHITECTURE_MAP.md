# AKSignal Canonical Architecture Map

> **定位**：当前代码库能力的 **canonical 状态图**（living snapshot）——模块 → 研究对象 → 回答什么问题 → 输入/产物/报告的单一事实映射。
> 更新日期：2026-09-03（随代码演化修订；过时的叙事/目标态描述看 `docs/ARCHITECTURE.md`）。
> 快速入口：`make run-day`（每日全链）；`python src/main.py <subsystem>`。

---

## 0. 五类计算主体（模块 → 对象 → 问题）

| 编号 | 主体 | 模块 | 研究对象 | 回答 | 输入 | 状态 |
|---|---|---|---|---|---|---|
| ① | Layer① ETF Rotation | `src/etf_signal/rotation` | 全市场 ETF（~1554） | 什么资产/主题在形成趋势 | EM/Sina 行情 → `raw/{code}.parquet` | Observation（制造事实） |
| ② | Layer② Industry / Tier 确认 | `src/sw_industry_rps` | 124 申万二级 → 主题 Tier | ETF 趋势有无产业支撑 / 主题是否确认 | SW 日频/realtime + `stock_metrics` | Observation（制造事实） |
| ③ | Layer③ Selection | `src/selection` | 主题 ETF（每日发布 ETF-only；engine 保留个股供 research） | 已确认主题选哪只 ETF 表达 + Lane Validation | Layer①② + three_lane + stock_metrics（离线） | Decision（消费事实） |
| L1 | Lane 1 ETF 趋势池 | `etf_signal/signal`（watchlist） | 全市场 ETF | 谁已是趋势（状态池） | Layer① rotation | Observation（ETF 产品状态） |
| L2 | Lane 2 底部/修复 | `src/research/etf_bottom` | 全市场 reliable ETF | 价格位置 / 赔率如何（底部域 + Repair-Retest V1） | raw 缓存（离线） | Observation（研究 → Application） |
| L3 | Lane 3 趋势转换 | `src/research/trend_transition` | 全市场 ETF | 处于什么生命周期阶段 | `v1_signal_daily` + raw | Observation（研究 → Application） |
| D | ETF State Fusion / 三 Lane 归集 | `etf_signal/three_lane` | 全市场 ETF | ETF 逐只「Trend × Position × Lifecycle」状态视图 | watchlist × v1 × state（三份已落盘） | Fact Aggregation / 归集层，不做 Policy |

> 命名提示（两套「层」同名，勿混）：**Layer①②③** = 主链 Observation/Decision；**Lane1/2/3** = ETF 三维研究引擎。
> Lane1 与 Layer① 同源不同产物：Layer①=横截面 RPS；Lane1=趋势状态池（`trend_state`）。
> Trend Engine（`src/trend_engine`）= 个股行情/技术评分，被 Layer② Tier 确认与 Layer③ 消费，非独立报告入口。

## 1. Observation / Decision / Lane 分层

```
 全市场 ETF 行情（raw）──────────────► 行业行情（SW）
        │                                       │
 ① Layer① ETF Rotation                 ② Layer② Industry/Tier
  · 横截面 RPS15/20/60 (+RPS1/Δ/liq)     · 行业 RPS + 主题确认 + Tier Gate
  · data_quality_flag (corporate_action)  · 只做 Observation
  ───────────── Observation ──────────────
        │ 旋转事实                           │ 确认事实
        ▼                                   ▼
 ③ Layer③ Selection（Decision，离线）═══════╪═════
  ETF: trend门→leadership→position(MA60)→signal  │
  (个股现状亦在此，规划收敛 ETF-only)              │
        │                    Lane1 trend_state    │
        ▼                    (同源状态池，③ 另读)  │
  today / why / recommendation / watchlist        │
        ▼
 ════ Lane 2（位置/赔率）· Lane 3（生命周期）═══════
        └──── three_lane join（L1×L2×L3）──► ETF 报告 ④ 路径视图
        ▼
 run-day-check（Final Validation）→ status/action/warnings
```

## 2. 每日 run-day 产物（`make run-day` 顺序）

```
etf-update → sw-rps-update → etf-calculate → sw-rps-calculate → etf-pipeline
  → stock-metrics-online → sw-rps-confirm → sw-rps-report → select
  → etf-bottom-scan → etf-refresh-v1 → trend-transition-state → three-lane → run-day-check
```

| 步骤 | 主报告/产物 | 说明 |
|---|---|---|
| etf-update | `data/etf_signal/raw/*` + `diagnostics/source_audit_{target}.json` | Data Acquisition Performance V1（增量/熔断/Sina 并发） |
| sw-rps-* | `data/processed/sw_industry/confirmation|tier_confirmation|structure_{date}.parquet` | Layer② 事实 |
| etf-calculate/pipeline | `data/etf_signal/daily/rotation_{date}.parquet` · `signals/account_candidates_{date}.parquet` · `outputs/etf_signal/{etf_rotation_{date}.html, watchlist_active_{date}.csv, candidate_cards_{date}.json}` | Layer①/Lane1 |
| stock-metrics-online | `outputs/stock_metrics/stock_metrics_{date}.parquet` | 个股趋势（Layer② Tier 与 ③ 共用） |
| select | `outputs/selection/tradable_candidates_{date}.{json,html}`（meta.scope=etf_only） | Layer③（ETF-only 发布；Lane Validation 硬 gate） |
| etf-bottom-scan | `outputs/research/etf_bottom/scan_{date}.{html,json,csv,parquet}` | Lane2 每日 Application |
| etf-refresh-v1 | `outputs/research/etf_bottom/backtest_v1/v1_signal_daily.parquet` | Lane2/3 历史态 |
| trend-transition-state | `outputs/research/trend_transition/trend_transition_state_{date}.{parquet,json}` | Lane3 每日 Application（无 HTML） |
| three-lane | `outputs/etf_signal/three_lane_{date}.{parquet,csv}` + 重渲染 ETF 报告 ④ | L1×L2×L3 join |
| run-day-check | 控制台 | status(CONFIRMED/PROVISIONAL) · action · warnings |

## 3. 报告输出清单（HTML/可读）

### 每日正式报告（run-day 产物）
| 报告 | 路径 | 结构 |
|---|---|---|
| ① ETF 轮动 | `outputs/etf_signal/etf_rotation_{date}.html` | ①大类资产往哪里动 ②趋势活跃 ETF ③我的主题 ETF（④ 三 Lane 路径视图，仅当 three_lane 与报告日对齐时注入） |
| ② 行业轮动 | `outputs/sw_industry_rps/sw_industry_rps_{date}.html`（CONFIRMED 同步 `_latest.html`；provisional 只落 `_{date}_provisional.html`） | ①产业方向Top ②行业/阶段表 ③每主题 Tier+判断+申万证据 |
| ③ 今日投资建议 | `outputs/selection/tradable_candidates_{date}.html` | 01 今日结论→02 主题状态→03 为什么→04 怎么表达→05 风险与变化→06 决策审计 |
| Lane2 全市场扫描 | `outputs/research/etf_bottom/scan_{date}.html` | Layer A Market Bottom Map · B Repair-Retest Scanner · C Historical Odds · 口径与限定 |
| 三 Lane 合成 | `outputs/etf_signal/three_lane_{date}.{csv,parquet}` | ETF｜Lane2 底部/target｜Lane3 迁移｜离开底部天数｜Lane1 趋势 |

### 研究报告（一次性/累计，`outputs/research/`）
- **etf_bottom（Lane2 研究）**：`study1_price_bottom` · `study1b_deep_stress` · `price_map_{date}` · `state_odds_result` · `state_odds_drilldown` · `bottom_episodes` · `context_matching` · `context_replication` · `repair_structure` · `current_odds_report` · `backtest_v1/v1_backtest_report`
- **trend_transition（Lane3 研究）**：`study3a_report`（断点）· `study3b_report`（预测 FAIL）· `study3c_report`（状态机 PASS，冻结 V1）
- **backtest**：`backtest_{theme}_{entity}_{range}*.html`（trades）· `sensitivity_*` · `matrix_*` · `construction_*` · `metrics_*.json` · `trades_*.parquet`
- **portfolio**：`portfolio_{range}.{html,json}`（NAV/回撤/Sharpe/超额）
- **其他研究**：`event_study_*` · `expression_regime_*` · `baskets/basket_compare` · `watchlist_compare` · `scania_china_watch` · `aug2026/aug2026_report` · replay 逐日信号 `research/daily/` + `historical_signals_{range}.parquet`
- **历史遗留（保留不删，已被新链取代）**：`etf_signal/funnel_report_*` · `sw_industry_confirmation_*`（独立旧版）· `etf_signal/stock_trend/trend_report_*`（并入 trend_engine 前）

## 4. 数据层清单（`data/`）

| 路径 | 内容 |
|---|---|
| `data/etf_signal/master/{etf_master,core_universe}.parquet` | 全市场清单/核心池 |
| `data/etf_signal/raw/{code}.parquet` | 单只日行情（原子写；Performance V1 增量） |
| `data/etf_signal/daily/rotation_{trade_date}.parquet` | Layer① 横截面（RPS + 元数据 trade_date/run_date/data_status/source） |
| `data/etf_signal/signals/account_candidates_{trade_date}.parquet` | 趋势池 ∩ 账户池（含 trend_state） |
| `data/etf_signal/backfill/` · `manifests/` · `positions/` | 缺口分类 checkpoint / 运行记录 / 仓位（L4 预留） |
| `data/etf_signal/diagnostics/source_audit_{target}.json` | Data Acquisition 观测（per-source/circuit/cache_hits） |
| `data/processed/sw_industry/{confirmation,tier_confirmation,ai_tier_confirmation,sw_industry_structure}_{trade_date}.parquet` | Layer② 事实 |
| `outputs/stock_metrics/stock_metrics_{trade_date}.parquet` | 个股趋势 Observation（stock-metrics 构建） |
| `config/` | 单一事实源（主题/资产池/策略/指标/执行/组合）；`market_data.yaml` 抓取参数不入 config_hash |

## 5. 关键语义（跨模块约束，勿静默破坏）

1. **trade_date 分区**：Layer①/②/③ 与 Lane Application 一律按 trade_date 分区/命名；run_date/generated_at 仅审计。盘中锚 T-1，收盘后取当日。
2. **alignment**：Selection 按各层元数据 trade_date 对齐（`aligned/stale_industry/stale_etf`）。Lane 产物同理：exact `three_lane_{trade_date}` 存在 → 对齐消费；exact 文件缺失 → lane-less（`lane_trade_date=None / lane_lag_days=None`），**禁止自动 fallback 到前后日期**。
3. **`*_latest.*` 别名**：仅 CONFIRMED 落盘后更新（现仅 Layer② `sw_industry_rps_latest.html`）；PROVISIONAL 只落带日期文件。
4. **Fact 不可变**：Observation 数字原值保留，Policy 接受/拒绝单独标注；Decision 禁止联网/重算/覆盖 Observation 字段。
5. **config_hash / rule_version**：`market_data.yaml`（Data Acquisition）不入 strategy config_hash，抓取参数变化不触发 replay parity 失效；算法变化才 bump `rule_version`。
6. **Layer③ ETF-only + Lane Validation（v0.11，生产中）**：Layer③ 每日发布输出收敛 ETF-only
   （`select run` 固定 `include_stocks=False`，`meta.scope=etf_only`，报告无个股区块）；个股保留在
   Layer② Tier 作行业确认输入，engine 默认 `include_stocks=True` 供 research replay/parity。
   ETF State Fusion（three_lane）以 exact 日期对齐消费，缺失 → lane-less 不 gate。
   Validation：**L2 数据可靠性=硬 gate**（`lane2_reliable_360=False` → 不可推荐，
   reason=lane2_unreliable）；L2 结构=soft；L3 阶段=纯 context（不 gate）。`RULE_VERSION`=v0.11.0。
7. **ETF 技术详情证据栈（Evidence Stack，v0.11）**：单只 ETF 的可审计「技术详情 · 底层证据」由
   三块产物聚合而成，但**不是无冗余的最小集，也不是仅此三块才完整**：

   - **聚合视图（curated，可读口径）**：Layer③ Selection candidate facts
     （`recommendation.etf` / `etf_monitoring` / `watchlist.etf` 条目）——rotation 核心
     （rps15/20/60/1、Δrps15、returns、liquidity）+ technical_diagnostics + blocking/
     data_quality_flags + 四段（leadership/position/signal）+ 主题 + lane 透传子集
     （6 字段）。**三块中的 hub**：已内嵌下两块的一部分字段（同源于 rotation 与 three_lane）。
   - **产品/账户层（Layer① 账户候选卡）**：`candidate_cards_{date}.json` —— trend 变化
     （trend_change/amount_change）、产品/账户门控（tradable + liquidity/size/history/premium
     gates）、risks、validation.next_step；scope=账户候选集（非全市场）；**Selection 不消费它**
     （属 Layer① 独立产物），其趋势字段与 Selection 重复是审计冗余而非事实双源。
   - **生命周期校验层（three_lane 原始事实全集）**：`three_lane_{date}.parquet` ——
     lane1 trend_state、lane2 bottom/target/reliable、lane3 transition/days +
     `_watchlist_date` 对齐标记；Selection 只透传其子集，需要 lane 原始全量时读本文件。

   **约束**：① 三块刻意有冗余（展示口径 vs 审计口径），不互为唯一真源；② scope 不同
   （candidate_cards=1291 账户池、three_lane≈1290 reliable、Selection=主题关键词命中池），
   逐 ETF 完整性只在三块共同覆盖时成立；③ 管道仍有未进三块的原始列（rotation
   `rank15/return_10/15/60/liquidity百分位/data_quality_flag`、account `trend_change/
   amount_change`、three_lane `pos120/confirmed_long_term_bottom/lane1/_watchlist_date`、
   Lane2 in-domain scan odds），字节级完整需连同 rotation/scan parquet 一起读。

## 6. 命令面（`make` / `python src/main.py`）

- 每日：`make run-day`（/ `run-day-offline` / `run-day-check`）；分步 `make etf-update|etf-calculate|etf-pipeline|sw-rps-*|stock-metrics(-online)|select|etf-bottom-scan|etf-refresh-v1|trend-transition-state|three-lane`
- 研究：`research replay single|parity|range` · `research event-study` · `research expression-regime` · `research etf-bottom --scan|--price-map|--state-odds|--drilldown|--episodes|--context-match|--context-replication|--repair|--current-eval|--refresh-v1` · `research trend-transition study3a|3b|3c|state`
- 回测：`backtest trades|sensitivity|matrix|construction|portfolio`（数据补数：`data benchmark refresh`）
- 验证：`make test`（865+ 用例）；`run-day-check`（产物完整性/状态聚合）

> 本图为能力盘点快照；随代码演化更新。修订时保持「模块→对象→问题→产物」四元组格式不变。
