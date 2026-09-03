# Layer ③ 报告说明（SELECTION_REPORT）

> **报告结构真源：`src/selection/report_spec.yaml`（声明式 Spec）**
> **人类可读说明：本文档（Spec 的镜像，以 YAML 为准）**
> **决策行为真源：`src/selection/selection.py` + `recommendation.py`**
> **HTML：Report Engine 解释 Spec + ReportViewModel 的生成结果**

> v0.11：Layer③ 每日发布**收敛 ETF-only**（`meta.scope=etf_only`，无个股区块）；
> ETF 审计详情新增「数据(L2)/阶段(L3)」列（three_lane 透传）；个股矩阵仅在存在个股行时渲染。

改报告内容 = 改 `report_spec.yaml`（结构/顺序/列/标签/条件/优先级），
改完重新冻结 golden（`tests/fixtures/report/*.golden.html`）。**不在 report.py 拼 HTML。**

---

## 0. 信息架构：三层阅读结构

报告按人的决策路径组织，而非按数据结构镜像：

```
③ 今日投资建议
├─ 01 今日结论      30 秒决策页：判断 / 一句话 / 今日可执行标的 / 今日关键变化
├─ 02 主题状态      矩阵：主题｜状态｜今天怎么做｜首选表达｜变化
├─ 03 为什么        论点式叙事（只解释异常）
├─ 04 怎么表达      首选 / 备选 / 不做什么（不遗漏正常机会）
├─ 05 风险与变化    与昨日相比（新增破位 / 状态恶化 / 数据降级）
└─ 06 决策审计      debug / audit console：个股矩阵 / ETF 矩阵 / 指标口径
```

阅读顺序：**结论 → 比较 → 解释 → 行动 → 异常 → 证据**。
原则：**只解释异常**——NORMAL 压缩，degraded/changed/actionable 展开。

## 1. 六段明细

### 01 今日结论（`renderer: decision_summary`）
| 元素 | 内容 |
|---|---|
| 判断 verdict | action.level（BUY/OBSERVE/WAIT）+ 跨主题一句话（每主题一从句合成） |
| 今日可执行标的 | `recommended_actions` 卡片：名称 · BUY · LEADER · MID · 主题 |
| 今日关键变化 | 变化日志 top5（up/down，如新增破位、表达降级） |

### 02 主题状态（`renderer: theme_matrix`）
列定义在 Spec `theme_matrix_columns`：主题 / 状态（display_state tag）/ 今天怎么做 / 首选表达 / 变化。
`display_state` 由多标签分类 + Spec `display_priority` 推导。

### 03 为什么（`renderer: theme_narrative`，`expand.only: [degraded, changed, actionable]`）
- 严格 exception-based：NORMAL 主题**不展开**；WATCH 仅「接近确认」时出现。
- **论点式**：标题 + 论证段 + 证据表。指标成为证据，不是正文。
- **证据驱动**：Spec 定义叙事结构（`narratives.*` 的 title/clauses/evidence_labels）与
  clause→触发谓词映射；`report_narrative.py` 解析「哪些事实支持哪个 clause」后动态组合论证句，
  不在 YAML 硬编码整段结论（如 HHI/Top3 高才出现「收益集中」）。
- 叙事类型：`degraded_execution`（结构判断与可执行产品不一致）/ `trend_diffusing`（趋势扩散）/
  `approaching`（尚差一步）。

### 04 怎么表达（`renderer: execution_cards`）
- `include: [actionable, degraded, confirmed]`——**不遗漏正常机会**。
- `normal_mode: compact`：NORMAL 已确认主题压成一行首选。
- `exception_mode: expanded`：degraded/changed 主题展开 首选/备选/不做什么 + 降级说明。

### 05 风险与变化（`renderer: change_log`）
- **跨日对比**：读 `date < 当前 selection_date` 的最近一份 Layer③ JSON（按 date，不用 mtime）。
- fail-soft：上一份缺失/损坏 → `comparison_status=UNAVAILABLE`，绝不阻塞 HTML；
  version 不兼容 → `VERSION_MISMATCH`，跳过结构 diff。
- diff 只进 `report_changes.py → ReportViewModel`，**永不写回 recommendation 对象**。
- 无上一份时回退日内变化（表达降级 / ETF 0/x 无一通过 / 新增破位）。

### 06 决策审计（`renderer: audit`，appendix 折叠）
更冷的 debug/audit console，**两层结构**：

```
06 决策审计
├─ 审计摘要（debug index）   个股审计 · 23 只｜破位 2 · 阻塞 5 · 合格未选 4 · 推荐 3 · 正常观察 7 · 仅监控 2
├─ 第一层 Decision Audit     6 列（异常优先排序，默认展开）
│    标的 | 主题 / 角色 | 当前结论 | 为什么 | 趋势 | 变化
└─ 第二层 Technical Detail   <details> 默认折叠（全字段）
     主题 | 赛道 | 参与 | 技术状态 | 阻塞 | 数据 | 证据阶段 | 日变化 | 趋势分 | 信号 | 位置 | 数据状态
```

- **审计摘要计数 = 单标签互斥**（求和 = 总行数），分类优先级：
  `BREAKDOWN → BLOCKED（有意义阻塞，排除纯 BELOW_TREND_GATE）→ QUALIFIED_UNSELECTED → RECOMMENDED → NORMAL → MONITOR_ONLY`
  ——monitor_only 是 provenance，**不覆盖风险状态**（monitor-only 且破位 → 归「破位」）。
- **第一层「为什么」= 当前结论的首要原因**（非「未推荐原因」）：破位→`中期破位 · MA60 -18.2%`、持有→`高位不追`、观察→`CORE×MID，仅观察`、等待→`未达趋势门`、推荐→`—`。
- **`主题 / 角色`** = theme_label · role（龙头/高弹性/设备与上游，与决策结构相关）；`tier_label`（赛道）下沉第二层。
- **排序**：Spec `audit.stock_matrix.sort: anomaly_first`（分类 rank + 组内趋势分降序）。
- 两张表行带稳定 `_asset_key`（code）作为内部 row key，为未来行级展开预留。

### 06b ETF 状态矩阵（两层审计，框架统一、证据不同）

ETF 审计回答的不是「为什么没进入推荐」，而是：**这个主题有没有可交易的 ETF 产品？卡在趋势/位置/流动性/还是排名？**

```
ETF 状态矩阵
├─ 审计摘要 + 主题产品可用性
│    ETF 审计 · 25 只｜破位 6 · 阻塞 14 · 合格未选 1 · 推荐 1 · 动态观察 3
│    主题产品可用性：AI 基建 38/127 可交易 · 汽车 0/55 ⚠ · 高现金流 0/32 ⚠
├─ 第一层 Decision Audit   6 列（异常优先，组内按 RPS15 降序）
│    ETF | 主题/角色 | 当前结论 | 为什么 | 强度 | 位置
└─ 第二层 Technical Detail <details> 默认折叠
      主题 | 来源 | 主题排名 | 龙头/核心 | RPS15 | RPS1 | ΔRPS15 | 趋势状态 |
      成交额 | 位置 | 位置值 | 阻塞 | 数据 | 信号 | 原因
```

- **决策链**：主题确认 → 产品强度（RPS15）→ 趋势门 → 位置 → 流动性 → 排名，故第一层用「强度+位置」而非「趋势+变化」。
- **ETF 专属分类**（`_etf_audit_category`，不复用个股）：
  `BREAKDOWN → BLOCKED（物料阻塞 = {LOW_LIQUIDITY, POSITION_HIGH, RISK_WARNING}，排除纯 BELOW_TREND_GATE 与 DEDUP_LOST/SIGNAL_WATCH）→ QUALIFIED_UNSELECTED（趋势达标未入选）→ RECOMMENDED → NORMAL（核心/卫星产品在决策池）→ DYNAMIC_WATCH（动态关键词匹配观察）`
- **`主题 / 角色`（ETF 化）** = theme · 核心ETF/卫星ETF（推荐来源）或 · 动态观察（watchlist 来源）。
- **`为什么`（ETF 化）**：破位→`中期破位 · MA60 -36.0%`；流动性→`成交额不足`；去重→`同主题有更优产品`；趋势门→`RPS15 22 · 未达趋势门`；排名→`同主题排名第 3，未进入核心产品`；动态→`动态观察，不在主题核心产品池`；推荐→`—`。
- **主题产品可用性**：把 theme 既有 `eligible_etf_count/etf_pool_total` 提到审计入口（如「高现金流 0/32 可交易 ⚠」），一眼看到「不是没有 ETF，而是都不达标」。
- **rps1 / ΔRPS15**：Layer① rotation 事实原值透传（selection.py additive 字段，Observation factual passthrough，不参与决策）。

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

`classification / narrative / change_log / display_state / expanded_collapsed` **全部属于
ReportViewModel（纯内存）**，绝不写回核心 JSON（`tradable_candidates.json` 仍 = 事实 + 决策）。
未来若需审计报告生成过程，加 `tradable_candidates_{date}.report_manifest.json`，不塞回核心 JSON。

## 4. 指标口径速查

| 指标 | 口径 | 定义位置 |
|---|---|---|
| ETF RPS15/20/60 | 相对全市场 ETF 横截面百分位 | Layer① `rotation` |
| 行业 RPS15 | 相对 124 申万二级横截面百分位 | Layer② `confirmation` |
| 趋势分（个股） | 0-100 绝对技术评分 | `trend_engine.scoring` |
| position_pct | `ma60_deviation`=乖离率%；`price_percentile`=历史分位 | `strategy_spec.historical_position` |
| signal | STRONG_BUY/BUY/WATCH/HOLD/WAIT | `strategy_spec.signal_policy` |
| technical_diagnostics | trend/momentum/relative_strength 三维 + level | `asset_state.py` |
| blocking/data_quality_flags | 共享语义接口（STALE_DATA 归 data_quality） | `asset_state.py` |

## 5. 修改指南

| 想改什么 | 改哪里 |
|---|---|
| 报告模块/顺序/列/标签/条件/优先级 | `src/selection/report_spec.yaml` + 重新冻结 golden |
| 叙事 clause 模板与触发谓词映射 | `report_spec.yaml narratives.*`（谓词解析在 `report_narrative.py`） |
| cell 格式化逻辑（数值/结论/状态） | `src/selection/report_formatters.py` |
| 跨日变化规则 | `src/selection/report_changes.py` |
| 多标签分类 / ViewModel 组装 | `src/selection/report_viewmodel.py` |
| 渲染器（HTML 拼装） | `src/selection/report_engine.py` |

纪律：**只改展示不改决策**；改动后同步本文档与 AGENTS.md；渲染确定性由
`tests/selection/test_report_v2.py`（逐字节 golden）守护。
