PYTHON = .venv/bin/python
SRC_MAIN = src/main.py

.DEFAULT_GOAL = help

.PHONY: help run-day run-day-offline run-day-check \
	etf-bootstrap etf-bootstrap-core etf-update etf-calculate \
	etf-classify etf-layer1 etf-watchlist etf-account etf-account-blacklist etf-card etf-pipeline \
	etf-refresh-v1 three-lane opportunity-radar \
	etf-retry-uncovered \
	sw-rps-run-day sw-rps-update sw-rps-calculate sw-rps-confirm sw-rps-report \
	sw-rps-bootstrap sw-rps-validate sw-rps-drilldown sw-rps-structure \
	select select-offline \
	stock-metrics stock-metrics-online \
	replay-single replay-parity replay-range event-study \
	backtest-trades backtest-sensitivity backtest-matrix backtest-portfolio backtest-construction \
	etf-bottom etf-bottom-price-map etf-bottom-odds etf-bottom-drilldown etf-bottom-episodes etf-bottom-context etf-bottom-context-replication etf-bottom-repair etf-bottom-current-eval etf-bottom-scan etf-bottom-v1-backtest etf-mapping-feasibility trend-transition-3a trend-transition-3b trend-transition-3c trend-transition-state \
	test install clean

help: ## 显示帮助信息
	@grep -E '^[-a-zA-Z_0-9]+:.*## ' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*## "}; {printf "  make %-20s %s\n", $$1, $$2}'

# ── 专项重试 ─────────────────────────────────────────────────

etf-retry-uncovered: ## 专项重试未覆盖 ETF（分批+熔断器重置）
	$(PYTHON) $(SRC_MAIN) etf retry-uncovered

etf-preflight: ## [保护] 数据源抽样探测：routing/viability/最新bar（30 秒判断是否 routing 层问题）
	$(PYTHON) $(SRC_MAIN) etf preflight

etf-backfill: ## [保护] 目标日缺口回填：分类 + checkpoint/resume + 耗时统计
	$(PYTHON) $(SRC_MAIN) etf backfill

# ── 每日市场扫描（唯一入口） ──────────────────────────────────────

run-day: ## 每日全流程：Observation 自动联网构建（ETF/行业/个股）→ Decision 离线消费 → 三Lane 合成 → Final Validation
	$(MAKE) etf-update
	$(MAKE) sw-rps-update
	$(MAKE) etf-calculate
	$(MAKE) sw-rps-calculate
	$(MAKE) etf-pipeline
	$(MAKE) stock-metrics-online  # Observation 构建：个股行情先建（Tier 确认消费，不依赖手工补数）
	$(MAKE) sw-rps-confirm     # 落 confirmation + tier_confirmation parquet（第三问消费）
	$(MAKE) sw-rps-report      # 再生成报告（消费 confirmation + tier + structure）
	$(MAKE) select                 # Decision 消费：离线、确定性
	$(MAKE) etf-bottom-scan        # [Lane2] 全市场底部扫描（Application，只读 raw 缓存，不联网）
	$(MAKE) etf-refresh-v1         # [Lane2] 轻量刷新 v1_signal_daily（Lane 3 状态机输入，全历史重算）
	$(MAKE) trend-transition-state # [Lane3] 状态分类 Application（读冻结 YAML，自动取最新 trade_date）
	$(MAKE) three-lane             # 三 Lane 合成表 + 重渲染 ETF 报告（④ 三 Lane 路径视图）
	$(MAKE) opportunity-radar      # [Observation] Theme 外机会 Radar（消费 three_lane Lane2/Lane3 事实，不联网）
	$(MAKE) run-day-check

run-day-offline: ## 离线重放/CI：只读已落盘 Observation，不联网抓取（严格重放请用 research replay）
	$(MAKE) etf-update
	$(MAKE) sw-rps-update
	$(MAKE) etf-calculate
	$(MAKE) sw-rps-calculate
	$(MAKE) etf-pipeline
	$(MAKE) stock-metrics          # 离线：仅用缓存，缺失/过期由 stale 降级兜底
	$(MAKE) sw-rps-confirm     # 落 confirmation + tier_confirmation parquet（第三问消费）
	$(MAKE) sw-rps-report      # 再生成报告（消费 confirmation + tier + structure）
	$(MAKE) select
	$(MAKE) etf-bottom-scan
	$(MAKE) etf-refresh-v1
	$(MAKE) trend-transition-state
	$(MAKE) three-lane
	$(MAKE) opportunity-radar      # [Observation] Theme 外机会 Radar（消费 three_lane，不联网）
	$(MAKE) run-day-check

run-day-check: ## [Final Validation] 校验 run-day 各层产物并输出最终结果
	$(PYTHON) $(SRC_MAIN) final-check

# ── ETF 全市场扫描（主系统） ────────────────────────────────────

etf-bootstrap: ## 初始化 ETF Master 并拉取全量历史数据
	$(PYTHON) $(SRC_MAIN) etf bootstrap

etf-bootstrap-core: ## 核心 Universe 初始化（推荐）
	$(PYTHON) $(SRC_MAIN) etf bootstrap-core

etf-update: ## 增量更新 ETF 日行情
	$(PYTHON) $(SRC_MAIN) etf update

etf-calculate: ## 计算 ETF 技术指标与 RPS
	$(PYTHON) $(SRC_MAIN) etf calculate

etf-classify: ## ETF 资产类别分类
	$(PYTHON) $(SRC_MAIN) etf classify

etf-layer1: ## 全市场资产热度分布
	$(PYTHON) $(SRC_MAIN) etf layer1

etf-watchlist: ## 生成趋势 Watchlist
	$(PYTHON) $(SRC_MAIN) etf watchlist

etf-account: ## 映射至国金账户可交易池
	$(PYTHON) $(SRC_MAIN) etf account

etf-account-blacklist: ## 维护国金不可交易黑名单（add/remove/list）
	$(PYTHON) $(SRC_MAIN) etf account-blacklist

etf-card: ## 生成 ETF 候选信息卡片
	$(PYTHON) $(SRC_MAIN) etf card

etf-pipeline: ## 完整发现链路：watchlist → account → card → JSON+CSV+HTML
	$(PYTHON) $(SRC_MAIN) etf pipeline

etf-refresh-v1: ## [Lane2] 轻量刷新 v1_signal_daily.parquet（每日全历史重算，Lane 3 状态机输入）
	$(PYTHON) $(SRC_MAIN) research etf-bottom --refresh-v1

three-lane: ## 三 Lane 合成表（watchlist × v1_signal_daily × trend_transition_state）+ 重渲染 ETF 报告（④ 三 Lane 路径视图）
	$(PYTHON) $(SRC_MAIN) etf three-lane $(if $(TL_DATE),--date $(TL_DATE),)

opportunity-radar: ## [Observation/Discovery] Theme 外机会 Radar（消费 Layer① × Theme Mapping × Lane2/3 事实，不联网）。RADAR_DATE=YYYYMMDD 可选钉定目标日
	$(PYTHON) $(SRC_MAIN) opportunity-radar run $(if $(RADAR_DATE),--date $(RADAR_DATE),)

# ── SW-RPS 行业信号模块（ETF 子模块） ──────────────────────────
# 结构：confirm 是 calculate 的下游，复用 calculate 产出的 RPS/指标，
#       不维护第二套指标计算逻辑。

sw-rps-run-day: ## SW-RPS 全流程：update→calculate→confirm→structure(offline enrichment,soft-fail)→report
	$(MAKE) sw-rps-update      # 1. 获取行情（内置 freshness probe）
	$(MAKE) sw-rps-calculate   # 2. 计算全量申万二级 RPS
	$(MAKE) sw-rps-confirm     # 3. Layer ② 主题确认（落 confirmation parquet，下游 report 第三问消费）
	$(MAKE) sw-rps-report      # 4. report 前置 offline structure enrichment（soft-fail），再生成报告（消费 confirmation）

# SW-RPS 日期锚点：SW_DATE=YYYYMMDD 从 Makefile 一路透传到 update/calculate/confirm/report。
# 空（默认）时各 stage 用自身最新/默认日；设置后全部按同一目标日推进（对齐 TL_DATE/SCAN_DATE/L3_DATE 写法）。
SW_DATE ?=

sw-rps-run-day-provisional: ## SW-RPS 全流程（允许 provisional 数据）
	$(PYTHON) $(SRC_MAIN) industry run-day --allow-provisional $(if $(SW_DATE),--target-date $(SW_DATE),)

sw-rps-update: ## 增量拉取行业行情（SW_DATE=YYYYMMDD 可选钉定目标日）
	$(PYTHON) $(SRC_MAIN) industry update $(if $(SW_DATE),--target-date $(SW_DATE),)

sw-rps-update-provisional: ## 增量拉取行业行情（允许 provisional）
	$(PYTHON) $(SRC_MAIN) industry update --allow-provisional $(if $(SW_DATE),--target-date $(SW_DATE),)

sw-rps-calculate: ## 计算行业 RPS（SW_DATE=YYYYMMDD 可选钉定目标日）
	$(PYTHON) $(SRC_MAIN) industry calculate $(if $(SW_DATE),--target-date $(SW_DATE),)

sw-rps-report: ## 生成行业报告（SW_DATE=YYYYMMDD 可选钉定目标日）
	$(PYTHON) $(SRC_MAIN) industry report $(if $(SW_DATE),--target-date $(SW_DATE),)

sw-rps-bootstrap: ## 初始化行业列表并拉取全部历史数据
	$(PYTHON) $(SRC_MAIN) industry bootstrap

sw-rps-validate: ## 校验行业数据质量
	$(PYTHON) $(SRC_MAIN) industry validate

sw-rps-drilldown: ## 强势区成分股贡献穿透分析
	$(PYTHON) $(SRC_MAIN) industry drilldown

sw-rps-confirm: ## [Layer ②] 主题确认（Theme Confirmation：行业证据，bucket/theme 分层）。SW_DATE=YYYYMMDD 可选钉定目标日
	$(PYTHON) $(SRC_MAIN) industry confirm $(if $(SW_DATE),--target-date $(SW_DATE),)

sw-rps-structure: ## [Layer ②] Enrichment 行业内部结构（offline 读缓存生成，soft-fail；--allow-online-fetch 做 Cache Refresh）
	$(PYTHON) $(SRC_MAIN) industry structure

# ── Layer ②/③ 个股趋势指标（Observation 层：Market Observation → Stock / Tier） ──

stock-metrics: ## 构建个股趋势指标产物（Observation 层，离线读缓存确定性；--allow-online-fetch 可手工补数）
	$(PYTHON) $(SRC_MAIN) select stock-metrics

stock-metrics-online: ## 个股行情在线补数（手工刷新 raw 缓存；run-day 离线不抓个股）
	$(PYTHON) $(SRC_MAIN) select stock-metrics --allow-online-fetch

select: ## Layer ③ 交易标的筛选（读 Layer①/② + 预计算个股趋势 → 候选对象 JSON + HTML；run-day 默认流程亦含，默认禁止联网）
	$(PYTHON) $(SRC_MAIN) select run

select-offline: ## Layer ③ 交易候选（强制离线，仅读缓存/产物）
	$(PYTHON) $(SRC_MAIN) select run --offline

# ── v0.5 Historical Replay ───────────────────────────────────────

DATE ?= 20260803
START ?=
END ?=
LAYERS ?= 123
SIGNALS ?=
THEME ?= ai_infrastructure
EXIT ?= signal_exit

replay-single: ## [v0.5] 单日期历史信号重放（纯离线；DATE=YYYYMMDD）
	$(PYTHON) $(SRC_MAIN) research replay single --date $(DATE)

replay-parity: ## [v0.5] 单日期重放 + 与正式产物 parity 校验（DATE=YYYYMMDD）
	$(PYTHON) $(SRC_MAIN) research replay parity --date $(DATE)

replay-range: ## [v0.5] 区间历史信号重放（START/END=YYYYMMDD, LAYERS=123|12）
	$(PYTHON) $(SRC_MAIN) research replay range --start $(START) --end $(END) --layers $(LAYERS)

event-study: ## [v0.5.1] 事件研究（SIGNALS=信号parquet, START/END可选, LAYERS=123）
	$(PYTHON) $(SRC_MAIN) research event-study --signals $(SIGNALS) --layers $(LAYERS)

backtest-trades: ## [v0.5.2] 交易层逐笔模拟（THEME=主题, EXIT=退出策略, SIGNALS=信号）
	$(PYTHON) $(SRC_MAIN) backtest trades --signals $(SIGNALS) --theme $(THEME) --entity-type etf --exit-policy $(EXIT)

backtest-sensitivity: ## [v0.5.2] 退出规则稳健性（固定/MA/分年/分ETF/成本扫描）
	$(PYTHON) $(SRC_MAIN) backtest sensitivity --signals $(SIGNALS) --theme $(THEME) --entity-type etf

backtest-matrix: ## [v0.5.2] 四组对比矩阵（configured vs theme-matched）
	$(PYTHON) $(SRC_MAIN) backtest matrix --signals $(SIGNALS)

backtest-portfolio: ## [v0.6] 共享账户组合模拟（单策略 + Core+Quality）
	$(PYTHON) $(SRC_MAIN) backtest portfolio --signals $(SIGNALS)

backtest-construction: ## [v0.6] 组合构建实验（Top-N/加权/持仓/比例/现金）
	$(PYTHON) $(SRC_MAIN) backtest construction --signals $(SIGNALS)

# ── Lane 2 Research（ETF 估值/底部研究） ────────────────────────

etf-bottom: ## [Lane2] Study 1 Price Bottom：729 FULL ETF 长期底部赔率（P756/DD30/MA20/MA60 恢复）
	$(PYTHON) $(SRC_MAIN) research etf-bottom

etf-bottom-price-map: ## [Lane2] Price Bottom Map：2026-08-28 横截面低位地图（60/120/360D 价格位置）
	$(PYTHON) $(SRC_MAIN) research etf-bottom --price-map --date 2026-08-28

etf-bottom-odds: ## [Lane2] Study 2 State Odds：进入底部状态后的 20/60/120D 前向收益
	$(PYTHON) $(SRC_MAIN) research etf-bottom --state-odds

etf-bottom-drilldown: ## [Lane2] Study 2A Drilldown：当前 29 只长期底部 ETF 逐只历史赔率
	$(PYTHON) $(SRC_MAIN) research etf-bottom --drilldown

etf-bottom-episodes: ## [Lane2] Study 2B Episodes：产业底部周期压缩（去同产业重复暴露）
	$(PYTHON) $(SRC_MAIN) research etf-bottom --episodes

etf-bottom-context: ## [Lane2] Study 2C Context Matching：当前底部 vs 历史 episode context 匹配
	$(PYTHON) $(SRC_MAIN) research etf-bottom --context-match

etf-bottom-context-replication: ## [Lane2] Study 2D Replication：2C 发现在大样本（13.8k entry）上的复现
	$(PYTHON) $(SRC_MAIN) research etf-bottom --context-replication

etf-bottom-repair: ## [Lane2] Study 2E Repair Structure：price_pos_120 结构验证（composition/interaction/date-weighting）
	$(PYTHON) $(SRC_MAIN) research etf-bottom --repair

etf-bottom-current-eval: ## [Lane2] 当前关注 ETF 的 Repair-Retest V1 阶段评估（读 2E 冻结 cut points）
	$(PYTHON) $(SRC_MAIN) research etf-bottom --current-eval

etf-bottom-scan: ## [Lane2] 全市场 Repair-Retest V1 每日扫描（三层：Market Map / Scanner / Odds）。SCAN_DATE=YYYY-MM-DD 可选（缺省自动取最新 raw 交易日）
	$(PYTHON) $(SRC_MAIN) research etf-bottom --scan $(if $(SCAN_DATE),--date $(SCAN_DATE),)

etf-bottom-v1-backtest: ## [Lane2] Repair-Retest V1 历史触发频率回测（Signal Incidence）。START=/END=YYYY-MM-DD 可选
	$(PYTHON) $(SRC_MAIN) research etf-bottom --backtest-v1 --start-date $(if $(START),$(START),2022-01-01) --end-date $(if $(END),$(END),2026-08-31)

etf-mapping-feasibility: ## [Lane2] Stage 1a ETF 跟踪指数 mapping 可行性抽样探测
	$(PYTHON) -m src.research.etf_mapping.cli --sample-n 10

trend-transition-3a: ## [Lane3] Study 3A Post-924 底部→趋势切换断点研究（只读缓存，不联网）
	$(PYTHON) $(SRC_MAIN) research trend-transition study3a

trend-transition-3b: ## [Lane3] Study 3B 预测 Trend Transition（walk-forward + PASS gate，只读缓存）
	$(PYTHON) $(SRC_MAIN) research trend-transition study3b

trend-transition-3c: ## [Lane3] Study 3C 状态分类研究（确定性 as-of 状态机 + C1-C5，PASS 冻结 V1）
	$(PYTHON) $(SRC_MAIN) research trend-transition study3c

trend-transition-state: ## [Lane3] Application：读冻结 YAML，输出 date-stamped 状态表（--date 缺省=最新 v1_signal_daily；L3_DATE=YYYYMMDD 可显式钉定）
	$(PYTHON) $(SRC_MAIN) research trend-transition state $(if $(L3_DATE),--date $(L3_DATE),)

# ── 开发维护 ──────────────────────────────────────────────────

test: ## 运行全部测试
	$(PYTHON) -m pytest tests/ -v

install: ## 安装依赖
	$(PYTHON) -m pip install -r requirements.txt

clean: ## 清理 __pycache__ 和 .pyc
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; \
	find . -type f -name '*.pyc' -delete
