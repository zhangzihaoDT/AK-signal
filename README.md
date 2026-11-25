# AKsignal — Financial Data Assets & Research Platform

当前版本标记仍为 `VERSION`（变更记录见 [CHANGELOG.md](CHANGELOG.md)）。AKsignal 的职责是采集、治理并沉淀可追溯的金融数据资产，向下游项目提供稳定、可复现的数据文件，并基于这些资产开展市场观察与专题研究。

## 项目边界

```text
Collection → Data Assets → Serving → Research
     │             │            │          ├─ Market Facts 观察日报
     │             │            │          ├─ Holdings Dashboard
     │             │            │          ├─ CSI500 Price Channel
     │             │            │          ├─ 正式 Basket Compare
     │             │            │          └─ IC500 期限结构专题研究
     └─ 来源证据、身份、日期、质量门、历史版本与快照

GridTrading：策略开发、参数优化、策略回测、交易信号、交易账本与策略自动化
```

AKsignal **不新增或维护独立交易策略运行体系**，不生成交易信号，不管理交易账本，也不重复建设 GridTrading 的策略和执行能力。市场事实与交易策略分离；Holdings Dashboard 只观察个人资产及持仓事实，不负责执行交易。

## 数据资产与正式来源

| 数据资产 | 当前来源 / 处理边界 |
|---|---|
| ETF 行情 | 东方财富 / 新浪模块路由；AKShare 为适配器。保留逐序列来源、日期和价格口径，不拼接已知不同口径。 |
| 股票及沪深300 | Longbridge OpenAPI / ForwardAdjust；公司行为算法未独立认证时保持 `price_basis_verified=false`。 |
| 核心指数 | 按 `core_indices.yaml` 固定来源；Longbridge 与中证官方指数序列各自独立。 |
| 股指期货 | CFFEX 官方日行情；按具体到期合约记录，不拼接连续合约。 |
| 行业指数 | TDX 官方 `.day`，固定 128 个 TDX Research Industry；SW2021 仅作为保留的历史研究数据，不再是正式运行或回退来源。 |

来源、价格依据、分类版本、源交易日、目标交易日、生成时间、覆盖情况、质量状态、Manifest 与快照摘要均应可追溯。缺失或质量不足保持 `UNAVAILABLE` / `INSUFFICIENT_HISTORY`，不以旧数据冒充新数据。详见 [来源契约](docs/OBSERVATION_DATA_SOURCES.md) 与 [数据存储说明](data/README.md)。

## 日常运行与报告

唯一完整日常入口为 `make run-day`；离线运行使用 `make run-day-offline`。主链为 ETF 行情更新/事实计算 → 个股事实 → 核心指数、CFFEX 与 TDX 观察构建 → 验收 → 原子发布。日报只发布市场事实，不输出买入建议或交易 action。

```bash
make run-day
make run-day RUN_DATE=20261124
make run-day-offline RUN_DATE=20261124
make run-day-status
make run-day-check
make daily-scheduler-status
```

正式日报入口：`outputs/observation/observation_{date}.html`，并带同日 JSON、Markdown、Parquet/CSV 事实和发布回执。批次输出从空目录构建；验收失败时保留上一正式发布。历史批次和快照不因瘦身清理而批量删除。

专题与个人页面：

- `Holdings Dashboard`：个人资产和持仓事实观察。
- `CSI500 Price Channel`：中证500 / 中证1000 历史价格轨道。
- `Basket Compare V2`：正式研究报告，输出不是实际组合收益或策略回测。
- `IC500`：全历史与期限结构研究，不要求自动更新。

本地入口见 [`dashboards/index.html`](dashboards/index.html)。刷新卡片链接与元信息运行 `make dashboards-index`；看板 HTML 仍留在原有 `data/audits/` 路径，不复制到目录页。

## 项目结构

- `config/`：数据来源、资产身份、分类、市场观察和研究配置。
- `src/market_facts/`：标准化数据契约、来源适配、质量门、数据采集与市场事实计算。
- `src/observation/`：日报事实组织、展示和验收。
- `src/research/baskets/`：正式 Basket Compare V2。
- `scripts/`：数据维护、审计、验证与独立专题研究工具。
- `data/`：raw / processed / audits / snapshots / state 等金融数据资产；受 `.gitignore` 保护，不进行未分类清理。
- `dashboards/`：本地 HTML 入口页及目录更新工具。

详细结构见 [PROJECT_STRUCTURE.md](docs/PROJECT_STRUCTURE.md)，当前能力状态见 [ARCHITECTURE_MAP.md](docs/ARCHITECTURE_MAP.md)，跨项目数据供给边界见 [DATA_SERVING_CONTRACT.md](docs/DATA_SERVING_CONTRACT.md)。本次职责收敛与退役记录见 [RETIREMENT_20261125.md](docs/validation/RETIREMENT_20261125.md)。
