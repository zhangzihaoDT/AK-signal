PYTHON = .venv/bin/python
SRC_MAIN = src/main.py

.DEFAULT_GOAL = help

.PHONY: help run-day run-day-offline run-day-check run-day-status run-day-summary \
	daily-scheduler-install daily-scheduler-start daily-scheduler-stop daily-scheduler-restart daily-scheduler-status daily-scheduler-uninstall \
	etf-bootstrap etf-bootstrap-core etf-update etf-calculate etf-retry-uncovered \
	etf-preflight etf-backfill \
	stock-metrics stock-metrics-online basket basket-refresh test install clean

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

# RUN_DATE=YYYYMMDD pins the entire batch; otherwise resolved once at startup.
RUN_DATE ?=
RUN_FLAGS ?=
run-day: ## 每日观察链：数据 → 趋势与底部位置 → 验收与发布
	$(PYTHON) -m src.daily_run $(if $(RUN_DATE),--date $(RUN_DATE),) $(RUN_FLAGS)

run-day-offline: ## 同日离线批次：仅缓存，不联网；观察池股票未对齐时停止发布
	$(PYTHON) -m src.daily_run --offline $(if $(RUN_DATE),--date $(RUN_DATE),) $(RUN_FLAGS)

run-day-status: ## 查看当前业务阶段、失败原因与恢复进度
	$(PYTHON) -m src.daily_run --status $(if $(RUN_DATE),--date $(RUN_DATE),) $(RUN_FLAGS)

.PHONY: run-day-status

daily-scheduler-install: ## 安装并启动 AKsignal 独立 macOS 08:30 Scheduler LaunchAgent
	$(PYTHON) -m src.daily_scheduler install

daily-scheduler-start: ## 启动 AKsignal Scheduler LaunchAgent
	$(PYTHON) -m src.daily_scheduler start

daily-scheduler-stop: ## 暂停 AKsignal Scheduler（不终止已启动的 Daily Run）
	$(PYTHON) -m src.daily_scheduler stop

daily-scheduler-restart: ## 重启 AKsignal Scheduler（不重跑 Daily Run）
	$(PYTHON) -m src.daily_scheduler restart

daily-scheduler-status: ## 查看 AKsignal Scheduler LaunchAgent 状态
	$(PYTHON) -m src.daily_scheduler status

daily-scheduler-uninstall: ## 卸载 AKsignal Scheduler LaunchAgent
	$(PYTHON) -m src.daily_scheduler uninstall

run-day-check: ## [Final Validation] 校验 run-day 各层产物并输出最终结果
	$(PYTHON) $(SRC_MAIN) observation check $(if $(RUN_DATE),--date $(RUN_DATE),)

run-day-summary: ## [Summary] 当日观察摘要（不输出推荐）
	$(PYTHON) $(SRC_MAIN) run-summary run $(if $(SUMMARY_DATE),--date $(SUMMARY_DATE),)

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

# ── Layer ②/③ 个股趋势指标（Observation 层：Market Observation → Stock / Tier） ──

stock-metrics: ## 构建个股趋势指标产物（Observation 层，离线读缓存确定性；--allow-online-fetch 可手工补数）
	$(PYTHON) $(SRC_MAIN) observation stock-metrics $(if $(RUN_DATE),--date $(RUN_DATE),)

stock-metrics-online: ## 个股行情在线补数（手工刷新 raw 缓存；run-day 离线不抓个股）
	$(PYTHON) $(SRC_MAIN) observation stock-metrics $(if $(RUN_DATE),--date $(RUN_DATE),) --allow-online-fetch

START ?=
END ?=

# ── Research Basket（主题篮子对比，手动按需） ─────────────────────

BASKET_OUTPUT ?= .run_day/runs/$(END)/basket-comparison/outputs/research/baskets/comparable

basket: ## [Research] 长桥篮子离线重算（START/END/SNAPSHOT 必填）
	$(PYTHON) $(SRC_MAIN) research basket compare --start $(START) --end $(END) --snapshot $(SNAPSHOT) --output $(BASKET_OUTPUT)

basket-refresh: ## [Research] 长桥获取+篮子重算（START/END 必填）
	$(PYTHON) $(SRC_MAIN) research basket compare --start $(START) --end $(END) --allow-online-fetch --output $(BASKET_OUTPUT)

# ── 开发维护 ──────────────────────────────────────────────────

test: ## 运行全部测试
	$(PYTHON) -m pytest tests/ -v

install: ## 安装依赖
	$(PYTHON) -m pip install -r requirements.txt

clean: ## 清理 __pycache__ 和 .pyc
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; \
	find . -type f -name '*.pyc' -delete
