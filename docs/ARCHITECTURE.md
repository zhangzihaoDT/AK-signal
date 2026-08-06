# AKSignal 整体架构

> 版本：v0.6.1（Strategy Specification 统一）
> 日期：2026-08-05
>
> 策略层：所有可实验参数收敛到 config/（themes/universes/strategies/indicators/execution/portfolio），
> 由 src/common/spec 统一 Loader + Schema 校验；算法/执行语义留代码。详见 docs/STRATEGY_SPEC.md。

## Observation / Decision 分层（最值得坚持的一点）

```
Observation Layer            ── 制造事实，不做决策
    Layer① ETF Rotation      全市场观察到什么（相对全市场 ETF 横截面）
    Layer② Theme Confirmation 行业证据观察到什么（相对 124 申万行业横截面）
Decision Layer               ── 消费事实，做决策
    Layer③ Selection          基于已确认事实，决定买哪只 ETF / 哪类股票
```

**核心原则：Observation 不做决策；Decision 不制造事实。**

- **Layer①/② 只回答「是什么」**：全市场/行业层面的强弱是客观观察结果，它们**不做**「该买什么」的判断。RPS、确认状态都是事实产物。
- **Layer③ 只回答「怎么办」**：消费 Layer①/② 已确认、已对齐、已落盘的事实，**不新增任何市场观察**（不联网、不抓数据、不重算行情）。它只是把「事实」压缩成「行动建议」。
- **两个 RPS 是不同横截面的观察**：ETF RPS 相对全市场 ETF（Layer①），行业 RPS 相对 124 申万行业（Layer②）——都是 Observation 的事实，标尺不同，不可直接对比；Layer③ 消费它们时也不混用。
- **规则上不可越界**：Observation 层的任何改动（RPS 算法、确认阈值）只改变「事实」，必须通过 Parity 验证；Decision 层不应当产生事实，因此**禁止联网/重算**（v0.4.3 已固化）。

### Fact 与 Policy 边界（Fact 不可变）

Layer①/② 产出的每个数字都是**事实**，Layer③ 不得改写它们：

```text
Layer①（Fact）   软件ETF RPS = 93、电力ETF RPS = 91   → 只是事实，没说买
Layer②（Fact）   AI Theme = Confirmed、Quality = Confirmed → 只是事实，没说买
Layer③（Policy） 消费 Fact A + Fact B + 配置 + 策略规则
                  → 输出 BUY / 推荐 电力ETF             → 第一次出现「应该买什么」
```

**铁律：Layer③ 不修改 Fact，只做 Policy 决策。**

**禁止 Layer③ 覆盖、重算或冒充 Observation 原始字段。**

- 筛选/拒绝/打分是 **Policy**，不是对事实的修正。`电力ETF RPS=91` 永远是 91；Layer③ 只是决定「这个事实是否通过我的策略门槛」。
- **基于行业弱势拒绝 ETF 是合法 Policy**。例如 `if industry_rps < 60: reject etf` 合法——但它必须：
  1. **保留原始 ETF RPS**（91 不被改写、不置空、不重算）；
  2. **显式记录拒绝规则**（reason 写明「行业弱势 RPS<60 拒绝」，而非让报告看起来像 ETF 本身不强）。
- **真正的反模式**：把「策略拒绝」表现为「事实修正」——例如覆盖 rps15 为 None、用 Layer② 事实重算 Layer① 字段、或让报告暗示「Layer① 说的 91 其实不存在」。这会制造「Layer① 说 91、Layer③ 说其实没有 91」的语义混乱。
- **落地要求**：
  1. 产物必须**保留事实原值**（ETF 的 rps15 等），Policy 的接受/拒绝单独标注（recommended / reason），不与事实混淆。
  2. 若某个 Policy 确实要改变「事实」本身（例如某阈值影响 RPS 计算），必须把它上移为 Observation 层的规则，并过 Parity；不得在 Layer③ 就地改写。
  3. Layer③ 的阈值（趋势门、流动性、rps_min、行业弱势拒绝）是**策略参数**（config/strategies.yaml），不是对 Observation 口径的修正。
  4. 不同策略可对同一事实给出不同 Policy（多策略兼容）：A 策略拒绝、B 策略接受，均不改变共享事实——「Fact 不可变」与「支持多策略」由此兼容。

## 架构

```
                    ┌──────────────────────────────┐
                    │  美股 AI/半导体局部观察池      │
                    │  SOXX / NVDA / ASML / ...     │
                    │  (AKShare stock_us_hist)      │
                    └──────────┬───────────────────┘
                               │ 并行接入，辅助最终判断
                               ▼
 ════════════ Observation Layer（制造事实，不做决策）════════════
┌──────────────────────────────────────────────────────────┐
│ ① A股全市场 ETF 轮动（etf_signal）                        │
│   Discover：全量 ETF → 横截面 RPS15/20/60 → 每主题焦点组   │
│   产出：rotation_{date}.parquet / etf_rotation_{date}.html│
└────────────────────────┬─────────────────────────────────┘
                         │ 指向强势主题（观察结论）
                         ▼
┌──────────────────────────────────────────────────────────┐
│ ② 主题确认（Theme Confirmation，sw_industry_rps confirm）│
│   Confirm：多主题焦点行业群 → 群共振/bucket 聚合/龙头广度   │
│   产出：confirmation_{date}.parquet（含 bucket/theme）    │
└────────────────────────┬─────────────────────────────────┘
                         │ 验证趋势质量（观察结论）
                         ▼
 ════════════ Decision Layer（消费事实，做决策）═════════════
┌──────────────────────────────────────────────────────────┐
│ ③ 多主题交易标的筛选（selection）                          │
│   Select：bucket → theme 逐主题表达决策（ETF vs 个股）      │
│   只消费 Layer①/② 事实，禁止联网/重算                      │
└────────────────────────┬─────────────────────────────────┘
                         │
                         ▼
              ┌──────────────────────────────┐
              │  Candidate JSON（决策产物）   │
              │  tradable_candidates_{date}   │
              └──────────────┬───────────────┘
                             │
                             ▼
              ┌──────────────────────────────┐
              │  ④ Portfolio（执行，未来）     │
              │  买多少 / 何时买 / 何时卖       │
              └──────────────────────────────┘

   Trend Engine（trend_engine，Selection 的依赖，非主链）
        ┌──────────────────────────────┐
        │  行情获取 / 缓存 / 多源回退    │
        │  技术指标 / 0-100 趋势评分     │
        │  ↑ 仅被 Selection 调用，无独立入口 │
        └──────────────────────────────┘
```

**信号主链**：Layer ① ETF 发现 → Layer ② 主题确认（Theme Confirmation）→ Layer ③ 交易候选（Candidate JSON）→ Layer ④ 执行

**多主题框架（v0.4.3，两方向）**：单一「AI/科技/半导体」方向升级为「Bucket（为什么持有）→ Theme（持有什么）→ 候选资产」的多主题结构。主题与行业/关键词定义在 `config/themes_two_directions.yaml`（单一事实源），Layer ①②③ 共同消费：

| Bucket | 目的 | Theme | 申万二级焦点行业 |
|--------|------|-------|------------------|
| Core（核心） | 获取长期收益 | AI 基础设施（注意不是 AI 应用） | 半导体 / 元件 / 通信设备 / 计算机设备 / 光学光电子 / 自动化设备 / 消费电子 |
| Quality（质量） | 高现金流防守 | 高现金流资产（电力/运营商/公用事业） | 电力 / 通信服务 / 铁路公路 / 航运港口 / 燃气 |

> **Future Themes（Not Enabled）**：以下方向不属当前两方向，但仅是「**未启用**」而非被否定，后续可在 `themes_two_directions.yaml` 重新打开：
> - **Resource Cycle**（有色 / 钢铁 / 煤炭等周期资源）
> - **High-end Equipment**（高端装备）
> - **Aerospace / Shipping**（航空航天 / 船舶）
>
> 若注册商品类 theme（如铜/铝），表达的是「**商品权益及 ETF 代理**」——ETF + 申万有色相关行业 + 资源股/商品 ETF，反映的是「股票市场如何交易商品」，**不是商品期货趋势系统**（无期货价格/库存/升贴水数据链）。配置上此类主题应标记 `maturity: PARTIAL`，不得据此输出增配信号；完整 Commodity Adapter 属于 v0.5。

**Trend Engine**：不是主链一环，而是 Selection 内部调用的依赖（原 stock_trend 底层能力重组），不暴露独立业务入口。

**并行接入**：美股 AI/半导体局部观察池，不参与主链驱动，只辅助最终判断

---

## 三层纵向递进

### ① Observation Layer · A股全市场 ETF 轮动

**核心问题**：每个配置主题（AI 基础设施 / 高现金流资产）在全部 A 股 ETF 资产中处于什么位置？

**职责**：从全量 ETF 中发现当前主线，不预设某个主题一定是主线。全市场横截面 RPS 对全部主题通用，每主题按 `themes_two_directions.yaml` 关键词聚合焦点组。**这是 Observation：产出「市场观察到什么」的事实，不做买入决策。**

**重点观察**：

| 维度 | 指标 |
|------|------|
| 横截面强度 | RPS15 / RPS20 / RPS60 |
| 收益排名 | 5日 / 10日 / 20日收益在全部 ETF 中的排名 |
| 趋势状态 | BUY_CANDIDATE / STRONG_WATCH / WATCH 分布 |
| 排名变化 | 板块 RPS15 中位数过去 5 日排名变动 |
| 强势数量 | 进入 RPS15 Top 10% / Top 20% 的 ETF 数量 |
| 内部扩散 | 板块内部 RPS 离散度，强势标的占比变化 |
| 主题焦点组 | 每主题 ETF 数 / 中位 RPS15 / Top10%/20% / 5 日排名变动 |

**这一层决定**：哪些主题正在成为 A 股主线。

### ② Observation Layer · 主题确认（Theme Confirmation）

**核心问题**：每个主题是否被底层行业证据支持？

**职责**：确认目标是 **Theme**，不是行业。按 `themes_two_directions.yaml` 加载每主题的申万二级行业焦点组作为**确认因子**（SW 行业 / ETF / 参与率 / HHI 都是因子，不是目标本身），从中观行业层面验证趋势质量，并按 Bucket 聚合（Core / Quality 两个组合意图当前哪个被行业证据确认支撑）。**这是 Observation：产出「行业证据是否支持主题」的事实，不做买入决策。**

**识别维度**：

| 维度 | 判断 |
|------|------|
| 单一 vs 群共振 | 单个行业强势，还是主题行业群同步走强 |
| 龙头 vs 广泛上涨 | 少数龙头贡献（高 HHI），还是广泛参与（高参与率） |
| ETF vs 行业背离 | ETF 强但行业弱 → 可能为资金交易行为，非主题确认（按主题独立判断） |
| Bucket 聚合 | 同一组合意图下的主题确认汇总 |

**直接复用的现有能力**：

- Top 3 贡献占比
- HHI（贡献集中度）
- 上涨参与率
- 双覆盖率（ETF × 行业）
- 4×4 驱动分类

**这一层决定**：主题层面的强势是真实趋势还是短期交易行为（按主题回答）。

### ③ Decision Layer · 多主题交易标的筛选与表达方式选择（selection）

**核心问题**：这个已确认主题，应当由哪只 ETF、哪类股票来交易？

**职责**：执行对象压缩层。把 Layer①/② 的结论压缩成「用 ETF 还是个股、用宽主题还是细分主题、选哪一只」，不回答买多少/何时买卖（Layer 4）。按 bucket → theme 逐主题输出候选对象。**这是 Decision：消费 Observation 层的事实（只读已落盘产物），不制造任何新事实（默认禁止联网/重算）。**

**输出结构**：

```
tradable_candidates_{date}.json
└── layer3
    ├── buckets[]
    │   ├── bucket / bucket_label / objective
    │   └── themes[]
    │       ├── theme / theme_label / signal_model / maturity
    │       ├── confirmed / confirmation_reason
    │       ├── expression（ETF vs 个股 决策）
    │       ├── core_etf / sub_industry_etf（动态从 Layer① 按主题关键词选）
    │       └── stock_watchlist / stock_candidates（universe 固定观察池）
    ├── recommended_actions / summary / action
```

**表达方式决策**（基于 Layer② 上涨结构，逐主题）：
- 广泛上涨（参与率高 + HHI 低）→ 优先 ETF
- 龙头主导（HHI/Top3 高）→ 优先龙头个股，ETF 作低风险替代
- 扩散形成 → ETF 核心 + 龙头卫星
- 行业未确认 → 仅 WATCHLIST

**分层资产池**：`config/stock_universe.yaml`（theme → tier → assets，bucket 归属由 `themes_two_directions.yaml` 推导，不重复维护），每主题压缩为：1 核心 ETF + 1–2 细分 ETF + 2–5 龙头 + 设备/上游观察。

**Trend Engine（内部依赖）**：原 stock_trend 底层能力（行情获取/缓存/多源/指标/0–100 评分）重组为 `trend_engine`，仅被 selection 调用，不暴露独立业务入口。

**Layer 4 边界**：本层只回答「买什么」。仓位/入场/止损/加仓/减仓/组合相关性属于未来 Layer 4 Portfolio（Execution）。

---

## 并行辅助：美股 AI/半导体局部观察池

**不参与主链驱动，只辅助最终判断**

三个固定 Basket：

| Basket | 标的 |
|--------|------|
| 半导体市场 | SOXX / SMH / SOXL / SOXS |
| AI 芯片 | NVDA / AMD / AVGO / TSM / ARM / MU |
| 半导体设备 | ASML / AMAT / LRCX / KLAC |

**数据源**：AKShare `stock_us_hist` / `stock_us_daily`

**原则**：
- 否决权，非驱动力
- 国内无信号时不因美股强而买入
- 国内信号强 + 美股弱 → 保持观察，不追高
- 国内信号强 + 美股强或无冲突 → 提高置信度

---

## 输出

| 状态 | 含义 |
|------|------|
| 买入 | 三层共振（主题 ETF 强 + 主题确认 + 标的技术有效） |
| 持有 | 趋势仍有效，不触发退出条件 |
| 卖出 | 趋势失效、主题确认转弱或风险触发 |

---

## 与四层决策框架的关系

```
长期投资方向（配置层）
    Core（长期增长）：AI 基础设施
    Quality（高现金流防守）：高现金流资产（电力 / 运营商 / 公用事业）

        │ 决定系统关注什么（config/themes_two_directions.yaml）

第一层：市场状态（Discovery）
    全市场 ETF 轮动，判断关注主题是否成为市场主线

第二层：主题确认（Theme Confirmation）
    验证关注主题是否得到行业证据共振支持（按 bucket 聚合）

第三层：交易候选（Selection）
    将确认后的主题压缩为可交易资产（ETF / 龙头 / 高弹性）

第四层：Portfolio（Execution）
    仓位、买卖时点、风险控制
```

**配置层不参与每日计算**：Bucket 与 Theme 是人工设定的长期研究方向（`themes_two_directions.yaml`），决定整个系统关注什么。Layer ①/②/③ 始终围绕这些主题运行，分别回答三个问题——**它是不是主线？是否得到确认？应该如何表达？**

---

## 当前状态 vs v0.4.3 目标

v0.4.3 是在现有 v0.4.2 上**增加多主题能力**的增量升级，不是另起新系统：三层信号链算法（ETF 横截面 RPS / 主题确认 / Selection 状态机）完全不变，变化的只是「单一 Investment Focus」→「Theme Registry」。

| 模块 | v0.4.2 当前状态（基线） | v0.4.3 目标（本次升级） |
|------|------------------------|------------------------|
| ① A股全市场 ETF 轮动 | ✅ 全市场横截面 RPS15/20/60 + rotation 报告（单一 AI 焦点组 `is_tech`） | 每主题独立焦点组（`themes_two_directions.yaml` 关键词），报告按 Bucket→Theme 展示 |
| ② 主题确认（Theme Confirmation） | ✅ 硬编码 10 个 AI 重点行业群共振 + 3 子主题 | `FOCUS_INDUSTRIES` → `themes_two_directions.yaml` 配置驱动（两方向 12 行业）+ bucket 聚合 + 每主题背离 |
| ③ 多主题交易标的筛选 | ✅ 单主题（ai_tech，3 子主题）候选 + 表达决策 | `buckets[].themes[]` 多主题候选；universe 保持 theme→tier→assets（bucket 由 themes_two_directions.yaml 推导） |
| Trend Engine | ✅ 原 stock_trend 底层能力重组（selection 内部依赖） | 复用，无变化 |
| ④ Portfolio（Execution） | ⬜ 未来 | 不在 v0.4.3 范围 |
| 美股 AI/半导体局部观察池 | ⬜ 待实现 | 不在 v0.4.3 范围 |
