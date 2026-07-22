PYTHON = .venv/bin/python
SRC_MAIN = src/main.py

.DEFAULT_GOAL = help

help: ## 显示帮助信息
	@grep -E '^[a-zA-Z_-]+:.*## ' Makefile | sort | \
		awk 'BEGIN {FS = ":.*## "}; {printf "  make %-20s %s\n", $$1, $$2}'

# ── 每日市场扫描（唯一入口） ──────────────────────────────────────

run-day: ## 每日 ETF 全市场信号 + SW-RPS 行业信号
	$(MAKE) etf-update
	$(MAKE) sw-rps-update
	$(MAKE) etf-calculate
	$(MAKE) etf-pipeline
	$(MAKE) sw-rps-calculate
	$(MAKE) sw-rps-report

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

etf-card: ## 生成 ETF 候选信息卡片
	$(PYTHON) $(SRC_MAIN) etf card

etf-pipeline: ## 完整发现链路：watchlist → account → card
	$(PYTHON) $(SRC_MAIN) etf pipeline

# ── SW-RPS 行业信号模块（ETF 子模块） ──────────────────────────

sw-rps-run-day: ## SW-RPS 全流程：更新→计算→报告
	$(PYTHON) $(SRC_MAIN) industry run-day

sw-rps-update: ## 增量拉取行业行情
	$(PYTHON) $(SRC_MAIN) industry update

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

# ── 个股趋势监控（独立观察项目，非每日任务） ──────────────────

stock: ## 运行个股趋势分析
	$(PYTHON) $(SRC_MAIN) stock

stock-offline: ## 缓存模式（不联网）
	$(PYTHON) $(SRC_MAIN) stock --offline

# ── 开发维护 ──────────────────────────────────────────────────

test: ## 运行全部测试
	$(PYTHON) -m pytest tests/ -v

install: ## 安装依赖
	$(PYTHON) -m pip install -r requirements.txt

clean: ## 清理 __pycache__ 和 .pyc
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; \
	find . -type f -name '*.pyc' -delete
