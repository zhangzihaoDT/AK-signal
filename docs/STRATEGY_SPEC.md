# AKsignal Strategy Specification（v0.6.1）

> Strategy 的定义与 config 的定位、参数与规则边界、硬编码审计清单。
> 核心原则：**可研究、可扫描、可比较、可能按主题变化的内容进入 config；算法定义、执行语义和系统不变量继续留在代码。**

## 1. Strategy 的定义

```text
Strategy
=
Strategy Specification      （config/ 中的策略知识与可实验参数）
+ Rule Implementation        （代码中的规则实现）
+ Execution Semantics        （撮合/状态机等系统契约）
+ Validation Evidence        （Replay Parity / 回测 / 事件研究证据）
```

**config 单独不等于完整 Strategy**。config 是「大脑」，代码是「手脚」，数据是「食物」，回测是「体检报告」。

## 2. 配置分层（config/）

| 文件 | 职责 | 不含 |
|---|---|---|
| `themes_two_directions.yaml` | 主题 / bucket / 行业焦点组 / ETF 关键词 / 启用状态 | 费用、组合权重 |
| `stock_universe.yaml` | 资产池 / 主题映射 / 黑名单 / Universe | Entry/Exit 阈值、Portfolio 分配 |
| `strategies.yaml` | 主题级策略（entry/exit 参数 + strategy_id + 权重） | Portfolio 参数 |
| `indicators.yaml` | RPS/MA 窗口、信号门限（ETF 趋势门/个股合格线/确认阈值） | 指标算法 |
| `execution.yaml` | 执行模型 / fee / slippage / leverage / pyramiding | 撮合实现 |
| `portfolio.yaml` | 初始资金 / 持仓 / 单资产上限 / deploy / 权重 | Entry/Exit 策略参数 |
| `sw_industry_rps.yaml` | 申万模块自身配置（provisional/storage 等） | — |

## 3. 参数 vs 规则边界（示例）

```text
rps15_min = 80                 → 参数，进 config（strategies.yaml / indicators.yaml）
RPS 如何计算（百分位）           → 规则实现，留代码
execution.model = next_open    → 执行模型选择，进 config
next_open 如何找下一交易日        → 执行语义，留代码
MA 窗口 = 20                    → 参数，进 config
MA20 是否只用判断日当日及以前数据    → 执行语义（as-of 不变量），留代码
fee_bps = 5                    → 参数，进 config（execution.yaml）
Sharpe / Calmar 如何计算          → 算法，留代码
```

判断标准：**参数是否可能成为下一轮敏感性扫描、策略比较或主题差异化实验的自变量。是 → config；否 → 代码。**

## 4. Loader 与校验

业务代码不直接读 YAML，通过统一 Loader 获取**经过校验、不可变（frozen）**的 typed 配置：

```python
load_strategy_spec(strategy_id)     # StrategySpec（entry + exit + universe_mode + weight）
load_indicator_spec()               # IndicatorSpec（RPS 窗口 / 信号门限）
load_execution_spec()               # ExecutionSpec（fee/slippage/model）
load_portfolio_spec()               # PortfolioSpec（资金/持仓/权重）
```

Schema 校验（`src/common/spec/schema.py`）在**运行开始阶段**失败：
- 类型 / 必填 / 数值范围 / 枚举值
- 跨字段：`fixed_horizon` 必须带 `horizon`；`ma_exit` 必须带 `ma_window`；`trend_confirmation` 必须带 `rps15_min` + `allowed_trend_states`
- 重复 `strategy_id` / 不存在的 theme / 非法 universe_mode / 不支持的 policy

**生产路径无隐藏默认值**：`config.get("x", 80)` 这类隐式默认已禁止；影响结果的参数必须显式存在于配置。

## 5. Hash 边界

| Hash | 覆盖 | 变化条件 |
|---|---|---|
| `config_hash` | 全部策略配置（主题/资产池/策略/指标/执行/组合/行业）+ rule_version，**order-independent** | 任一配置数值变化 |
| `universe_hash` | 实际参与运行的资产集合（排序后哈希） | 资产增删；顺序变化不改变 |
| `rule_version` | 规则实现代码语义版本（= v0.6.1） | 算法定义变化才改 |

配置数值变化（如 `rps15_min: 80 → 75`）只改 `config_hash` 不改 `rule_version`；RPS 算法改变才改 `rule_version`。

## 6. Provenance

历史信号、逐笔交易、Portfolio 报告、Construction 实验记录：
`strategy_id / rule_version / config_hash / universe_hash / universe_mode / execution_model`。

可回答：两次回测是否同一策略？配置/Universe/数据/执行模型是否变化？结果变化来自策略、数据、Universe 还是代码版本？

注：`historical_signals` 是**策略无关**的信号层（Layer①②③ 在策略之前生成）；`strategy_id` 进入策略相关的交易与组合产物。

## 7. 硬编码审计清单（v0.6.1 已迁移）

| 位置 | 当前值 | 分类 | 是否迁移 | 目标配置 |
|---|---|---|---|---|
| `etf_signal/signal.py` compute_trend_state | strong 80 / watch 60 | A | ✅ | indicators.signal_gates.etf |
| `etf_signal/rotation.py` RPS_WINDOWS | (15,20,60) | A | ✅ | indicators.rps |
| `selection/selection.py` ETF_TREND_GATES | {BUY_CANDIDATE, STRONG_WATCH} | A | ✅ | indicators.signal_gates.etf.gate_states |
| `selection/selection.py` STOCK_QUALIFIED_SCORE | 70 | A | ✅ | indicators.signal_gates.stock.qualified_score |
| `selection/selection.py` ETF_MIN_AMOUNT | 5e7 | A | ✅ | indicators.signal_gates.etf.min_amount |
| `sw_industry_rps/confirmation.py` 90/80/60 | 90/80/60 | A | ✅ | indicators.confirmation |
| `backtest` entry rps15_min / fee / slippage | 80 / 5bp / 5bp | A | ✅ | strategies.entry / execution.yaml |
| `portfolio` max_positions / max_weight / deploy | 5 / 0.2 / 1.0 | A | ✅ | portfolio.yaml |
| RPS 百分位/趋势分计算 | — | B | 留代码 | — |
| next_open / unfilled / as-of / no_pyramiding | — | C | 留代码+测试 | — |
| report.py 90/80/70 展示阈值 | 90/80/70 | D | 不纳入 | — |

分类：A=Strategy Parameter（→config）；B=Algorithm Constant（留代码+说明）；C=Execution Invariant（留代码+测试）；D=Display/Formatting（不纳入）。

## 8. 验收（已满足）

1. 可实验阈值/窗口/状态集合/Policy 选择 → 统一配置 ✅
2. 算法/执行语义/系统不变量 → 留在代码 ✅
3. 业务模块经统一 Loader 获取配置 ✅
4. 生产路径无隐藏默认值 ✅
5. Schema 完整校验 ✅
6. 每个策略稳定 `strategy_id` ✅
7. 信号/交易/组合产物 Provenance ✅
8. `config_hash / universe_hash / rule_version` 边界明确 ✅
9. 硬编码审计清单 ✅
10. Daily/Replay/Trade/Portfolio 全链路 Parity ✅
11. 现有回测结论无业务变化 ✅
12. 全量测试通过 ✅
13. 架构文档 + Agent 指令更新 ✅
