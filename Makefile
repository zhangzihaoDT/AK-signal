PYTHON = .venv/bin/python
SRC_MAIN = src/main.py

.DEFAULT_GOAL = help

help: ## 显示帮助信息
	@grep -E '^[a-zA-Z_-]+:.*## ' Makefile | sort | \
		awk 'BEGIN {FS = ":.*## "}; {printf "  make %-20s %s\n", $$1, $$2}'

# ── 个股趋势监控 ──────────────────────────────────────────────

stock: ## 默认运行个股趋势
	$(PYTHON) $(SRC_MAIN) stock

stock-offline: ## 缓存模式（不联网）
	$(PYTHON) $(SRC_MAIN) stock --offline

# ── 申万行业 RPS 全流程 ──────────────────────────────────────

run-day: ## industry 全流程：更新→计算→报告
	$(PYTHON) $(SRC_MAIN) industry run-day

run-day-date: ## 指定日期全流程（用法: make run-day-date DATE=YYYYMMDD）
	$(PYTHON) $(SRC_MAIN) industry run-day --target-date $(DATE)

# ── 申万行业 RPS 分步 ─────────────────────────────────────────

update: ## 增量拉取行情（仅 active 124 个行业）
	$(PYTHON) $(SRC_MAIN) industry update

calculate: ## 计算 RPS 指标（幂等替换目标日期分区）
	$(PYTHON) $(SRC_MAIN) industry calculate

report: ## 生成 HTML/CSV 报告
	$(PYTHON) $(SRC_MAIN) industry report

validate: ## 校验原始数据与加工数据质量
	$(PYTHON) $(SRC_MAIN) industry validate

bootstrap: ## 初始化行业列表并拉取全部历史数据
	$(PYTHON) $(SRC_MAIN) industry bootstrap

drilldown: ## 强势区行业成分股贡献穿透分析
	$(PYTHON) $(SRC_MAIN) industry drilldown

# ── ETF 趋势信号 ─────────────────────────────────────────────────

etf-bootstrap: ## 初始化 ETF Master 并拉取全量历史数据
	$(PYTHON) $(SRC_MAIN) etf bootstrap

etf-update: ## 增量更新 ETF 日行情
	$(PYTHON) $(SRC_MAIN) etf update

etf-classify: ## 对 ETF 执行资产桶和暴露类型分类
	$(PYTHON) $(SRC_MAIN) etf classify

etf-layer1: ## [Layer 1] 全市场资产热度分布
	$(PYTHON) $(SRC_MAIN) etf layer1

etf-screen: ## [Layer 2] 国金门控管线：筛选可交易标的
	$(PYTHON) $(SRC_MAIN) etf screen

etf-calculate: ## 计算 ETF 技术指标
	$(PYTHON) $(SRC_MAIN) etf calculate

etf-signal: ## 生成 ETF 信号状态机
	$(PYTHON) $(SRC_MAIN) etf signal

etf-report: ## 生成 ETF 日报
	$(PYTHON) $(SRC_MAIN) etf report

etf-run-day: ## ETF 全流程：update → calculate → signal → report
	$(PYTHON) $(SRC_MAIN) etf run-day

# ── 开发维护 ──────────────────────────────────────────────────

test: ## 运行全部测试
	$(PYTHON) -m pytest tests/ -v

install: ## 安装依赖
	$(PYTHON) -m pip install -r requirements.txt

clean: ## 清理 __pycache__ 和 .pyc
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; \
	find . -type f -name '*.pyc' -delete
