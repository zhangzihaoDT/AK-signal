# Market Facts Contract v1 与迁移顺序

> 2026-10-16 后续更新：用户已指定模块独立来源，长桥个股生产入口已接入；ETF保留EM/Sina，行业仅TDX且价格待接通。当前状态以 [OBSERVATION_DATA_SOURCES.md](OBSERVATION_DATA_SOURCES.md) 为准；下文试运行结论保留为历史验收记录，复权独立认证仍未完成。

2026-10-16。目标方案分三步：

1. 统一事实契约，长桥固定池独立试运行。
2. 打通通达信行业价格，独立验证 TDX RPS。
3. 验收后正式切换基准。

## 实现边界

新增 `src/market_facts/contract.py` 定义供应商无关的日线快照；长桥试运行入口
`python -m src.market_facts.longbridge_pilot --date 2026-10-15 --env-file .env`。
需要可导入的 `longbridge` SDK（本次使用既有临时 5.2.1，不修改生产依赖）。
只调用 QuoteContext 历史日线，不调用交易或账户接口。

默认输出 `data/pilots/longbridge/{YYYYMMDD}/{unique_run_id}/`，每证券独立 JSON，
manifest 保留固定池配置哈希、所有 theme/tier/participation 注册及每证券结果。
按市场/代码去重请求，跨主题注册不丢失。每次新目录，不追加旧供应商历史，
不覆盖生产缓存，不进入 Daily 发布，也不代表长桥已升为正式主源。

## 字段与约束

| 层次 | 字段/语义 |
|---|---|
| 证券身份 | security_id、market、exchange、currency、asset_type、provider_symbol；配置映射不冒充独立身份验证 |
| 获取链 | provider、adapter、endpoint；AKShare 是 adapter，EM/Sina 是 provider |
| 价格依据 | price_basis、adjustment_version、price_basis_verified；ForwardAdjust 独立标识，版本未知明确 unknown |
| 分类 | classification_version；股票未附分类时 not_applicable，行业必须指定分类版本 |
| 时间 | target_trade_date 是批次目标；每 bar 的 source_trade_date 来自源时间戳按市场时区换算；fetched_at 是获取时间 |
| 快照 | contract_version、内容哈希 snapshot_id；历史重写生成新快照，抓取时间不参与内容哈希 |
| 日线 | open/high/low/close/volume/turnover，保留 source_timestamp |

拒绝重复日期、未来日线、非有限值、非正价格、负成交量/额及 OHLC 不合法关系。
缺目标日允许保存研究证据，但命令返回失败。源错误只保存异常类型，避免写入凭证或响应秘密。
每证券查询800个日历日，低于单次1000根日线限制；串行请求间隔0.6秒，不自动重试。

目标日存在、历史行数足够、交易日历连续、收盘完整、身份已核验、价格依据已核验是不同维度。
本阶段只检查目标日与60/120/360行数，不把行数视为有效窗口证明。
日历完整性及收盘完整性为 UNKNOWN；identity_verified 与 price_basis_verified 为 false；
production_eligible 固定 false。上市不足保留不足，不补造历史。显式目标日期必须早于运行日，
但这不能代替交易所日历检查；休市日目标会表现为缺目标日。

## 尚未完成的 step1 验收

- 用独立交易所日历核验日期连续性、上市/停牌与收盘完成状态。
- 核验证券主数据身份；审计调整序列与版本，不根据价格相等自动升级验证状态。
- 用同一事实契约包装 EM/Sina/ETF/行业现有链路，保留旧源未知证据；禁止补写虚假已验证状态。
- 将独立快照交给现有趋势/位置纯计算，并验证同输入结果一致；当前试运行只验证取数与快照。
- 连续多个交易日记录更新可用时点、失败与运行时间，再决定固定池生产接入。

## 后续门槛

TDX 在拿到稳定带日期行业价格之前只作候选分类目录。467节点的345终端节点中，
309为7字符深度、36为5字符深度，RPS denominator 仍需明确设计与版本化。
不能以申万同名行业价格填充 TDX，不拼接 SW/TDX 排名历史。
正式切换前保留 SW 当前基准身份，分别保存两套横截面与方法版本。

Longbridge 请求定义参考：https://open.longbridge.com/docs/quote/pull/history-candlestick 。

## SDK 时间戳适配

Longbridge Python SDK 的 `PyOffsetDateTimeWrapper` 使用
`datetime.fromtimestamp(epoch, None)`，返回主机本地 naive datetime。
适配器先按主机本地时间恢复时间点，再转换到市场时区，并保存带偏移时间戳和转换路径。
禁止将该 naive 值直接声明为 UTC 或市场本地时间。
依据：[SDK time.rs](https://github.com/longbridge/openapi/blob/main/python/src/time.rs)。
测试同时覆盖 aware UTC 跨日转换与 SDK naive 本地时间的反向恢复。

可选依赖清单：`requirements-market-data-pilot.txt`。试运行目录独立于 outputs 的
原子发布目录；尚未接入 Daily Run 调度。失败批次保留 manifest，不更新任何 latest 别名。

## Step1 第二批实现（2026-10-16）

已实现独立的离线消费和验收链，尚未接入生产 Daily：

- `adapters.py`：显式 EM/Sina 源响应适配（provider 与 AKShare adapter 分离），及股票/ETF/行业旧缓存包装；旧缓存来源不明则 provider=unknown。保存源文件哈希、包装时间、原始来源标签，原抓取时间未知不补造。
- `calendar.py`：独立的有界 CN/HK 日历快照，保存版本、摘要、逐日收盘时刻。当前基于 `exchange-calendars==4.13.2` 的规则模型，不称交易所认证。CN 与现有上交所公告日历在重叠区间交叉核对，差异则停止验收；SZSE 暂使用 XSHG A股日期代理，不能当成独立深交所核验。
- `consume.py`：校验输入哈希后，复用现有指标/趋势评分及 stock_position_v1。缺日历、非交易日、未收盘、关键窗口缺口或 OHLCV 不足时明确不可用。缺失或滞后的基准不静默生成当日相对强度；未来基准行在计算前截断。
- `audit_longbridge.py`：只读补充证券静态资料和 NoAdjust 对照，独立落盘。代码/交易所/币种一致和复权开关有效，不等于发行人身份、公司行为算法已经独立验证。
- `validate_pilot.py`：独立日历检查、同输入现有引擎 parity、旧缓存价格/趋势/位置差异，保存输入与代码摘要。
- `audit_archived_sources.py`：离线验证既有 EM/Sina 源标记参考；参考分区日期单独记录，不冒充目标日新行情。

契约允许明确的 close_only 投影：open/high/low 为 null，记录排除列及源文件哈希；不能用来生成 OHLCV 趋势。正价/OHLC 约束拒绝只说明不满足本分析契约，不直接断言供应商原始数据错误。非正 close 仍拒绝，不填充或修正。

新增 volume_unit/turnover_unit，未知单位明确 unknown；不把不同供应商成交量单位默认为可比较。
source_dates_verified（全历史）与 target_source_date_verified（目标日）分开；旧 SW 主源日期证据沿用现有 verified_price_dates，未注明日期的实时兜底不晋升为已验证。
新建证券键统一香港五位代码，已有快照不改写。

复现入口（可选日历依赖安装在独立环境）：

```bash
python -m src.market_facts.calendar --output data/pilots/calendars/20261016 --start 2024-01-01 --end 2026-12-31
python -m src.market_facts.validate_pilot --snapshots data/pilots/longbridge/20261015/3ced4ae47f0d4683a169f8f815df2d9d --calendars data/pilots/calendars/20261016 --references data/pilots/longbridge_audits/9b58ac4b1ba046a5932818d852f14891
python -m src.market_facts.audit_archived_sources --snapshots data/pilots/longbridge/20261015/3ced4ae47f0d4683a169f8f815df2d9d --reference-dir data/audits/stock_price_basis/20261012/references
```

旧“尚未完成”清单中，日历检查、来源适配和纯计算消费已具备试运行实现；仍需独立发行人身份/复权依据核验、异常缺口归因，以及真实跨日运行稳定性证据。当前各结果 production_eligible 均为 false，不触发正式基准切换。
