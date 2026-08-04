PYTHON = .venv/bin/python
SRC_MAIN = src/main.py

.DEFAULT_GOAL = help

.PHONY: help run-day run-day-check \
	etf-bootstrap etf-bootstrap-core etf-update etf-calculate \
	etf-classify etf-layer1 etf-watchlist etf-account etf-account-blacklist etf-card etf-pipeline \
	etf-retry-uncovered \
	sw-rps-run-day sw-rps-update sw-rps-calculate sw-rps-report sw-rps-confirm \
	sw-rps-bootstrap sw-rps-validate sw-rps-drilldown \
	select select-inputs select-offline \
	replay-single replay-parity replay-range event-study \
	backtest-trades backtest-sensitivity backtest-matrix backtest-portfolio backtest-construction \
	test install clean

help: ## 显示帮助信息
	@grep -E '^[-a-zA-Z_0-9]+:.*## ' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*## "}; {printf "  make %-20s %s\n", $$1, $$2}'

# ── 专项重试 ─────────────────────────────────────────────────

etf-retry-uncovered: ## 专项重试未覆盖 ETF（分批+熔断器重置）
	$(PYTHON) $(SRC_MAIN) etf retry-uncovered

# ── 每日市场扫描（唯一入口） ──────────────────────────────────────

run-day: ## 每日全流程：ETF 信号 → SW-RPS 信号 → 个股趋势输入 → Layer③ 候选 → Final Validation
	$(MAKE) etf-update
	$(MAKE) sw-rps-update
	$(MAKE) etf-calculate
	$(MAKE) sw-rps-calculate
	$(MAKE) etf-pipeline
	$(MAKE) sw-rps-report
	$(MAKE) sw-rps-confirm
	$(MAKE) select-inputs
	$(MAKE) select
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

# ── SW-RPS 行业信号模块（ETF 子模块） ──────────────────────────
# 结构：confirm 是 calculate 的下游，复用 calculate 产出的 RPS/指标，
#       不维护第二套指标计算逻辑。

sw-rps-run-day: ## SW-RPS 全流程：update→calculate→report→confirm
	$(MAKE) sw-rps-update      # 1. 获取行情（内置 freshness probe）
	$(MAKE) sw-rps-calculate   # 2. 计算全量申万二级 RPS
	$(MAKE) sw-rps-report      # 3. 原有全市场行业报告
	$(MAKE) sw-rps-confirm     # 4. Layer ② 行业群确认（下游复用指标）

sw-rps-run-day-provisional: ## SW-RPS 全流程（允许 provisional 数据）
	$(PYTHON) $(SRC_MAIN) industry run-day --allow-provisional

sw-rps-update: ## 增量拉取行业行情
	$(PYTHON) $(SRC_MAIN) industry update

sw-rps-update-provisional: ## 增量拉取行业行情（允许 provisional）
	$(PYTHON) $(SRC_MAIN) industry update --allow-provisional

sw-rps-calculate: ## 计算行业 RPS
	$(PYTHON) $(SRC_MAIN) industry calculate

sw-rps-report: ## 生成行业报告
	$(PYTHON) $(SRC_MAIN) industry report

sw-rps-bootstrap: ## 初始化行业列表并拉取全部历史数据
	$(PYTHON) $(SRC_MAIN) industry bootstrap

sw-rps-validate: ## 校验行业数据质量
	$(PYTHON) $(SRC_MAIN) industry validate

sw-rps-drilldown: ## 强势区成分股贡献穿透分析
	$(PYTHON) $(SRC_MAIN) industry drilldown

sw-rps-confirm: ## [Layer ②] 主题确认（Theme Confirmation：行业证据，bucket/theme 分层）
	$(PYTHON) $(SRC_MAIN) industry confirm

# ── Layer ③ 交易标的筛选（selection 内部调用 trend_engine） ──────

select-inputs: ## 构建个股趋势输入产物（离线读缓存，确定性；--allow-online-fetch 可手工补数）
	$(PYTHON) $(SRC_MAIN) select inputs

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

# ── 开发维护 ──────────────────────────────────────────────────

test: ## 运行全部测试
	$(PYTHON) -m pytest tests/ -v

install: ## 安装依赖
	$(PYTHON) -m pip install -r requirements.txt

clean: ## 清理 __pycache__ 和 .pyc
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; \
	find . -type f -name '*.pyc' -delete
