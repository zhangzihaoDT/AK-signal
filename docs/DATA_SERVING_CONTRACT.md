# 跨项目数据供给契约（现状与设计）

更新：2026-11-25。本文只记录现状、缺口和文件级契约方案，不新增 API、不修改 GridTrading、不部署基础设施。

## 1. 当前消费者

| 消费者 | 读取内容 | 读取方式 | 风险 |
|---|---|---|---|
| GridTrading 策略研究脚本 | `data/reference/dataset_paths_*.json`、ETF master parquet、ETF raw parquet、已发布 observation facts | 直接文件路径 | 依赖 AKsignal 可变目录与本地路径；索引无 schema 版本和内容 hash |
| GridTrading 自带取数 | Eastmoney/Sina/Longbridge | 独立采集与审计 | 与 AKsignal ETF 采集重叠，但服务其策略输入/公司行为门控，不能简单视为可删除重复 |
| AKsignal Holdings Dashboard | GridTrading `private/positions/current_holdings.json` | 直接读取 | 反向依赖；属于个人持仓输入而非策略读取 |
| AKsignal Hub | 正式日报、Basket Compare 指针与 hash | 只读内部回执 | 不是对外数据服务 |

当前没有稳定的跨仓库 reader/API。已有 `data/reference/dataset_paths_20261118.json` 只是某日的一次性路径清单：

- 有 `trade_date`、`path_base`、`paths`、`published_paths`；
- 没有 schema 版本、资产清单 hash、日历版本或逐文件 SHA-256；
- 没有生成器/校验器；
- `paths` 指向历史缓存，目录内容可能随日更变化。

## 2. 数据资产现状

| 数据族 | 当前格式 | 身份/口径 | 质量状态 | 版本锁定 |
|---|---|---|---|---|
| 股票日线 | Longbridge raw JSON + `market-facts-v1` 单序列快照 | `SeriesIdentity`（市场、交易所、provider、endpoint、price basis、adjustment version） | quality 字段：目标日、源日期、日历完整性、历史窗口；`price_basis_verified=false` | snapshot_id + partition index |
| ETF 日线 | raw parquet（逐基金） + master + daily indicators/rotation | 逐行来源/price basis 可能为 `unknown_legacy`；不能跨口径拼接 | 覆盖/异常标记；部分旧序列来源未知 | raw 文件、processed 分区；无统一 dataset manifest |
| 核心指数 | 核心指数快照 JSON + 发布 facts | 逐指数固定 provider/price basis | 固定行展示，缺失 UNAVAILABLE，模块 PARTIAL | snapshot/registry hash |
| 股指期货 | CFFEX 原始 CSV + facts | 具体到期合约；不拼接连续合约 | 目标日/来源文件日期独立记录 | 原始文件 + partition index |
| 行业指数 | TDX 原始 `.day` + processed prices/metrics | 固定 128 行业、分类版本 20261016、未复权指数 | 全横截面或 UNAVAILABLE；窗口样本数 | 原始字节、分类版本、快照登记 |

## 3. 契约缺口

1. **没有面向下游的统一数据集 schema**：各数据族字段、单位、复权口径、日期含义不同，只能靠阅读代码或旧索引。
2. **日期与质量语义未统一暴露**：`trade_date`、`source_trade_date`、`fetched_at`、`run_date` 需明确区分；`PARTIAL` 是发布状态，不等于数据可用。
3. **没有数据集级内容摘要/清单**：现有 snapshot hash 主要覆盖单序列或单个 facts 文件，不是跨文件 dataset manifest。
4. **缓存与发布混用**：GridTrading 同时读取 master/cache 和 published facts，无法保证同一批次、同一价格口径。
5. **公司行为/复权依据不足**：`price_basis_verified=false` 必须在供给契约中保留，不能因下游需要而追认。
6. **交易日历与覆盖范围未随数据交付**：消费方需自行推断缺失交易日，容易把停牌、新上市或源缺口混为一谈。
7. **没有稳定读取入口**：只有内部 Python readers 和文件路径约定，缺少只读、版本化的外部读取方式。

## 4. 建议的文件级契约（后续实现，不在本轮）

一个数据集包至少包含：

```text
dataset_<name>_<trade_date>_<version>/
  manifest.json
  data.parquet            # 或分片；列与单位在 manifest 中声明
  calendar.parquet        # 有界交易日历版本与交易日
  source_evidence/        # 原始来源摘要/回执，不复制无关凭证
```

`manifest.json` 建议字段：

| 字段 | 含义 |
|---|---|
| `contract_version` / `dataset_id` / `trade_date` | 契约版本、数据集身份、目标交易日 |
| `generated_at` / `source_cutoff` | 构建时间与源数据截止 |
| `schema` | 列名、类型、单位、可空性、主键 |
| `assets` | 资产代码、市场、交易所、资产类型、名称来源 |
| `price_basis` / `adjustment_version` / `price_basis_verified` | 价格依据，未知必须显式 |
| `calendar` | 日历版本、覆盖范围、目标日是否完整收盘 |
| `quality` | `CONFIRMED/COMPLETE/PARTIAL/UNAVAILABLE` 及各模块覆盖/缺失 |
| `source` | provider、endpoint、原始文件/响应摘要 |
| `files` | 每个交付文件的 SHA-256、行数、字节数 |
| `known_limitations` | 缺失交易日、历史不足、未认证公司行为等 |

读取规则：

- 消费方只读 manifest 列出的文件，不扫描 AKsignal 可变目录；
- 目标日不完整时拒绝消费，或用显式 `PARTIAL` 标记并说明缺失；
- 同一包内不得混用不同复权口径或不同来源的序列；
- 包的 hash 进入下游 run manifest，保证回测可复现；
- 不把 AKsignal 的 `outputs` 当前指针当作历史版本。

## 5. 本轮结论

AKsignal 已具备数据采集、身份、日期门控、质量证据和历史快照基础；尚缺面向 GridTrading 的稳定、版本化文件契约。下一步应先在现有文件上定义 manifest/reader 契约，再评估是否需要独立服务。不得通过让 GridTrading 直接依赖 AKsignal 内部实现来替代契约设计。
