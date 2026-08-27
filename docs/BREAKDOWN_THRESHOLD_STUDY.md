# BREAKDOWN Threshold Study（研究规格）

> 状态：**研究任务规格**（未实现，不搭脚本）。当前生产固定阈值 `breakdown_pct = -15.0`（v0.9.0，commit 567d1a9）。
> 目的：在引入 ATR / 波动率自适应阈值之前，先用固定阈值重放验证「是否存在资产类别异质性」，为是否复杂化提供证据。

## 1. 定位与背景

Layer③ position 段在 commit 567d1a9 改为 MA60 乖离率 + BREAKDOWN 破位拦截：

- `< breakdown_pct → BREAKDOWN`：中期趋势破坏，禁止买入（即使 LEADER/CORE）
- `[breakdown_pct, low_below_pct) → LOW`：深度回调，赔率区
- `[low_below_pct, high_above_pct] → MID`：正常趋势区
- `> high_above_pct → HIGH`：追高，HOLD

当前 `breakdown_pct = -15%` 是**可解释、可回测的固定基准版本**。但 ETF、港口股、半导体股的波动率差异极大，固定 −15% 很可能不是最优工程方案。本研究的任务是：

1. 扫描固定阈值 −10 / −12.5 / −15 / −17.5 / −20%，量化「被 BREAKDOWN 拦掉的 BUY 后续表现」；
2. 判断是否**存在资产类别异质性**（ETF vs 个股、高波动主题 vs 低波动主题）；
3. 只有异质性充分成立，才有理由进入 ATR / `deviation < -max(10%, k×volatility)` 自适应阈值。

**顺序纪律：不先复杂化。** −15% 保持为基准；自适应阈值是后续独立设计，不在本任务实现。

## 2. 决策原则：不是最大化平均前向收益

**本研究的优化目标偏向：**

$$
Loss = MAE + \lambda \times OpportunityCost
$$

而不是：

$$
\max ForwardReturn
$$

为什么不能只看平均收益——示例：

- 阈值放宽到 −20% 后，被放行的标的中可能有一批「深跌大反弹」，60D 平均收益反而很高；
- 但它的 **MAE = −18%**（买入后中间要扛一个月 −18% 浮亏）；
- 它依然**不适合作为 BUY timing**——入场后先深套，是 timing 质量问题，不是收益质量问题。

因此每个阈值档位的结论，**以 MAE / 站回 MID 天数为主判据**，前向收益只作辅助。

## 3. 事件与口径定义

### 3.1 事件 = 首次进入 BREAKDOWN（非重叠，且按阈值独立定义）

- **事件**：某资产在某 trade_date 因 `ma60_deviation < threshold` **首次进入 BREAKDOWN**（状态转换，非每日快照）。
- **非重叠**：同实体连续 BREAKDOWN 区间只取第一个进入日，避免同一样本重复计数（对齐现有 event-study 的 entry 事件语义，`events.py`）。
- **阈值独立事件集（严格 counterfactual 的关键）**：`-10%` 的首次 BREAKDOWN 与 `-20%` 的首次 BREAKDOWN 是**不同的事件**，进入日不共享。五档扫描时**对每个候选阈值独立生成**「该阈值下首次进入 BREAKDOWN 的非重叠事件集」。否则同一资产在窄阈值下早已进入 BREAKDOWN，在宽阈值下的「进入日」只是持续破位中的某一天，两档样本不可比，扫描就不是严格 counterfactual。
- 事件发生时该资产应处于「本可买入」的候选资格态（trend+theme+leadership 均过、仅 position 被压成 WAIT）——这是**被拦 BUY** 样本；若趋势/主题本身不过，不进入样本（那不是 BREAKDOWN 拦的，是别的 Gate 拦的）。

### 3.2 对照组（counterfactual）

- **被拦 BUY**：BREAKDOWN 事件日（上述定义）。
- **正常 BUY**：同资产（或同类实体）在 position ∈ {LOW, MID} 且信号 ∈ {STRONG_BUY, BUY} 的推荐日。
- 对照回答：同样是趋势/主题合格的候选，被 BREAKDOWN 拦掉的那批，后续 20D/60D 是否显著更差、MAE 是否显著更深。

### 3.3 前向收益与路径指标

- **起算**：事件日 T 的下一交易日 T+1 开盘（对齐 v0.5.2 `next_open` 执行口径），避免事件当日跳空失真。
- **Horizon**：20D / 60D。
- **基准（分层可交易 universe 中位，避免 benchmark distortion）**：事件日对应**主题 / 资产类别可交易 universe** 的 T+1→T+20 / T+60 中位收益。
  - 分层基准口径：ETF 事件 → 该事件主题（或全市场）ETF universe 中位；个股事件 → 该事件主题 universe 个股中位。
  - **不要笼统用全市场横截面中位**：AI / 半导体等**高 beta 主题**在前向窗口整体跑赢时，全市场中位基准会把「主题 beta」误判为「破位后的 alpha」，产生 benchmark distortion；分层基准把 beta 留在组内，excess 才反映破位后的相对强弱。
  - 具体分层粒度（主题级 vs 资产类别级）在实现时按样本量定，本规格只锁定「必须分层、不得用单一全市场中位」。
- **MAE / MFE**：事件窗口内的路径最大值（最大不利波动 / 最大浮盈），复用 `returns.py` 已有实现。
- **站回 MID 天数**：从 BREAKDOWN 进入日到 `dev ≥ low_below_pct`（−5%）的交易日数 = 资金被套多久。

### 3.4 数据与覆盖

- 价格簿全离线：ETF 全市场 close pivot（2020→）、universe 个股 close（2018+）、按 trade_date 截断。
- 个股 position 需要 ≥ `ma_window=60` 个交易日历史，不足者判 UNKNOWN（中性），**不计入 BREAKDOWN 样本**。
- 样本窗口：重放 `research replay range` 可覆盖的最长历史；输出 manifest 记录每日覆盖与 eligibility。

## 4. 阈值扫描

对 `breakdown_pct ∈ {−10, −12.5, −15, −17.5, −20}` 逐档重放 selection，输出每档：

- 被拦 BUY 数量（随阈值放宽的单调性 → 阈值是否过于激进）
- 20D / 60D 绝对前向收益
- 20D / 60D 基准超额（excess return）
- **MAE（核心主判据）**
- MFE（是否错杀大反弹）
- win rate（前向收益 > 0 比例，辅助）
- BREAKDOWN → 站回 MID 天数（资金被套多久）

## 5. 样本分层

| 分层维度 | 分组 |
|---|---|
| 资产类别 | ETF vs Stock |
| 主题波动率 | 高波动主题（半导体 / AI 基础设施）vs 低波动主题（高现金流 / 公用事业 / 港口） |

视样本量决定是否继续细分（如按 ETF 流动性、按股票市值）。分层是判定「异质性」的唯一依据。

## 6. 判断标准与读表顺序

1. **阈值激进度**：看「被拦 BUY 数量」曲线。若 −10% 已拦住绝大多数候选，说明门槛过低、过度干预。
2. **MAE 为主**：对每档看 MAE 分布（均值/分位）与站回 MID 天数。即使 −20% 档 60D 平均收益高，若 MAE 仍深（如 −18%）且需扛一个月，该档**不适合**。
3. **资产类别异质性**：若分层后出现
   - ETF 最优 ~−18%、个股最优 ~−12%、
   - 半导体（高波动）阈值宽、高现金流（低波动）阈值窄，
   才构成进入 ATR / 波动率自适应阈值的充分理由；否则维持单一定值。

## 7. 实施前置依赖（不建脚本，仅登记）

本规格落地脚本前需要先解决以下基础设施缺口。**实现顺序严格按 ①→④**，每步独立可做 parity test：

1. **`historical_signals` schema 增加位置态（存原始数值，不存标签）**：`SIGNAL_COLUMNS`（src/research/signals/schema.py）目前只落 `selection_status / recommended_action`，**没有 `position_level / position_pct`**。
   - **关键设计：`position_pct` 必须存原始 MA60 deviation 数值**（如 `-37.6`），而不是 `LOW/MID/BREAKDOWN` 标签。
   - 这样五档阈值扫描**不需要重复计算历史价格位置**，只需对同一列 deviation 重新 classify（`dev < threshold`），计算量与复杂度大幅下降，且天然满足 §3.1 的「按阈值独立生成事件集」。
   - 标签（`position_level`）仅作展示/审计，可同时落；阈值判定一律基于数值列。
2. **阈值参数化**：replay 通过 `load_stock_selection_spec`（lru_cache）读 config，逐档扫描需要支持覆盖 `breakdown_pct`（清 spec cache 或注入 override），不得改配置后手工逐次跑。
3. **事件定义扩展**：现有 `events.py` 事件层面向 Layer1/2/3 状态字段；需新增「Layer3 position 转换事件」（进入/离开 BREAKDOWN），并按 §3.1 的阈值独立 + 非重叠规则提取。
4. **被拦 BUY 判定**：需要「反事实」逻辑——重放时若把该实体 position 视为 LOW/MID（无视 BREAKDOWN），信号是否 ∈ {STRONG_BUY, BUY}；这决定样本是否进入「被拦 BUY」池。

以上为研究前置，不随本规格落地。

## 8. 后续（若异质性成立，另行设计）

- ATR 口径：`Close < MA60 − 3×ATR20`
- 波动率口径：`BREAKDOWN = deviation < −max(10%, k × volatility)`（k 待回测标定）
- 标尺仍为「下方破位」，与上方追高（HIGH）分开设计，不合并为对称区间。
