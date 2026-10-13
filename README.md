# AKsignal — Market Intelligence & Portfolio Strategy System

当前基线：**v0.5.0**（2026-10-13）。版本源为 `VERSION`，变更记录见 [CHANGELOG.md](CHANGELOG.md)。

通过量化方法辅助管理一个真实的投资组合，而不是只生成市场观察报告。

整体架构为 **Facts + Strategy**：Facts 包含 Market Facts（市场事实）与 Portfolio State（实际持仓、现金、风险暴露）；Strategy 包含 Selection、Entry、Allocation、Exit、Portfolio Advice。

Allocation 给出单项资产的目标仓位建议；Portfolio Advice 结合真实组合与现金、主题集中度等约束，给出综合调整建议。实际仓位、目标仓位与本次调整量分别记录。

**当前实现阶段**是 Market Facts：观察全市场 ETF、全行业及固定池个股的趋势与底部位置，daily-run 尚不自动给出投资建议。Portfolio State 和五项 Strategy 能力是后续建设方向。

保留八项能力：固定池个股数据、全市场 ETF 数据、全行业指数数据、行业轮动、大类资产趋势、ETF 趋势活跃池、个股趋势、ETF 与个股底部位置。覆盖范围随配置和行情质量变化，固定池个股不等于 A 股全量。

**必须坚持：AKsignal 负责发现和量化市场事实，你负责理解这些事实并作出投资决策。**

## 当前 Market Facts 与日报

Data Foundation（数据基础）：固定池股票、全市场 ETF、全行业指数。
Trend（趋势观察）：行业轮动、大类资产、ETF 趋势、个股趋势。
Position（位置观察）：ETF 与固定池个股底部位置。
Daily Observation Report：统一日期、统一数据质量、统一发布。

日报正文只有市场方向、趋势活跃、底部位置三个观察区域。覆盖、复权审计、缓存及来源状态放在独立数据质量区域。

## 每日运行

```bash
make run-day
make run-day RUN_DATE=20261012
make run-day-offline RUN_DATE=20261012
make run-day-status
make run-day RUN_DATE=20261012 RUN_FLAGS=--resume
make run-day RUN_DATE=20261012 RUN_FLAGS=--restart
make run-day-check
make run-day-summary
```

主链：ETF / 行业更新 → ETF / 行业计算 → 观察池个股行情与趋势 → 趋势与位置观察 → 验收 → 统一发布。启动时固定最近完整交易日，失败从检查点恢复；代码或配置变化需 --restart。离线模式不联网补数。

当前产品不运行主题 Tier 确认、Selection 推荐与 Timing、结构穿透、Radar、三 Lane、迁移状态机或历史赔率。旧代码和显式历史研究入口不属于核心每日产品。

## 交付

- `outputs/observation/observation_{date}.{html,json,md}`：一个观察日报入口。
- `outputs/observation/{etf_trends,asset_directions,industry_rotation,industry_directions,etf_positions,stock_positions}_{date}.{parquet,csv}`：完整观察事实。
- `outputs/stock_metrics/stock_metrics_{date}.parquet`：个股趋势事实。
- `outputs/stock_metrics/stock_update_audit_{date}.json`：个股行情审计。
- `outputs/run_day/final_check_{date}.json`、`publication.json`：验收与发布回执，不含投资 action。

批次 outputs 从空目录建立，验收后一次切换发布指针。失败保持上一完整日报；历史批次仍保存在 `.run_day/runs`，首次旧目录保存在 `.run_day/legacy`，不随新批次重复复制。

底部只描述历史价格位置。个股口径独立实现；历史缓存已逐只核对当前参考，但全池口径尚未统一，price_basis_verified=false。缺历史与数据洞保持未知，不伪造离底。

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

能力与边界见 [canonical 状态图](docs/ARCHITECTURE_MAP.md)；位置口径见 [POSITION_OBSERVATION.md](docs/POSITION_OBSERVATION.md)。
