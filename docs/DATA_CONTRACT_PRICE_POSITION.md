# P0 数据契约 · 价格窗口位置（price_pos）

> 状态：FROZEN（2026-09-14 起）
> 唯一真源：`src/research/etf_bottom/position_contract.py`
> 触发背景：2026-09-03 全市场 1598/1609 只 ETF 缺 bar，`50.0` 兜底把「数据洞」
> 伪装成「位置=50%」的合法观测，毒化 Lane 2 全历史重算与 Lane 3 状态机数周。

---

## 1. 契约（必须遵守的不变量）

1. **只有完整、无洞、无折算、且有波动的窗口，才有位置值。**
2. **未知一律为 `None`（NaN），绝不落地为任何数值——尤其禁止 `50.0`。**
3. **缺失的「原因」决定语义，而不是缺行/缺值这种形式本身决定语义。**
4. `price_pos_N` 一旦无法定义，其派生的 `bottom_state` / `long_term_bottom`
   **不得降格为「有效非底部」（NORMAL / False）**，必须显式为 `UNRELIABLE`。
5. Lane 2 的两条实现（`state_odds.daily_state_series` 全历史、`price_map.compute_row`
   横截面快照）必须走同一套规则；标量版与向量化版由 parity 测试锁定。

---

## 2. 原因码（reason taxonomy）

| reason | 触发条件 | price_pos | bottom_state | 语义 |
|---|---|---|---|---|
| `OK` | 窗口 N 个 bar 齐全、hi>lo、无折算 | 有值 | 正常判定 | 有效观测 |
| `INSUFFICIENT_HISTORY` | 该标的自身 bar 数 < N | `None` | `UNRELIABLE` | **正常生命周期**（新上市/历史短），非错误 |
| `DATA_GAP` | 窗口内含「应存在但缺失」的 close | `None` | `UNRELIABLE` | **数据管线事故**，必须告警/修数 |
| `CORPORATE_ACTION` | 窗口内含疑似份额折算（\|单日收益\| ≥ 20%） | `None` | `UNRELIABLE` | 价格跳变不可用于分位 |
| `FLAT_WINDOW` | 窗口无波动（hi == lo） | `None` | 正常判定（非底部） | 无从定义分位 |
| `FLAT_PRICE_ASSET` | 货币/债券类近零波动资产 | `None` | `NORMAL` | 分位无意义（沿用既有口径） |

关键区分：**`DATA_GAP`（事故）与 `INSUFFICIENT_HISTORY`（生命周期）必须分开**——
两者都不产出位置值，但前者要触发数据质量流程，后者不需要。

---

## 3. 传播路径（修复前 vs 修复后）

修复前的错误传播：

```
缺失 bar ──► rolling 窗口失效 ──► 50.0 静默兜底 ──► 「有效位置=50」
   └─► long_term_bottom=False ──► confirmed_ltb=False ──► 伪造 first_exit / 状态机失明
```

修复后：

```
缺失 bar ──► reason=DATA_GAP ──► price_pos=None ──► bottom_state=UNRELIABLE
   └─► long_term_bottom=False（但明确标 UNRELIABLE，非有效非底部）
   └─► 状态机在不可靠日不检测事件、跨段不翻转（既有 reliable_360 门控）
```

---

## 4. 适用范围与边界

- 覆盖：`price_map.compute_row`、`state_odds.daily_state_series`（二者共同底座）。
- 产物新增列：`low_N` / `high_N` / `distance_to_low_N` / `window_reason_N`；
  `price_map` JSON 新增 `window_reason_counts`（审计 DATA_GAP 规模）。
- **不在本契约范围**（后续独立处理）：
  - 状态机对 `long_term_bottom` 的三态传播（`build_persistence_series` 仍读 bool；
    `reliable_360` 门控已能阻止跨不可靠段的伪翻转）；
  - raw 写入边界的「不得落 NaN bar」守卫；
  - 源数据补数（09-03 等缺失 bar 的重拉与重算）。

## 5. 直接后果（重要）

在源数据（09-03 等缺失 bar）修复之前，**含洞窗口会被一致地标为 `UNRELIABLE`**：
Lane 2 全历史与 Lane 3 状态机在 60/120/360 窗口各自恢复前，会对受影响标的输出
`UNRELIABLE`。这是契约的预期行为——**宁可显式未知，也不静默造假**。修数后覆盖自动恢复。
