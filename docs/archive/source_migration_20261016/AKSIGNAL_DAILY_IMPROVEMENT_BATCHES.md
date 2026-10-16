> 历史归档：已由正式来源架构替代，仅供追溯，不作为当前运行规范。

# AKsignal Daily 业务改进批次

> 记录 2026-10-16 确认的两个改进批次。申万发布时间调查使用本地运行日志；第二批按用户授权对少量公开接口做只读样本查询。未写缓存、未重算历史价格、未发布日报。完整供应商基线见 [数据需求与供应商覆盖审计](AKSIGNAL_DATA_SOURCE_AUDIT.md)。

## 执行结论

| 批次 | 状态 | 已落地 |
|---|---|---|
| 第一批：Daily 稳定性 | **实现并通过针对性测试** | 申万源更新时间证据整理；源端交易日核验；行业缺失时模块级 `PARTIAL` 发布；不混入旧日/不完整行业横截面；部分日报可人工重跑升级 |
| 第二批：市场事实质量 | **追溯字段与未知状态已落地；基础价格映射仍待权威数据** | 固定池股票行情记录 provider route、调整请求和未认证价格口径；ETF 报告落跟踪指数与分类缺失状态；港股旧错码已在配置中改为正确代码 |

第二批没有篡改 OHLCV 或凭产品名称猜测 ETF 跟踪指数。没有可审计的供应商基准/行业代码映射时，“补全映射”或“统一复权”不能靠本地代码安全推导；需取得授权且带明确身份/调整基准的数据后再完成数值修复。

## 第一批｜Daily 稳定性

### 申万数据到达时点：本地运行证据

| 目标 trade_date | 探测/更新记录（Asia/Shanghai 日志时间） | 结论 |
|---|---|---|
| 2026-10-13 | 10/14 11:26:28 探测到 `index_analysis_daily_sw`；11:26:33 完成 124/124 | 当日主源在上午晚些时候可用 |
| 2026-10-14 | 10/15 09:20:39 探测确认；09:20:45 完成 124/124 | 该次较早可用 |
| 2026-10-15 | 10/16 08:31:21 与 08:49:07 探测均为空；10:01:16 再次为空。10:01 的实时接口缺 `prev_close_trade_date`，不能作为目标日证据；10:06:45 历史源仍为 0/124 verified | 至少该日期到 10:06 仍未形成可核验全量价格；旧版 08:33 log 曾把 realtime 行标成 provisional、08:49 汇总曾显示 124/124，但这只是本地 assigned trade_date 的行数，不是当前源日期核验口径 |

证据来自 `.run_day/runs/20261013/`、`20261014/`、`20261015/` 中的 industry update 日志。当前样本只证明发布时间在样本间有差异，**不构成稳定的 T+1 发布时间承诺**；08:30 调度时不能假设申万日线已发布，10:00 也不是可保证的统一 SLA。已给定的 AKShare、Tushare 和通达信验证结论沿用其既有报告，本批未复测。

### 日期验收合同

已完成的本地代码门控（`src/sw_industry_rps/source_status.py`、`src/sw_industry_rps/cli.py`、`src/daily_run.py`、`src/observation/cli.py`）：

1. 主源日线的 `trade_date` 是其价格记录本身的日期证据；实时最新价/昨收盘必须各自携带字段级 `close_trade_date` / `prev_close_trade_date`，且匹配 target。
2. 缺少 source date 的实时返回、历史缓存分区标签、checkpoint 的 completed 标志，都不能单独通过验收。
3. 原始行业、计算指标、Observation build 和最终回执均核验；有效行业目标日分母为当前 124 个 active code，重复、错日、缺失都不能进入正式 124 行横截面。
4. 完整的 `COMPLETE` fallback 回执必须带 `industry_price_dates_verified=true`；旧 realtime 缓存不会被补成日期已验证。

### 模块级降级交付：用户确认采用“发布部分日报”

- ETF、固定池股票/沪深300等模块各自满足目标日门控时可独立交付。
- 行业 124 只只要没有全部可验证目标日，runner 将 industry module 记录为 `unavailable`，跳过 `industry calculate`；Observation 生成空的目标日行业分区及“本批不可用”面板，不显示旧交易日数据、不基于 123/124 等子集重算 RPS。
- `observation_{date}.json` / `final_check_{date}.json` 保存 module status、verified/expected 行数；日报仍发布，整体 `status=PARTIAL`、`ok=true`（其他必需模块通过时）、`data_complete=false`，warnings 说明行业缺口。`PARTIAL` 是一次正式的部分观察交付，不等于行业数据已验证。
- 缺少 ETF 或固定池股票等非行业核心输入仍会阻断，不被行业降级逻辑放行。
- Scheduler 接受有效的 `PARTIAL` 回执且不自动重试。人工再次运行该 trade_date 可重新抓取；原部分发布继续可读，只有替代批次通过验收后才原子切换。显式 `--require-complete` 仍要求完整等级。

对应实现：`src/daily_run.py`、`src/daily_schedule.py`、`src/observation/cli.py`、`src/observation/report.py`。行业 fresh-date 单测和 Daily/报告部分发布测试包含在测试结果中。

## 第二批｜市场事实质量

### 个股复权与来源

`outputs/stock_metrics_{date}.parquet` 增加 `source_route`、`adjustment_requested`、`price_basis`、`price_basis_verified`：

- CN 在线 EM/Sina/Tencent route 记录请求 `qfq`，但因为历史增量直接拼接且没有因子/anchor 重标定证据，标记 `qfq_requested_unverified`、`price_basis_verified=false`。
- 离线/缓存沿用标记 `cached_or_unknown_adjustment`；HK `stock_hk_daily` 未传调整参数，标记 `provider_default_adjustment_unknown`；不把 cache 命中伪装成已验证的 qfq。
- acquisition audit 的逐标的记录增加 `source_route`、`adjustment_requested` 和未认证状态；生产收盘价不被更改。
- 2026-10-12 本地对照审计仍是当前量化证据：58 只中 55 `MISMATCH_OR_INCOMPLETE`、1 alternate-QFQ、1 unadjusted、1 current-QFQ match。它不能作为逐只价格修复输入；真实修复仍需保留 provider basis、调整因子/基准日期及一致历史重建。

### 身份

- 当前配置已将禾赛从错误 HK 02510 改为 HK 02525；`config/observation_identity_issues.json` 保留 02510 为已解决历史错配。未来观察身份仍按 `(market, symbol)` 区分，不复用旧代码行情。
- 2026-10-14 旧正式批次含 02510 历史，已在当时日报排除正文；不能将该历史数据回标为 02525。
- 正确 02525 的历史覆盖、源 route 与复权目前仍未核验；修复代码身份不等于获得正确历史价格。

### ETF 跟踪指数与资产分类

- `rotation_{date}` 事实增加 `tracking_index_status`：无名称/代码映射就写 `missing`，有映射但未做来源核验写 `present_unverified`；不会根据基金名称猜具体指数。
- 同一事实增加 `classification_status`：已有桶仍标 `classified_unverified`，空桶显式标 `unclassified`。名称分类是观察标签，不证明指数身份。
- Observation 数据质量区展示观察样本的未分类数与缺跟踪指数数。当前基线 Master 有 1,554 个 ETF，跟踪指数名称/代码均为空；其中 650 只没有资产桶；10/14 Observation 样本中 563/1,331 未分类。新字段会在后续正常 Daily 计算时生成，本批未运行 pipeline 写入任何正式产物。
- 当前安装的 `.venv` AKShare 不含 `fund_etf_info_sina` 属性；仓库的 `fetch_etf_info()` 捕获缺少该属性后返回空值。当前可见的 `fund_etf_fund_info_em` 是基金净值历史接口，不是跟踪指数映射。没有对 1,554 只 ETF 做跟踪指数抓取或填充。

### 只读供应商样本核验（2026-10-16）

经用户授权，对既有复权审计中有代表性的 3 只 CN 股票各请求 EM/Sina qfq 日线，并对更正后的 HK 02525 请求 `stock_hk_daily`。只保留在本报告中的汇总，不写入原始缓存或正式指标。

| 代码 | EM qfq | Sina qfq | 共同行情比对 | 结论 |
|---|---|---|---|---|
| CN 000429 粤高速A | 2,129 行；最新 2026-10-15 close 13.58 | 2,129 行；同日 close 13.58 | 2,129 个共同日期；2,067 日 close 差异 >0.01；最新日差 0 | 当前 close 一致不能证明历史复权一致 |
| CN 600941 中国移动 | 1,155 行；最新 2026-10-15 close 101.50 | 1,155 行；同日 close 101.50 | 1,155 个共同日期；1,124 日差异 >0.01；最新日差 0 | 历史序列差异仍显著 |
| CN 000887 中鼎股份 | 2,129 行；最新 close 16.11 | 2,129 行；同日 close 16.11 | 2,129 个共同日期；1,847 日差异 >0.01；最新日差 0 | 与 10/12 本地缓存审计中 Sina qfq 匹配不矛盾：当前 EM/Sina 两套 qfq 历史本身不一致 |
| HK 02525 禾赛 | — | — | `stock_hk_daily` 返回 256 行，最新日期 2026-10-15 close 13.04；调用未指定 adjust | 只证明此时接口返回了目标日期行；provider 身份及复权仍 UNKNOWN |

供应商函数并未提供独立公司身份凭证。A 股两端同日 close 一致，不代表两条完整历史可拼接；HK 返回行也不构成身份/复权验证。小样本结果不能外推全池覆盖或长期稳定性。

### 仍需外部事实才能关闭的工作

| 项目 | 当前结论 | 关闭条件 |
|---|---|---|
| 58 只股票历史复权重建 | 本批已把不确定性随产物透传；未认证行不等于误价结论 | 获得同一证券、同一目标复权基准的历史价格/因子；日期、OHLC、公司行动复核后重建并比较对趋势/位置的影响 |
| HK 02525 历史 | 配置身份已改正，价格映射仍 UNKNOWN | 获取带日期的正确代码历史并验证 provider、复权和上市日期；不得从 02510 复制 |
| ETF 跟踪指数 | 当前 1,554 个 Master mapping 缺失 | 可审计产品主数据/发行人字段覆盖率、指数代码体系/币种/版本与更新时间，并逐笔校验后进入缓存 |
| ETF 资产桶 | 当前 563 个日报观察 ETF 未分类 | 先定权威标签源和优先级；明确分类不确定/多重暴露处理，避免进一步依赖宽泛子串推断 |

本批没有供应商接入、缓存回填、历史价格重算或日报发布。只读样本查询结果未写入项目数据目录。

## 验证

```text
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  tests/daily_run tests/observation tests/trend_engine/test_inputs.py \
  tests/etf_signal/test_rotation_pulse.py tests/sw_industry_rps -q
345 passed
```
