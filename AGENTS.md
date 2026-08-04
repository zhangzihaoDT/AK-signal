# AKsignal — Agent 操作手册

## 信号日期语义（v0.4.3）

- **统一约定**：Layer1（ETF）、Layer2（SW-RPS 确认）、Layer ③（Selection）一律以 **trade_date（最近完整交易日）** 作为信号分区日期与文件名日期。
- 盘中运行时（16:30 前 / SW 15:10 前），trade_date 自动锚定到上一完整交易日（周一回溯到上周五）；收盘后取当日。
- `run_date`（运行日）与 `generated_at`（生成时间）**仅承担审计语义**，写入 parquet 元数据，不参与文件命名与消费对齐。
- 每个产物 parquet 内带元数据列：`trade_date` / `run_date` / `generated_at` / `data_status`（confirmed/provisional）/ `source`（ETF=em；SW=sw_daily / sw_realtime+ths_enrichment / sw_hist）。
- Selection 消费逻辑：读取各层**元数据 trade_date** 做共同对齐，输出 `alignment`（aligned / stale_industry / stale_etf + industry_lag_days）。ETF 与 SW 不同步时显式标记滞后，不静默组合。
- `select run --date YYYYMMDD` 的 `--date` 语义 = **目标 trade_date**，字面按 `rotation_{date} / account_candidates_{date} / confirmation_{date}` 精确加载、缺文件不降级。
- 盘中快照与日频收盘信号分离：日频 run-day 只输出完整交易日横截面；盘中版本（snapshot_type=intraday）为独立模式，未启用。

## 多主题框架（v0.4.3，两方向）

- **单一事实源**：`config/themes_two_directions.yaml` 定义 bucket → theme → 申万二级行业焦点组 + ETF 关键词。Layer ①②③ 共同消费，不再硬编码。
  - Core（核心，长期收益）：**AI 基础设施**（注意不是 AI 应用，不含软件开发/IT 服务）
  - Quality（质量，高现金流防守）：**高现金流资产**（电力（火电·水电·核电）/ 三大运营商 / 公用事业（高速·公路·港口））
- **ETF 归属**：按 `themes_two_directions.yaml` 的 `etf_keywords` 匹配（bucket 顺序优先），不再依赖单一 is_tech 焦点组。`is_tech` 列保留作向后兼容。
- **Layer ②**：确认输出按 bucket/theme 分层，confirmation parquet 新增 `bucket` / `bucket_label` 列，报告按 Bucket → Theme 展示。
- **Layer ③**：候选对象 JSON 结构升级为 `layer3.buckets[].themes[]`（含 confirmed/expression/core_etf/sub_industry_etf/stock_watchlist/stock_candidates）。
- 主题资产池：`config/stock_universe.yaml` 保持 theme → tier → assets；bucket 归属由 `config/themes_two_directions.yaml` 推导，不在两个配置重复维护。
- **跨主题资产语义**：同一资产可在多个 theme 注册（如 通信ETF 同时属 ai_infrastructure 与 high_cashflow）。
  - 动态 ETF 候选按 `themes_two_directions.yaml` 关键词**首个命中**归属单一主题（bucket 顺序优先，不做跨主题复制）；
  - 固定池资产跨主题注册时，`recommended_actions` 按 (asset_type, code) **去重**，保留首个 bucket 的 primary 归属；
  - Position 权重归属属于 Layer 4（v0.4.3 不做），跨主题清单由 `select run` 输出 `config_issues.cross_theme_assets` 暴露。
- **配置降级**：asset pool 存在未注册 theme（不在 themes_two_directions.yaml）时，其资产不进入任何候选。默认告警并标记 `degraded` 继续发布（报告顶部显示配置降级提示）；`select run --strict` 可中止发布。
- **Future Themes（Not Enabled）**：Resource Cycle（有色/钢铁/煤炭）、High-end Equipment（高端装备）、Aerospace/Shipping（航空航天/船舶）等不在当前两方向，仅是「未启用」而非被否定，可在 `themes_two_directions.yaml` 重新打开；若启用商品类 theme，表达的是「权益/ETF 代理」（ETF + 申万有色行业 + 资源股），非商品期货趋势系统，须标 `maturity: PARTIAL`，不输出增配信号。

## 每日运行（run-day）

- **唯一入口**：`make run-day`（etf-update → sw-rps-update → etf-calculate → sw-rps-calculate → etf-pipeline → sw-rps-report → sw-rps-confirm → **select-inputs** → **select** → **run-day-check**）
- **run-day 默认含 Layer ③**：selection 是 run-day 默认流程的固定环节；`make select` / `make select-offline` 保留为独立执行入口
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
- 原白名单（116 只人工验证）已归档至 `config/guojin_tradable_verified_backup.csv`，仅作历史参考，不参与判定
- 实战维护命令：
  ```bash
  python src/main.py etf account-blacklist add --code 588000 --reason "科创板权限未开通"
  python src/main.py etf account-blacklist remove --code 588000
  python src/main.py etf account-blacklist list
  ```

## Layer ③ 交易标的筛选（selection）

- **定位**：执行对象压缩层——把 Layer①/② 结论压缩成「这个已确认主题用哪只 ETF、哪类股票交易」；不回答买多少/何时买卖（Layer 4）
- **核心输出是结构化候选对象**（JSON），HTML 只是可视化
- **多主题结构**：`layer3.buckets[].themes[]`（Core → AI基础设施 / Quality → 高现金流资产），逐主题独立确认与表达决策
- **表达方式决策**基于 Layer② 上涨结构：广泛上涨→ETF、龙头主导→龙头个股、扩散→ETF核心+龙头卫星、未确认→仅观察
- ETF 候选动态从 Layer① rotation 全市场按 `themes_two_directions.yaml` 主题关键词选（趋势门控 + 流动性 + 评分 + 去重）
- **个股趋势读取预计算产物**：`outputs/selection_inputs/stock_metrics_{trade_date}.parquet`（统一 schema：asset_id/trade_date/close/return_5d/return_20d/trend_score/score_trend/watch_level/action/risk_flags/volatility_20d/drawdown_20d/source/data_status/source_trade_date/lag_days）
- **Selection 默认禁止联网（v0.4.3）**：Layer③ 是纯消费/纯决策层，只读 Layer① ETF rotation + Layer② confirmation + 预计算个股趋势；缺个股输入不自动重试，按 `data_status=missing / selection_status=unavailable / reason=stock_trend_input_missing` 局部降级，不阻塞整体
- **覆盖率报告**：selection JSON/HTML 带 `coverage`（etf_reused / stock_inputs_loaded / selection_coverage / selection_coverage_pct / degraded_assets / online_fetches）
- **在线补数仅显式**：`select run --allow-online-fetch` 或 `select inputs --allow-online-fetch`（轻量重试：初试+1 次、缓存优先、无缓存记 missing）；run-day 始终离线
- 个股趋势按 `as_of_date = trade_date` 截断，避免使用目标日期之后的盘中/最新数据（look-ahead）
- 分层资产池：`config/stock_universe.yaml`（theme → tier → assets，bucket 由 themes_two_directions.yaml 推导），已废弃扁平 `stock_pool.csv`
- 命令：
  ```bash
  make select-inputs   # 构建个股趋势输入产物（离线读缓存，确定性）
  make select          # 构建交易候选（读 Layer①/② + 预计算个股趋势，默认禁止联网）
  make select-offline  # 强制离线
  python src/main.py select run --date 20260731   # 按目标 trade_date 精确回放
  python src/main.py select run --allow-online-fetch  # 手工在线补数
  python src/main.py select inputs --allow-online-fetch  # 手工补数并落盘产物
  python src/main.py select universe   # 查看分层池
  ```

## 常用命令

```bash
make run-day          # 每日全流程
make etf-pipeline     # 仅 ETF 发现链路
make sw-rps-run-day   # SW-RPS 全流程：update(含probe)→calculate→report→confirm
make select-inputs    # 构建个股趋势输入（离线读缓存）
make select           # Layer ③ 交易候选（读预计算趋势，默认禁止联网）
make replay-single    # v0.5 单日期历史信号重放（DATE=YYYYMMDD）
make replay-parity    # v0.5 重放 + 与正式产物一致性校验
make test             # 全部测试
```

## 产物

- ETF：`outputs/etf_signal/etf_rotation_{date}.html`（Layer ① A股全市场 ETF 轮动，替换原 funnel_report）+ `candidate_cards_{date}.json`
- ETF 轮动数据：`data/etf_signal/daily/rotation_{trade_date}.parquet`（全市场横截面 RPS15/20/60 + 5日排名变动，含 trade_date/run_date/data_status/source 元数据）
- 账户候选：`data/etf_signal/signals/account_candidates_{trade_date}.parquet`（趋势池 ∩ 账户池，含元数据列）
- SW-RPS：`outputs/sw_industry_rps/sw_industry_rps_{date}.html`（+ `_latest.html` 指向最新）
- Layer ② 主题确认：`outputs/sw_industry_rps/sw_industry_confirmation_{date}.html` + `data/processed/sw_industry/confirmation_{trade_date}.parquet`（含 data_status/source/coverage/generated_at，多主题 bucket/theme 行业证据共振/龙头广度/背离）
- Layer ③ 个股趋势输入：`outputs/selection_inputs/stock_metrics_{trade_date}.parquet`（预计算趋势指标，Selection 只读此产物）
- Layer ③ 交易候选：`outputs/selection/tradable_candidates_{date}.json`（结构化候选对象，`layer3.buckets[].themes[]`）+ `.html`（可视化）

## v0.5 Research（src/research/）

- **定位**：历史信号研究链路（v0.5.0）——用共享规则函数对指定 trade_date 纯离线重放 Layer①/②/③ 信号，产出 `historical_signals_{date}.parquet`（统一 schema：trade_date/entity_type/entity_code/theme/layer/rps15/trend_score/trend_state/confirmation_status/selection_status/recommended_action/signal_reason/source_trade_date/data_status/rule_version/config_hash/signal_origin）
- **子包**：`replay/`（engine + cli）、`signals/`（schema/config_hash）、`validation/`（parity）；`backtest/`（v0.5.2）与 event_study/datasets 待实现时新建
- **与 daily pipeline 分离**：只调用纯计算函数（rotation / confirmation / selection），跳过 drilldown、报表、网络
- **Parity 验收**：`research replay parity --date` 把重放结果与正式产物逐字段对比——数值（rps15 等）容差、状态字段（trend_state/confirmation_status/selection_status/recommended_action）必须完全一致；缺正式产物的层标记 not_checked
- **区间重放**：`research replay range --start --end [--layers 123|12]` 逐交易日重放，输出 `historical_signals_{start}_{end}.parquet` + manifest（含每日横截面覆盖 eligible/priced/coverage_rate）；通过 cache 预加载输入避免逐日读盘；Layer3 为慢路径可用 `--layers 12` 跳过
- **已通过**：20260803（Layer1 1259/1259、Layer2 12/12、Layer3 25/25）、20260731（Layer1 1255/1255、Layer2 12/12，无正式 selection）；区间重放在已有正式日期与单日期重放完全一致
- 命令：`python src/main.py research replay single|parity|range --date/--start/--end YYYYMMDD`（`replay` 为兼容别名）
