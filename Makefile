PYTHON = .venv/bin/python
SRC_MAIN = src/main.py

.DEFAULT_GOAL = help

.PHONY: help run-day \
	etf-bootstrap etf-bootstrap-core etf-update etf-calculate \
	etf-classify etf-layer1 etf-watchlist etf-account etf-account-blacklist etf-card etf-pipeline \
	etf-retry-uncovered \
	sw-rps-run-day sw-rps-update sw-rps-calculate sw-rps-report sw-rps-confirm \
	sw-rps-bootstrap sw-rps-validate sw-rps-drilldown \
	select select-offline test install clean

help: ## 显示帮助信息
	@grep -E '^[-a-zA-Z_0-9]+:.*## ' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*## "}; {printf "  make %-20s %s\n", $$1, $$2}'

# ── 专项重试 ─────────────────────────────────────────────────

etf-retry-uncovered: ## 专项重试未覆盖 ETF（分批+熔断器重置）
	$(PYTHON) $(SRC_MAIN) etf retry-uncovered

# ── 每日市场扫描（唯一入口） ──────────────────────────────────────

run-day: ## 每日 ETF 全市场信号 + SW-RPS 行业信号
	$(MAKE) etf-update
	$(MAKE) sw-rps-update
	$(MAKE) etf-calculate
	$(MAKE) sw-rps-calculate
	$(MAKE) etf-pipeline
	$(MAKE) sw-rps-report
	$(MAKE) sw-rps-confirm

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

sw-rps-confirm: ## [Layer ②] AI/科技/半导体 行业群确认
	$(PYTHON) $(SRC_MAIN) industry confirm

# ── Layer ③ 交易标的筛选（selection 内部调用 trend_engine） ──────

select: ## Layer ③ 交易标的筛选（读 Layer①/② + trend_engine → 候选对象 JSON + HTML）
	$(PYTHON) $(SRC_MAIN) select run

select-offline: ## Layer ③ 交易候选（仅用缓存行情，不联网）
	$(PYTHON) $(SRC_MAIN) select run --offline

# ── 开发维护 ──────────────────────────────────────────────────

test: ## 运行全部测试
	$(PYTHON) -m pytest tests/ -v

install: ## 安装依赖
	$(PYTHON) -m pip install -r requirements.txt

clean: ## 清理 __pycache__ 和 .pyc
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; \
	find . -type f -name '*.pyc' -delete
