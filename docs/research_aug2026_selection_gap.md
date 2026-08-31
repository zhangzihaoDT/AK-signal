# 2026-08 Selection 未推荐 AI 个股 — 诊断与机会成本研究

> 研究区间：2026-07-31 → 2026-08-28（qfq）。特征锁死 7/31，无 look-ahead。
> 反事实方法：import 生产函数（`calc_action`/`_stock_state`），只操纵 `drawdown_from_high` 输入，零生产改动。
> 相关文档：`docs/research_aug2026_findings.md`（横截面研究）、`docs/research_aug2026_risk_gate_review.md`（B 讨论稿）。
> 产物：`outputs/research/aug2026/counterfactual_ablation_full.csv`、`ablation_summary.csv`、`ablation_curve.csv`、`ablation_returns.csv`。

---

## 版本结论（v1 封版，2026-08-31）

**机制确认**：DD hard gate 确实系统性阻断了 8 月 AI 个股表达（`eligible_stock_count=0` 整月）。

**收益验证**：解除 DD gate 并不能追回 8 月主要上涨——主要 Alpha 发生在趋势确认之前；解除后的 forward return 反而为负（Executable −2.58%），并伴随明显 MAE（释放组平均最大回撤 −12.1%，最差 −17.3%）。

**生产决策**：本轮 **KEEP**，不修改规则。

**架构结论**：不等于确认 static DD hard veto 长期最优。「历史路径风险能否随当前趋势修复而解除」仍是明确存在、需要跨月验证的问题。

**下一研究优先级**（两个独立问题，不混成一个）：
1. **Risk Recovery State** —— DD 是否应从「静态 hard veto」改为「趋势修复后解除」，需跨月（含趋势月）验证
2. **Trend Confirmation Lag** —— 8 月主要 Alpha 发生在趋势确认之前，selection 趋势门控对超跌反弹的滞后是否可改善

---

## 因果链（完整）

```
Observed failure
  8 月 AI 主题 confirmed=True，但 eligible_stock_count=0，整个 8 月无一只 AI 个股被推荐
        ↓
Mechanism
  drawdown_from_high ≥ 15%  →  calc_action=风险警戒  →  risk_gate_passed=False
        ↓
  趋势 / 领导力 / position / signal 四段全部无法执行
        ↓
Counterfactual ablation（CF∞）
  13 只股票被释放为 RECOMMENDED（11 AI + 2 汽车）
  11/13 趋势确认当天即应被推荐（days_trend_to_recommend=0）
        ↓
Executable return
  CF 首次推荐日建仓 → 8/28：−2.58%（等权）
  对照组：AI ETF 真实推荐 −1.09% / HS300 +0.46% / 全市场等权 +8.7%
        ↓
Risk trade-off
  释放组 8 月最大回撤均值 −12.1%（最差 −17.3%）
  释放组收盘 11/13 上涨，Hindsight ceiling +31%（事后不可赚）
        ↓
Policy conclusion
  KEEP 优先；架构层面存在两个位置指标语义冲突，若未来改动优先候选 2/4（见 B 稿）
```

## 核心数字表

| 项 | 数值 |
|---|---|
| 8 月 AI 个股 production 候选 | **0**（全月） |
| 移除 DD 规则释放股票 | 13（11 AI + 2 汽车） |
| 释放组 Hindsight ceiling（7/31→8/28） | +31.0%（**事后上限，不可赚**） |
| **释放组 Executable（首次推荐日→8/28）** | **−2.58%** |
| AI ETF 真实推荐 Executable | −1.09% |
| HS300 / 全市场等权 8 月 | +0.46% / +8.7% |
| 释放组 8 月最大回撤（MAE） | 均值 −12.1%，最差 −17.3% |
| 阈值扫描 20/25/30/∞ 释放数 | 4 / 9 / 12 / 13 |

## 关键结论

1. **failure 真实且机制清晰**：DD 风险规则使 AI 个股表达整月失效，主题只靠 ETF 表达。
2. **但机会成本有限（诚实评估）**：即使移除规则，8 月趋势确认后建仓的可执行收益为 −2.6%，不优于生产 ETF 推荐（−1.1%）。8 月暴涨（8/1-8/16）发生在趋势确认之前，selection 趋势门控天然滞后于超跌反弹启动。
3. **规则有真实保护价值**：释放组平均最大回撤 −12.1%，DD 规则挡住了「追深跌反弹被套」的尾部风险。
4. **架构冲突存在**：`drawdown_from_high`（120d 回撤，上游风险门）与 `ma60_deviation`（60d 乖离，下游 position 门）对同一时点给出相反结论（8/17 六只释放股：dd 19-32% 禁买 vs ma60dev +0.4~7% 中性可买）。
5. **单月 OOS 不足以改规则**：8 月是超跌反弹月，结论不适用于趋势月。若要改，需跨多月回测 + Parity。

## 建议

- 本轮 **KEEP**（不改生产）。
- 若未来要动，优先候选 2（DD 规则加方向性：已修复 MA60 上方则降级为记录）或候选 4（RECOVERING 恢复确认状态）。两者都需过 Parity + 多个月份验证。
