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
├── src/
│   ├── main.py                   # 纯路由
│   ├── common/                   # 公共层
│   │   ├── paths.py
│   │   ├── run_context.py
│   │   └── manifest.py
│   ├── stock_trend/
│   └── sw_industry_rps/
└── tests/
```

### 测试总览

| 模块         | 测试数  |
| ------------ | ------- |
| 个股趋势监控 | 143     |
| 申万行业 RPS | 58      |
| **总计**     | **201** |

---

## 个股技术趋势监控

### AKSignal v0.3 ✅

AKShare 拉取 → raw 缓存 → 指标计算 → 趋势评分 → 相对强度(沪深300) → watch_level / action / portfolio_summary / watchlist → HTML/CSV 报告

监控标的：寒武纪、中际旭创、科大讯飞；理想汽车(US/HK)、蔚来、小鹏汽车(US/HK)；上汽集团；宁德时代、德赛西威、韦尔股份、Mobileye、速腾聚创、地平线；中证500ETF、黄金ETF

### CLI

```bash
python src/main.py                          # 默认（向后兼容）
python src/main.py stock [options]           # 显式
python src/main.py stock --offline           # 仅缓存
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
python src/main.py industry drilldown             # 分析最新交易日的新进入行业
python src/main.py industry drilldown --window 5  # 使用 5 日窗口
python src/main.py industry drilldown --limit 3   # 最多分析 3 个行业
python src/main.py industry drilldown --output-csv # 输出 CSV 到 outputs/sw_industry_rps/
```

#### 数据源策略

| 窗口 | 主力源 | 回退策略 | 典型覆盖率 |
|------|--------|---------|-----------|
| 5 日（标准） | legulegu.com 返回的近 5 日涨幅 | 东方财富 → 新浪 → 腾讯 | 100% |
| 其他窗口或缺失 | 东方财富 → 新浪 → 腾讯多源轮询 | 指数退避 5 次重试 | 取决于频率限制 |

数据源模块 `src/common/market_data.py` 从 `stock_trend.data_provider` 抽取，与个股模块共享相同的节流、退避和外层重试逻辑。

#### 已知限制

1. **成分股快照时滞**：legulegu.com 返回的是最新成分股，不是突破时刻的历史成分股。权重会因调仓、IPO、增发等事件偏离突破时的真实权重。
2. **市值口径误差**：使用总市值而非自由流通市值，大股东持股比例高的个股权重会高估。
3. **非标准窗口行情受限**：10 日等 legulegu 不直接提供的窗口需依赖多源回退，极端情况下权重股仍可能获取失败。
4. **分类阈值未经验证**：`top1_contrib_share >= 50%`（单核主导）、`top3_share >= 60%`（集中领涨）等边界值为初步设定，需 20~30 个行业样本校准。

#### 0716 日运行示例（市值代理结果）

| 行业 | 窗口 | actual | proxy | gap | 覆盖 | 质量 | 贡献结构 | 广度结构 | 前三大贡献股 |
|------|--------|-------|-----|------|------|---------|---------|------------|
| 乘用车 | 5d | 6.02% | 6.66% | +0.64pp | 100% | 良好 | 单核主导 | 广泛上涨 | 比亚迪 5.23%, 长城汽车 0.79%, 上汽集团 0.54% |
| 影视院线 | 5d | 4.66% | 4.87% | +0.21pp | 100% | 良好 | 多龙头带动 | 广泛上涨 | 光线传媒 2.29%, 幸福蓝海 0.91%, 中国电影 -0.87% |

### CLI

```bash
python src/main.py industry <command>              # 显式
python src/main.py run-day                         # 向后兼容
python src/main.py industry drilldown             # 成分股穿透分析
python main.py bootstrap|update|...                # 向后兼容
```
