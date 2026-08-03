# 项目状态

## 运行基础设施标准化 P1 ✅

`common/` 公共层建立：`paths.py`（路径单一事实源）、`run_context.py`（运行描述）、`manifest.py`（产物发现契约）。

### 目录结构

```text
AKsignal/
├── config/
├── data/                         # 运行数据与状态（gitignored）
│   ├── raw/
│   ├── processed/
│   └── state/asset_state.csv
├── outputs/                      # 用户消费产物（gitignored）
│   ├── stock_trend/
│   ├── sw_industry_rps/
│   └── manifest.json
├── docs/
├── config/
│   ├── etf_buckets.yaml
│   ├── etf_signal_rules.yaml
│   ├── etf_universe.yaml
│   ├── guojin_tradable_whitelist.csv
│   ├── stock_pool.csv
│   └── sw_industry_rps.yaml
├── data/                         # 运行数据与状态（gitignored）
│   ├── etf_signal/
│   ├── processed/
│   ├── raw/
│   └── state/
├── outputs/                      # 用户消费产物（gitignored）
│   ├── etf_signal/
│   ├── stock_trend/
│   ├── sw_industry_rps/
│   └── manifest.json
├── docs/
├── reports/                      # 日报
│   └── etf_daily/
├── scripts/
├── src/
│   ├── main.py                   # 纯路由
│   ├── common/                   # 公共层
│   │   ├── paths.py
│   │   ├── run_context.py
│   │   └── manifest.py
│   ├── stock_trend/
│   ├── sw_industry_rps/
│   └── etf_signal/               # 新增：ETF 趋势信号
└── tests/
    ├── stock_trend/
    ├── sw_industry_rps/
    └── etf_signal/                # 新增
```

### 测试总览

| 模块             | 测试数  |
| ---------------- | ------- |
| 个股趋势监控     | 143     |
| 申万行业 RPS     | 58      |
| ETF 趋势信号     | 0       |
| **总计**         | **201** |

---

## 个股技术趋势监控

### AKSignal v0.4.3 ✅（多主题 · 两方向）

在 v0.4.2 三层信号链之上增加多主题能力（增量升级，不另起新系统）：

```
Layer ① ETF 发现 → Layer ② 主题确认（Theme Confirmation）
    → Layer ③ 多主题交易候选（buckets[].themes[]）→ Layer ④ Portfolio（未来）
```

- **Theme Registry**：`config/themes_two_directions.yaml` 为唯一事实源（bucket → theme → SW 行业证据 + ETF 关键词），Layer ①②③ 共同消费
  - Core：AI 基础设施（不含 AI 应用）/ Quality：高现金流资产（电力·运营商·公用事业）
  - Future Themes（Not Enabled）：Resource Cycle / High-end Equipment / Aerospace / Shipping 可在配置重新打开
- **Layer ①**：rotation 新增 theme 列，报告按每主题焦点组展示（多主题主线焦点）
- **Layer ② 改名「主题确认」**：确认目标是 Theme，SW 行业 / ETF / 参与率 / HHI 均为确认因子；confirmation parquet 新增 bucket/theme 列 + bucket 聚合 + 每主题背离
- **Layer ③**：`layer3.buckets[].themes[]` 多主题候选；顶层 Action 收敛为「今日方向」（BUY/OBSERVE/WAIT + Bucket + Theme），ETF/股票/观察池落在下层；`recommended_actions` 跨主题去重（primary = 首个 bucket）
- **配置健康**：未注册 theme → 告警 + degraded 标记（`select run --strict` 中止）；跨主题资产经 `config_issues.cross_theme_assets` 暴露
- 资产池：`config/stock_universe.yaml` 保持 theme → tier → assets（bucket 由 themes 配置推导，不重复维护）
- 实测：20260731 横截面两方向跑通（高现金流确认 BUY，AI 基础设施观察）；255 项测试通过

### AKSignal v0.4.0 ✅

三层决策信号链 + Trend Engine：

```
Layer ① ETF 发现（etf_signal）→ Layer ② 行业确认（sw_industry_rps confirm）
    → Layer ③ 交易候选（selection）→ Candidate JSON → Layer ④ Portfolio（未来）
Trend Engine（trend_engine）：selection 内部依赖，无独立入口
```

- Layer ①：全市场横截面 RPS15/20/60 + 5 日排名变动 → `etf_rotation_{date}.html`
- Layer ②：10 重点行业群共振 + 子主题（ai_core/TMT/智能制造）+ 龙头广度 → `sw_industry_confirmation_{date}.html`
- Layer ③：表达方式决策（ETF vs 个股）+ 执行对象压缩 → `tradable_candidates_{date}.json/.html`
- 国金账户：黑名单机制（默认可交易，确认不可交易才加入）

### CLI

```bash
python src/main.py select run            # Layer ③ 交易候选（调用 trend_engine）
python src/main.py etf <cmd>             # Layer ①
python src/main.py industry <cmd>        # Layer ②
make run-day                             # 每日全流程（① ETF + ② SW-RPS）
```

---

## 申万二级行业 RPS 监控 v0.1

### 状态：真实数据全量闭环完成

| 指标               | 值                       |
| ------------------ | ------------------------ |
| 历史行业 master    | 131                      |
| 当前有效行业       | 124                      |
| 退休行业           | 7                        |
| 当前有效行业覆盖率 | 100%                     |
| 历史数据           | 409,028 行               |
| 日期范围           | 1999-12-30 至 2026-07-16 |

bootstrap / update / calculate / validate / report / run-day 均已验证。

### v0.1.1 更新（2026-07-17）

- **freshness probe**：每次 `run-day` 先对 `801016.SI` 执行一次上游 API 调用，确认源端目标日期是否就绪，而非仅依赖本地缓存判断
- **三日期门控**：`requested_target_date` / `source_latest_common_date` / `published_report_date` 严格区分，仅当源端实际到达目标日期后才执行 calculate + report
- **免重复发布**：auto 模式和显式 target-date 均检查最近 completed 报告的日期，无新数据时跳过（`--target-date` 同日期重复运行也跳过），`--force-report` 强制重新生成
- **manifest 保护**：`waiting_for_source` / `no_new_data` 不会覆盖已有 completed 记录

---

## 申万二级行业 RPS 监控 v0.2（已冻结）

### v0.2.1 校准版（进行中）

#### P1：成分股行情获取可靠性加固 ✅

**问题**：原流程逐只从东方财富获取个股行情，受限于请求频率限制，高权重股（如比亚迪 62.5%）经常获取失败，导致覆盖率不可控。

**方案**：legulegu.com 一次返回成分股列表的同时，已附带近 1 日、近 5 日涨跌幅字段。核验后采用：

- **标准窗口（5 日）**：legulegu 涨幅为主 → 零额外 API 调用，覆盖率 100%
- **其他窗口（10 日等）或数值缺失**：回退到东方财富

**核验结果**（20~30 只股票，legulegu vs 东方财富对照）：

| 指标 | 1 日涨幅 | 5 日涨幅 |
|------|---------|---------|
| 对比样本量 | 16 只 | 20 只 |
| 误差中位数 | 0.0000 pp | 0.0000 pp |
| 绝对误差 P90 | 0.0000 pp | 0.0210 pp |
| 误差 ≤ 0.1pp | 100% | 100% |

结论：legulegu 涨跌幅与东方财富前复权口径基本一致，可直接作为主力行情源。

#### P0：验证代理贡献能否解释指数收益

新增四个校准字段到 `DrilldownResult` 和 drilldown 输出：

| 字段 | 含义 |
|------|------|
| `proxy_return_pct` | Σ(市值代理权重 × 个股收益率) |
| `reconstruction_gap_pct` | proxy_return - actual_return |
| `reconstruction_quality` | 良好(good) / 一般(moderate) / 较差(poor) |
| `weight_coverage` | 成功获取行情的成分股权重占比 |

质量判定：`quality_score = 1 - min(|gap| / max(|actual|, 2.0), 1.0)`，≥ 0.8 为良好，≥ 0.5 为一般，否则较差。

#### 0716 日校准结果

| 行业 | 窗口 | actual | proxy | gap | 覆盖 | 质量 |
|------|------|--------|-------|-----|------|------|
| 乘用车 | 5d | 6.02% | 6.66% | +0.64pp | 100% | 良好 |
| 影视院线 | 5d | 4.66% | 4.87% | +0.21pp | 100% | 良好 |

初步迹象正面（两个案例均达良好），但样本量严重不足。需累计 20~30 个行业突破事件后才能系统评估：

- 误差中位数与分布
- 不同行业类型间的差异
- 总市值权重是否存在系统性高估
- 极端单核行业是否更容易失真

### 强势区突破成分股贡献穿透

### 实际产出说明

当前 v0.2 实现的不是 **申万行业指数官方权重归因**，而是**基于申万行业成分范围的市值代理贡献分析**。

两者差异：

| 维度 | 当前方案（市值代理） | 官方归因 |
|------|-------------------|---------|
| 权重来源 | legulegu.com 市值反推 | 申万指数编制规则（自由流通市值、分级靠档等） |
| 权重口径 | 总市值 | 自由流通市值（可能含分级靠档调整） |
| 成分快照 | 当前快照 | 突破时刻的历史成分+权重 |
| 行情来源 | 东方财富个股日线 | 申万行业指数构建时使用的同源行情 |
| 调仓处理 | 不处理 | 需跟踪指数定期/临时调仓 |

**为什么仍有价值**：成分范围与市值加权逻辑与申万指数基础结构相近，在多数情况下是合理的近似。

**需要校验才能使用**：市值口径（总市值 vs 自由流通市值）、成分时点（当前 vs 历史）、调仓事件等因素会对贡献归因产生偏差，目前尚未对齐官方数据。

#### 逻辑链路

```
行业 RPS 日序列 → 识别首次进入强势区
    ↓
获取该行业当期成分股列表及总市值（legulegu.com）
    ↓
总市值占比 → 代理权重 → 获取成分股区间涨跌幅（东方财富）
    ↓
权重 × 涨跌幅 → 代理贡献
    ↓
HHI + 参与率 + Top3 占比 → 判断：
    ↓
contribution_structure × breadth_structure（两维度正交）
    ↓
单核主导/集中领涨/多龙头带动/分散上涨 × 广泛上涨/中度扩散/少数带动/明显分化
```

#### 分类标准

**contribution_structure**（贡献集中度）：

| 标签 | 条件 |
|------|------|
| 单核主导 | top1_contrib_share >= 50% |
| 集中领涨 | top1_weight >= 30% 且 top3_share >= 60% |
| 多龙头带动 | top1_weight < 30% 且 top3_share >= 50%，或 top3_share >= 35% |
| 分散上涨 | top3_share < 35% |

**breadth_structure**（参与宽度）：

| 标签 | 条件 |
|------|------|
| 广泛上涨 | 参与率 >= 70% |
| 中度扩散 | 参与率 >= 40% 且 < 70% |
| 少数带动 | 参与率 < 40% |
| 明显分化 | 下跌家数占比 >= 30% 且负贡献占比 >= 25% |

两者正交组合，例如 `单核主导 × 中度扩散`、`多龙头带动 × 广泛上涨`。

#### 新增模块

| 文件 | 职责 |
|------|------|
| `src/sw_industry_rps/constituents.py` | 从 legulegu.com 获取申万二级行业成分股列表，计算市值代理权重 |
| `src/sw_industry_rps/contribution.py` | 代理贡献分解计算、驱动模式分类（HHI + 参与率 + Top3 贡献占比） |

#### 新增命令

```bash
python src/main.py industry drilldown             # 强势区成分股贡献分析
python src/main.py industry drilldown --window 5  # 使用 5 日窗口
python src/main.py industry drilldown --limit 3   # 最多分析 3 个行业
python src/main.py industry drilldown --output-csv # 输出 CSV 到 outputs/sw_industry_rps/
```

---

## AKSignal ETF Discovery v0.1（✅ 已完成）

P0 方案见 `docs/AKsignal_ETF_P0_方案.md`。

### 正式版本

```text
标签：  v0.1.0-etf-discovery
状态：
  orchestration pipeline：PASSED
  discovery signal：      PASSED
  historical coverage：   PARTIAL（85.0%）
  broker coverage：       PARTIAL（116/300 已验证）
```

### 工作流：从全量 ETF 到候选卡片

```text
                                        注释
300 只核心 Universe                      按成交额 + 资产优先级从 1554 只中筛选
  │
  ▼
255 只历史行情成功                        45 只 588xxx 科创板缺失（新浪不覆盖）
  │
  ▼
252 只指标通过                           3 只上市不足 60 日
  │  计算：return_5d/20d/60d、MA20/60、RPS15/60
  │        波动率、ATR、距高点距离、最大回撤、成交额变化
  ▼
101 只进入活跃池                         151 只 RPS15 < 60 被趋势规则排除
  │  BUY_CANDIDATE  29 只（RPS15≥80 + 均线多头）
  │  STRONG_WATCH   22 只（RPS15≥80，均线未多头）
  │  WATCH          50 只（60≤RPS15<80）
  │
  ▼
101 只国金可交易（116 只白名单覆盖全部活跃池）
  │  VERIFIED_TRADABLE      101
  │  UNVERIFIED              0（活跃池全部已验证）
  │
  ▼
101 张完整候选卡片                       含趋势 + 账户 + 风险标记
  │  complete     101
  │  flagged       0
  │  incomplete    0
  ▼
outputs/etf_signal/
├── candidate_cards_{date}.json           完整卡片（含 risk_flags）
└── watchlist_active_{date}.csv           101 只活跃 ETF 清单
```

### 命令

```bash
# 数据底座
python src/main.py etf bootstrap           # 初始化 Master + 全量历史
python src/main.py etf bootstrap-core      # 阶段 A：核心 Universe（推荐）
python src/main.py etf classify            # 资产类别分类

# 全链路
python src/main.py etf calculate           # 计算技术指标 + RPS
python src/main.py etf pipeline            # watchlist → account → card

# 各步骤独立执行
python src/main.py etf watchlist           # 仅生成趋势关注池
python src/main.py etf account             # 仅账户映射
python src/main.py etf card                # 仅生成卡片
```

### 模块

| 模块 | 行数 | 职责 |
|------|------|------|
| `data_source.py` | 490 | ETF 全市场数据采集（东财 + 同花顺校验 + 新浪备用） |
| `master.py` | 195 | ETF Master 持久化 + 核心 Universe 筛选 |
| `classifier.py` | 210 | 资产类别与暴露分类（primary_asset_class / bucket / tags） |
| `indicators.py` | 130 | 技术指标计算（return / MA / 波动率 / ATR / 成交额变化） |
| `signal.py` | 155 | 趋势 Watchlist（RPS 排名 + BUY_CANDIDATE / STRONG_WATCH / WATCH） |
| `account.py` | 110 | 国金账户三态映射（VERIFIED_TRADABLE / UNVERIFIED / VERIFIED_UNTRADABLE） |
| `card.py` | 280 | ETF 候选卡片 + 验收门控（指标完整性 / 极端值 / 拆分检测） |
| `heat.py` | 215 | Layer 1 全市场热度 + 风险偏好 |
| `universe.py` | 160 | 数据质量门控（U0→U1→U2） |
| `sw_enrichment.py` | 165 | 行业 ETF SW-RPS 增强（预留） |
| `trend_animal_validation.py` | 120 | 趋势动物 Pro 验证（预留） |
| `portfolio.py` | 105 | 持仓管理（P1） |
| `order_plan.py` | 140 | 订单计划（P1） |
| `report.py` | 125 | 日报生成 |
| `cli.py` | 1030 | 全流程编排（12 个子命令） |

### 配置文件

| 文件 | 用途 |
|------|------|
| `config/guojin_tradable_whitelist.csv` | 国金账户白名单（116 只，三态） |
| `config/etf_universe.yaml` | 质量门控参数 |
| `config/etf_buckets.yaml` | 资产桶定义 |
| `config/etf_signal_rules.yaml` | 信号规则 |

### 后续阶段

| 阶段 | 交付 | 状态 |
|------|------|------|
| P0-D | 趋势动物 Pro 验证 | 预留接口 |
| P0-F | 回测与影子运行 | 待实现 |
| P1 | 条件单接入 | 待定 |
| P2 | QMT/PTrade 自动执行 | 待定 |

### 已知限制

| 缺口 | 影响 | 方向 |
|------|------|------|
| 45 只 588xxx 科创板 ETF 缺少历史行情 | coverage 85% | 寻找第三历史源 |
| ETF 分类、跟踪指数、exposure 未补齐 | 卡片基础字段为空 | 接入 fund_etf_info_sina |
| 无历史回测 | 信号有效性未量化 | P0-F |
