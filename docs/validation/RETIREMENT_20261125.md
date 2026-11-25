# 业务收敛与瘦身治理 — 第二阶段执行记录

日期：2026-11-25。审批范围：ETF 子系统、SW 行业子系统、Basket legacy、一次性脚本及策略配置。执行原则：保留数据资产，退役策略/旧报告链；不修改正式行情口径和数据质量规则；历史批次与来源证据不批量删除。

## 1. 最终职责边界

- **AKsignal = Financial Data Assets & Research Platform**：Collection、Data Assets、Serving、Research。
- **GridTrading**：策略开发、参数优化、回测、交易信号、交易账本和策略自动化。
- AKsignal 不再维护 Selection、账户映射、候选卡、BUY/WATCH/WAIT、执行/组合配置或独立 ETF/SW 策略报告。
- Market Facts、Holdings Dashboard、CSI500 Price Channel、正式 Basket Compare V2、IC500 专题、A-Shape V2 专题保留；A-Shape V2 只做历史形态量化研究，不生成交易动作。

## 2. 代码删除与替换清单

### 2.1 新增（替代/抽离）

| 文件 | 作用 |
|---|---|
| `src/market_facts/etf_observation.py` | 从 `etf_signal/rotation.py` 抽离 ETF 横截面 RPS、异常质量标记、跨资产方向；计算体保持不变 |
| `src/market_facts/industry_quality.py` | 从旧 SW 模块抽离来源日期、确认状态、闭市日判断；供 TDX/历史缓存适配器使用 |
| `src/market_facts/industry_observation.py` | 从旧 SW 模块抽离趋势阶段分类与一级行业方向聚合；供 TDX 日报使用 |
| `src/observation/quality.py` | 从 `final_validation` 抽离 ETF/个股抓取质量警告读取；日报 build/check/report 直接消费 |
| `tests/market_facts/test_etf_observation.py` | ETF 横截面事实回归（RPS1/RPS5/ΔRPS15/流动性/异常/分类缺口/跨资产方向） |
| `tests/market_facts/test_industry_quality.py` | 来源日期、完整性、闭市日 cutoff 回归 |
| `tests/market_facts/test_industry_observation.py` | 行业方向聚合与趋势阶段回归 |
| `dashboards/` | 本地看板入口页与目录刷新工具（上一任务保留） |

### 2.2 删除的 Git 跟踪文件（67 个）

| 路径 | 类别 |
|---|---|
| `src/etf_signal/rotation.py` | 旧 Rotation/候选逻辑；计算体已抽离到 market_facts |
| `src/etf_signal/rotation_report.py` | 独立 ETF Rotation HTML |
| `src/etf_signal/signal.py` | BUY_CANDIDATE/STRONG_WATCH 趋势信号 |
| `src/etf_signal/account.py` | 国金账户映射与黑名单 |
| `src/etf_signal/card.py` | ETF 候选信息卡 |
| `src/etf_signal/universe.py` | ETF 筛选/准入 |
| `src/etf_signal/heat.py` | 独立 ETF 热度报告能力 |
| `src/final_validation/__init__.py`、`src/final_validation/cli.py` | 旧 Action/Selection 末端验收 CLI；质量读取已抽离 |
| `src/sw_industry_rps/__init__.py` | 旧 SW 子系统包入口 |
| `src/sw_industry_rps/cli.py` | 旧 SW 更新/计算/报告/确认 CLI |
| `src/sw_industry_rps/confirmation.py` | 旧主题确认 |
| `src/sw_industry_rps/confirmation_sections.py` | 旧确认展示 |
| `src/sw_industry_rps/constituents.py` | 旧 SW 成分/结构穿透 |
| `src/sw_industry_rps/contribution.py` | 旧行业贡献/结构 |
| `src/sw_industry_rps/data_source.py` | SW/SWSResearch/THS 采集 |
| `src/sw_industry_rps/drive_labels.py` | 旧驱动标签展示 |
| `src/sw_industry_rps/metrics.py` | 旧 SW RPS 计算；方向聚合已抽离 |
| `src/sw_industry_rps/regimes.py` | 旧 SW 状态机 |
| `src/sw_industry_rps/report.py` | 旧 SW HTML/CSV 报告；阶段分类已抽离 |
| `src/sw_industry_rps/source_status.py` | 旧来源状态；通用检查已抽离 |
| `src/sw_industry_rps/stock_emerging.py` | 旧个股观察 |
| `src/sw_industry_rps/storage.py` | 旧 SW raw/processed/报告存储 |
| `src/sw_industry_rps/structure.py` | 旧行业结构计算 |
| `src/sw_industry_rps/ths_mapping.py` | THS 临时映射 |
| `src/sw_industry_rps/tier_confirmation.py` | 旧 Tier 确认 |
| `src/sw_industry_rps/validator.py` | 旧 SW 质量检查 |
| `src/research/baskets/legacy/__init__.py` | Basket legacy 包 |
| `src/research/baskets/legacy/calculator.py` | 旧多源篮子计算 |
| `src/research/baskets/legacy/fetch.py` | 旧多源篮子抓取 |
| `src/research/baskets/legacy/report.py` | 旧 Plotly 报告 |
| `src/common/spec/hash.py` | Strategy config_hash/universe_hash |
| `src/common/spec/model.py`、`schema.py`、`loaders.py` | 仅保留 IndicatorSpec 与质量配置；策略/执行/组合模型与 loader 删除 |
| `scripts/exploration/a_shape.py` | A-Shape V1 检测器（被 V2 替代） |
| `scripts/exploration/probe_csindex_history.py` | 固定日期 CSIndex 探测 |
| `scripts/validation/preview_group_attention.py` | 一次性分组注意力预览 |
| `docs/archive/.../validate_tushare_sw_daily.py` | 依赖已删除 SW 包的归档验证脚本 |
| `config/strategy_spec.yaml` | Selection/策略参数 |
| `config/execution.yaml` | 执行/费用参数 |
| `config/portfolio.yaml` | 组合/仓位参数 |
| `config/industry_data.yaml` | SW 运行配置 |
| `config/guojin_tradable_blacklist.csv` | 账户映射黑名单 |
| `config/_legacy/etf_signal_rules.yaml`、`config/_legacy/etf_universe.yaml` | 旧 ETF 规则草案 |
| `tests/etf_signal/test_rotation_pulse.py` | 旧 Rotation 报告/候选测试；事实测试已迁移 |
| `tests/research/test_a_shape.py` | A-Shape V1 测试 |
| `tests/research/test_baskets.py` | Basket legacy 测试 |
| `tests/final_validation/__init__.py` | 已无测试 |
| `tests/sw_industry_rps/`（19 个文件） | 旧 SW calculate/confirm/report/storage/THS 测试 |
| `tests/trend_engine/test_industry_update.py` | 旧 SW active-universe/checkpoint 测试 |

> 完整路径与 Git 状态见 `git diff --name-status`。`src/sw_industry_rps` 目录和 `tests/sw_industry_rps` 目录已清空；`src/research/baskets/legacy` 目录已清空。没有创建新的 archive/legacy 代码目录。

### 2.3 保留但降低职责的代码

| 文件 | 处理 |
|---|---|
| `src/etf_signal/cli.py` | 仅保留 bootstrap/bootstrap-core/update/classify/calculate/retry-uncovered/preflight/backfill；移除 layer1/screen/watchlist/account/account-blacklist/card/pipeline/signal/backtest/run-day 及对应 imports |
| `src/etf_signal/data_source.py`、`master.py`、`fetch_policy.py`、`backfill.py`、`classification_facts.py`、`classifier.py`、`indicators.py` | 保留数据采集、身份/分类、回填和事实指标计算 |
| `src/daily_run.py` | 移除 industry update/calculate 步骤和 SW 降级分支；行业由 observation build 内 TDX 负责 |
| `src/observation/cli.py`、`report.py`、`industry_events.py` | 改用新的 `market_facts` helper；TDX-only 行业路径 |
| `src/market_facts/adapters.py` | 历史缓存适配器改用 `industry_quality`，不再依赖 SW 包 |
| `src/research/baskets/cli.py` | 只保留正式 compare 命令；移除 legacy imports、legacy-run/legacy-fetch |
| `src/main.py` | 更新路由说明；ETF CLI 仍由 `etf` 入口转发 |

## 3. 退役业务链路

| 链路 | 处理 |
|---|---|
| ETF Rotation HTML / 三问三答 | 退役；不再生成 HTML |
| ETF Watchlist → Account → Card → Pipeline | 退役；CLI 与 Makefile 入口删除 |
| ETF `signal` / `backtest` / 独立 `run-day` | 退役 |
| SW RPS update → calculate → confirm → structure → report | 退役；SW CLI、报告、确认、结构、THS provisional |
| Selection/Strategy/Execution/Portfolio 配置与模型 | 退役；只保留 IndicatorSpec 与质量参数 |
| Basket legacy-run/legacy-fetch | 退役；正式 Basket Compare V2 保留 |
| `final_validation` 旧 Action 验收 CLI | 退役；质量检查迁入 observation |
| Group Attention 一次性预览 | 退役；事实与回执保留 |
| CSIndex 一次性接口探测 | 退役；官方网页存档保留 |
| A-Shape V1 | 退役；V2 保留 |

## 4. HTML 与本地产物清理

### 已删除（明确分类）

- `data/audits/group_activity/20261023/attention_preview_v1/v2/v3/` 下 9 个 HTML 和 9 个 PNG（约 13.1MB，含 3 套预览图）；保留 `render_verification.json`、`browser_verification.json`、事实 JSON 与原始 observation facts。
- `data/audits/a_shape_v2/official-20261123/csi500_price_channel.html` 和 `plot_csi500_price_channel.py`（核验为重复 CSI 渲染；保留 `scan.py`、`plot.py`、CSV、PNG、receipt）。
- 本地 `.run_day` 历史批次中已退役类别的 HTML：ETF Rotation、SW RPS/confirmation、Selection candidates、Opportunity Radar、旧 backtest 等 **332 个文件，约 38.6MB**。
- 参考 manifest 的 4 个历史 HTML 保留：两份 `sw_industry_rps_20261012.html`、两份 `basket_compare.html`（legacy 目录和 20261012 run 各一份），避免破坏历史回执引用。

### 保留

- 正式日报：当前 `outputs/observation/observation_20261123.html` 及历史 `.run_day/runs/*/observation` 日报。
- 正式 Basket Compare V2：`research/baskets/comparable/*.html` 及 Hub pin 的报告。
- IC500：`exploration/ic500_trend_full_20261119.html` 等。
- Holdings Dashboard / CSI500 Price Channel。
- 官方来源网页：`data/audits/core_indices/*/official_page.html`、`cffex_contract_page.html`。
- Group Activity 原始 observation facts 与验收回执。
- A-Shape V2 扫描数据、CSV、PNG、receipt。

## 5. 数据资产保留清单

| 资产 | 位置 | 处理 |
|---|---|---|
| Longbridge 股票/指数/篮子 | `data/sources/longbridge/` | 完整保留 |
| CSIndex 历史与审计 | `data/sources/csindex/` | 完整保留 |
| CFFEX 原始合约行情 | `data/sources/cffex/` | 完整保留 |
| TDX 原始 `.day` 与 processed | `data/sources/tdx/` | 完整保留；正式行业源 |
| ETF EM/Sina raw/master/daily/audits | `data/sources/etf_em_sina/` | 完整保留 |
| SW 历史原始行情 | `data/legacy/raw/sw_industry/`（约 59MB，154 文件） | 永久保留 |
| SW 历史加工数据 | `data/legacy/processed/sw_industry/`（约 119MB，72 文件） | 保留 |
| SW 来源证据/Manifest/审计 | `data/legacy/`、`data/audits/`、历史批次 | 保留；不因代码退役删除 |
| 核心指数/CFFEX/SW 历史报告与回执 | `data/audits/`、`.run_day/runs` | 按分类保留 |

## 6. 跨项目数据供给审计

已新增 [跨项目数据供给契约](DATA_SERVING_CONTRACT.md)，记录了：

- GridTrading 已知读取：`dataset_paths_20261118.json` → ETF master/cache 及 published facts，属于直接路径依赖，不是稳定 API。
- GridTrading 仍有自己的 Eastmoney/Sina/Longbridge 取数与公司行为审计；不能因 AKsignal 有 ETF 资产就认定其可删除。
- 缺口：缺统一 schema、date/source-date 语义、price basis、calendar/coverage、dataset-level manifest/hash、稳定 reader。
- 方案：定义只读文件包 + manifest + hash；本轮不实现 API，不修改 GridTrading。

## 7. 量化对比

| 指标 | 治理前 | 治理后 | 变化 |
|---|---:|---:|---:|
| Git 跟踪文件 | 358 | 预计 306（含 15 个新增） | −52 |
| Git 跟踪 Python 文件 | 219 | 预计 168 | −51 |
| `src/` Python 行数 | 28,242 | 17,285 | −10,957 |
| `tests/` Python 行数 | 10,494 | 7,464 | −3,030 |
| `scripts/` Python 行数 | 2,880 | 2,600 | −280 |
| Python 总行数（src/tests/scripts） | 41,616 | 27,349 | −14,267 |
| `.run_day` HTML | 500 / 208.3MB | 169 / 171.3MB | −331 文件 / −37.0MB |
| `data/audits` HTML | 20 / 27.3MB | 10 / 17.4MB | −10 文件 / −9.8MB |
| `data/audits` 磁盘 | 166MB | 153MB | −13MB |
| `.run_day` 磁盘 | 约 1,135MB | 约 1,116MB | −19MB |
| `data/sources` 磁盘 | 1.3GB | 1.3GB | 0（资产未删） |
| ETF CLI 子命令 | 18 | 8 | −10 |
| SW CLI 子命令 | 9 | 0 | −9 |
| Basket CLI 子命令 | 3 | 1 | −2 |
| Makefile targets | 34 | 29 | −5 |
| 本地调度任务 | 1 个 LaunchAgent | 1 个 LaunchAgent | 0 |

说明：`.run_day` HTML 减少量小于删除字节数，因为本次正式重跑新增了一份 11/23 日报。`data/audits` 的 HTML 数包含 holdings 的 app/dist 中间页，未删除。

## 8. 测试与业务验收

### 单元/回归测试

```text
.venv/bin/python -m pytest tests -q
677 passed
```

覆盖：Market Facts、ETF 事实、TDX、Observation、日报、批次恢复、调度、Hub、Basket V2、Dashboard 目录。

### 正式日链

执行：

```bash
make run-day-offline RUN_DATE=20261123 RUN_FLAGS=--restart
```

结果：

- 5 步全部完成；正式发布 `status=PARTIAL`（与治理前同一日期一致）。
- 行业快照已登记；日报发布为 20261123。
- 原有警告保留：5 只 ETF 未更新、核心指数 7/9、分类待核实、历史行业快照缺失、位置变化未知。
- 发布路径：`.run_day/runs/20261123/e05365f87fff4899acf914d3af3fdb85/outputs`。

### 关键事实一致性

对比治理前批次 `9c4879b7...` 与治理后 `e05365f8...` 的同日 parquet（忽略 `generated_at`/`run_date`）：

| 事实 | 结果 |
|---|---|
| ETF 趋势 | 1,549 行，58 列一致 |
| 行业轮动 | 128 行，53 列一致 |
| 行业方向 | 30 行，10 列一致 |
| 核心指数 | 9 行，43 列一致 |
| 衍生品 | 4 行，41 列一致 |
| ETF 位置 | 1,549 行，35 列一致 |
| 个股位置 | 122 行，31 列一致 |
| 大类资产方向 | 26 行，10 列一致 |
| 日报 summary | modules/counts/missing/display contract 一致 |
| final_check | ok/status/errors/modules 一致 |

ETF 独立重算（raw + master）与治理前 `daily_indicators_20261123.parquet`、`rotation_20261123.parquet` 逐列一致；TDX 独立重算与治理前 `industry_rotation/directions` 稳定列一致。

日报 HTML 唯一差异：新批次多列出历史快照 `20261119` 缺失（登记表在治理前批次之后新增了该条目）。这不是指标变化，行业模块仍按既有 PARTIAL 规则降级。`config/observation_snapshots.json` 因正式重跑更新了 20261123 的生成时间/hash，事实内容一致。

## 9. Git diff

当前改动未提交，供复核：

- `git diff --stat`（release commit 相对上一版本节点 `7e1149d`）：140 个文件，1,723 行新增、16,559 行删除。
- 其中：67 个删除、58 个修改、15 个新增（含 `dashboards/`、`market_facts` 新模块、测试、契约、验收与决策记录）。
- 前次看板/CSI 报告的工作区改动仍保留，未混入本次退役判断。

## 10. 后续注意

1. 历史 `.run_day` 中保留的两份 SW 20261012 报告和两份 legacy Basket 报告仅因 manifest 引用保留；若未来清理 manifest，需先决定回执保留策略。
2. `data/legacy` 的 SW 数据、`data/sources` 所有资产均未删除；代码退役不等于数据退役。
3. 后续若新增跨项目消费，先按 `docs/DATA_SERVING_CONTRACT.md` 设计 manifest/reader，不直接暴露内部目录。
4. 本届治理没有修改正式行情口径、数据质量规则或报告统计规则。
