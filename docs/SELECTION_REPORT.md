# Layer ③ 报告说明（SELECTION_REPORT）

> **报告结构真源：`src/selection/report_spec.yaml`（声明式 Spec）**
> **人类可读说明：本文档（Spec 的镜像，以 YAML 为准）**
> **决策行为真源：`src/selection/selection.py` + `recommendation.py`**
> **HTML：Report Engine 解释 Spec + ReportViewModel 的生成结果**

> v0.11：Layer③ 每日发布**收敛 ETF-only**（`meta.scope=etf_only`，无个股区块）；
> ETF 审计详情新增「数据(L2)/阶段(L3)」列（three_lane 透传）；个股矩阵仅在存在个股行时渲染。
> v0.12.0 Selection V2：ETF 表达路径三段化（**③A Eligibility → ③B Vehicle Selection → ③C Timing**）；
> `selection_score` = 载体适配度（amount），rps15 只作展示不复算排序。
> v0.12.1 IA：HTML 收敛为**决策漏斗 8 段**（见 §0），canonical fixture = 20260902。
> 关键拆解：**③B 表达载体 ≠ ③C 今日执行** —— 载体行永远展示选定 CORE/SUB，
> 执行行单独给 BUY/WATCH/WAIT + Lane 原因（如 20260902 AI：载体 159819，今日执行 WAIT，
> Lane1 未达趋势门）；不再出现「首选表达：— 无合格标的」。

改报告内容 = 改 `report_spec.yaml`（结构/顺序/列/标签/条件/优先级），
改完重新冻结 golden（`tests/fixtures/report/*.golden.html`）。**不在 report.py 拼 HTML。**

---

## 0. 信息架构：决策漏斗（v0.12.1，8 段）

报告按「主题成立 → 谁可靠可用 → 选哪只表达 → 现在能不能买 → 为什么 → 什么会改」组织，
而非镜像数据结构。**③B 表达载体与 ③C 今日执行是两个独立对象**：

```
③ 今日投资建议
├─ 01 今日结论         action (BUY/OBSERVE/WAIT) + one_liner + 今日可执行 chips + 关键变化
├─ 02 Theme Confirmation   主题成立吗？确认面 / 依据（Tier/行业）
├─ 03 ③A Eligibility       有多少可靠可用 ETF（eligible / pool）
├─ 04 ③B 表达载体          同方向可靠候选中的表达载体（CORE/SUB + 载体适配度）
├─ 05 ③C Timing            载体当前执行：Lane1 趋势 / Lane2 / Lane3 → BUY/WATCH/WAIT
├─ 06 Why Now              当前为什么是这个结果（不买 / 买 的直接原因）
├─ 07 Next Trigger         什么变化会改变结果（只列真能改 Policy 结果的条件）
└─ 08 决策审计             appendix：全 ETF · reason_codes · Lane 上下文
```

阅读顺序：**结论 → 主题成立 → ③A → ③B → ③C → Why → Next → 证据**。
阅读前提：**载体入选 ≠ 今日可买**——③B 给出「用哪只表达」，③C 才回答「现在买不买」。

## 1. 八段明细

### 01 今日结论（`renderer: decision_summary`）
| 元素 | 内容 |
|---|---|
| 判断 verdict | action.level（BUY/OBSERVE/WAIT）+ 跨主题一句话 |
| 今日可执行标的 | `recommended_actions` 卡片（有 BUY 时才出现） |
| 今日关键变化 | 变化日志 top5（up/down）；跨日 diff 归此段，不再单独成段 |

### 02 Theme Confirmation（`renderer: theme_confirmation`）
表：主题 / 成立（已成立·未成立）/ 确认面 / 依据（Tier Gate 或行业）/ 今日怎么做。
`confirmed` 来自 theme 事实；确认依据读 `why.confirmation_reason`。

### 03 ③A Eligibility（`renderer: eligibility`）
表：主题 / ③A 可靠可用（eligible_etf_count）/ 关键词命中（etf_pool_total）/ 资格说明 / Lane 说明。
资格口径 = 账户可交易 ∧ 流动性 ∧ Lane2 数据可靠 ∧ 有效行情；**趋势态不是资格**。
`eligible_etf_count` 即「③A 通过数」（Vehicle Universe 大小）。

### 04 ③B 表达载体（`renderer: vehicle`，每主题一卡）
- 载体 = `recommendation.primary_etf` / `etf_monitoring[monitoring_source=recommendation]` 的 CORE/SUB 行。
- 列：表达载体（名称）/ 代码 / 角色（核心载体·细分载体）/ 适配度（selection_score）/ 为什么是它（engine reason）。
- 载体候选来自 ③B 选车（eligible 内同方向去重，适配度最高者）。

### 05 ③C Timing · 今日执行（`renderer: timing`，每主题一卡）
对选定载体逐行展示：载体 / **今日执行**（BUY·买入 / WATCH·观察 / WAIT·等待）/ Lane1 趋势 /
位置 / Lane2 / Lane3 / 信号 / 原因。`__exec__` 语义 = ③C timing 结果：
`recommended → BUY`；`below_trend_gate → WAIT`；HOLD/WATCH 原样；否则 signal。
示例（20260902 AI）：载体 人工智能ETF易方达 159819 → 今日执行 WAIT · Lane1 未达趋势门。

### 06 Why Now（`renderer: why_now`）
每主题一句当前结论的直接原因：未成立→不买；成立无载体→等待合格载体；有载体未达 BUY→
载体已入选但 ③C=WAIT（Lane1 未达趋势门 / 破位 / 高位）；已推荐→可执行买入。

### 07 Next Trigger（`renderer: next_trigger`）
**只列真能改变 Policy 结果的条件**：未成立→任一 Tier 进入确认门；成立但无合格载体→
出现 ≥1 只 ③A 合格 ETF；载体 WAIT→进入趋势态（RPS15 ≥ 80）或位置回到可执行区 / 修复破位。
已定格（可执行）主题不列无意义条件。

### 08 决策审计（`renderer: audit`，appendix 折叠）
更冷的 debug/audit console，**两层结构**：

```
08 决策审计
├─ 审计摘要（debug index）   ETF 审计 · N 只｜③A 合格 · 载体 · 时机到 · 数据不可靠 …
├─ 第一层 Decision Audit     ETF | 分类 | 主题/角色 | 为什么 | reason_codes | RPS15 | Lane1 趋势 | 位置
└─ 第二层 Technical Detail   <details> 默认折叠（全字段 + Lane 上下文）
```

- **审计摘要计数 = 单标签互斥**（求和 = 总行数）。
- **个股矩阵**沿用 v2 分类（ETF-only 发布无个股行时整块不渲染）。
- 第一层「为什么」读 `reason_codes` / blocking：不可靠→`③A 数据不可靠`；流动性→`成交额不足`；
  未达趋势→`RPS15 · 未达趋势门`；载体落选→`同方向已有代表入选`。
- 行带稳定 `_asset_key`（code）作为内部 row key。

### 08b ETF 状态矩阵（两层审计，V2 分类）

ETF 审计回答：**这只 ETF 卡在 ③A 资格 / ③B 载体 / 还是 ③C 时机？**

```
ETF 状态矩阵
├─ 审计摘要 + ETF 分类计数（互斥）
└─ 第一层 Decision Audit    ETF | 分类 | 主题/角色 | 为什么 | reason_codes | RPS15 | Lane1 趋势 | 位置
```

- **ETF 专属分类**（`_etf_audit_category`，V2 语义，先资格后时机）：
  `BREAKDOWN（破位）→ UNRELIABLY（③A Lane2 不可靠）/ LOW_LIQUIDITY（③A 流动性）/
  BELOW_ACCOUNT（③A 不在账户）/ BLOCKED（POSITION_HIGH/RISK）→ VEHICLE（③B 载体：recommendation 源）
  → TIMING_READY（③C recommended）→ ELIGIBLE（③A 合格未入选）→ DEDUP_LOST（③B 载体落选）
  → BELOW_TREND（③C 未达趋势）→ DYNAMIC_WATCH`
- **`主题 / 角色`（ETF 化）** = theme · 核心ETF/细分ETF（载体源）或 · 动态观察（watchlist 源）。
- **`为什么`（ETF 化）**：破位→`中期破位 · MA60 -36.0%`；数据不可靠→`③A 资格门未过`；
  流动性→`成交额不足`；未达趋势→`RPS15 22 · 未达趋势门`；推荐→`—`。
- **rps1 / ΔRPS15**：Layer① rotation 事实原值透传（Observation factual passthrough，不参与决策）。

## 2. 多标签分类 + display priority

```python
@dataclass
class ReportClassification:      # 多标签事实，可重叠
    actionable: bool
    changed: bool
    degraded: bool
    watch: bool

    def display_state(priority) -> str:   # 单标签展示
        for label in priority:
            if getattr(self, label, False): return label
        return "normal"
```

`display_priority` 在 Spec（`display_priority: [degraded, changed, actionable, watch, normal]`），
优先级归 Spec 控制，业务代码不硬编码。例：高现金流 = ACTIONABLE+CHANGED+DEGRADED → display_state=DEGRADED。

## 3. 派生字段边界（冻结）

```
today Layer③ JSON ────┐
                       ├─ report_changes → ReportViewModel → HTML
previous Layer③ JSON ─┘
```

`classification / panel（confirmation/eligibility/vehicle/timing/why/next_trigger） / change /
display_state` **全部属于 ReportViewModel（纯内存）**，绝不写回核心 JSON
（`tradable_candidates.json` 仍 = 事实 + 决策）。
未来若需审计报告生成过程，加 `tradable_candidates_{date}.report_manifest.json`，不塞回核心 JSON。

## 4. 指标口径速查

| 指标 | 口径 | 定义位置 |
|---|---|---|
| ETF RPS15/20/60 | 相对全市场 ETF 横截面百分位 | Layer① `rotation` |
| 行业 RPS15 | 相对 124 申万二级横截面百分位 | Layer② `confirmation` |
| eligible_etf_count | ③A 资格通过数（可交易∧流动性∧Lane2 可靠∧有效行情） | Layer③ selection |
| selection_score | ③B 载体适配度（amount，去 timing） | Layer③ selection |
| signal | STRONG_BUY/BUY/WATCH/HOLD/WAIT | `strategy_spec.signal_policy` |
| lane2_reliable_360 | Lane2 数据可靠性（③A 前置资格） | `three_lane` |
| technical_diagnostics | trend/momentum/relative_strength 三维 + level | `asset_state.py` |
| blocking/data_quality_flags | 共享语义接口（STALE_DATA 归 data_quality） | `asset_state.py` |

## 5. 修改指南

| 想改什么 | 改哪里 |
|---|---|
| 报告模块/顺序/列/标签/条件/优先级 | `src/selection/report_spec.yaml` + 重新冻结 golden（canonical=20260902） |
| Panel 派生 / Why / Next trigger / 审计分类 | `src/selection/report_viewmodel.py` |
| cell 格式化逻辑（数值/结论/状态） | `src/selection/report_formatters.py` |
| 跨日变化规则 | `src/selection/report_changes.py` |
| 渲染器（HTML 拼装） | `src/selection/report_engine.py` |

纪律：**只改展示不改决策**；改动后同步本文档与 AGENTS.md；渲染确定性由
`tests/selection/test_report_v2.py`（逐字节 golden）守护。
