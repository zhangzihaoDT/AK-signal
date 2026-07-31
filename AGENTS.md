# AKsignal — Agent 操作手册

## 每日运行（run-day）

- **唯一入口**：`make run-day`（etf-update → sw-rps-update → etf-calculate → sw-rps-calculate → etf-pipeline → sw-rps-report → sw-rps-confirm）
- **数据发布时点（实测 2026-07-31）**：
  - ETF 行情（东财 spot）：盘中即可用，当日 07:30 前已覆盖前一交易日
  - SW-RPS 行业行情（swsresearch/legulegu）：**T+1 上午约 10:00 前后发布**。09:25 探测仍为空，10:08 已确认
- **结论**：`make run-day` 应在上午 10:00 之后运行。10:00 前运行会拿不到前一日 SW 行业数据，报告停在 T-1
- 若 10:00 前跑了 `run-day`，10:00 后重跑一次 `make run-day` 即可补齐，系统支持免重复发布

## 数据源新鲜度门控（SW-RPS）

- L1 `index_analysis_daily_sw`：单次批量接口，优先使用；目标日期无数据时返回空（KeyError '发布日期'）
- L2 `index_realtime_sw`：盘中会跳过（realtime 不能代理收盘价）
- L3 `index_hist_sw`：逐行业回退路径，极慢（约 5 分钟/124 行业）且凌晨/早上拿不到 T+1 数据，尽量避免触发
- 探测机制已内置在 `industry update` 中，正常情况无需手动干预

## 国金账户可交易池（黑名单机制）

- **默认全部可交易**，仅在实际交易中发现无法交易后加入黑名单（`config/guojin_tradable_blacklist.csv`）
- 原白名单（116 只人工验证）已归档至 `config/guojin_tradable_verified_backup.csv`，仅作历史参考，不参与判定
- 实战维护命令：
  ```bash
  python src/main.py etf account-blacklist add --code 588000 --reason "科创板权限未开通"
  python src/main.py etf account-blacklist remove --code 588000
  python src/main.py etf account-blacklist list
  ```

## Layer 3 交易标的筛选（selection）

- **定位**：执行对象压缩层——把 Layer①/② 结论压缩成「这个已确认方向用哪只 ETF、哪类股票交易」；不回答买多少/何时买卖（Layer 4）
- **核心输出是结构化候选对象**（JSON），HTML 只是可视化
- 单主题（AI/科技/半导体）+ 内部保留 3 子主题（ai_core / TMT / 智能制造）
- **表达方式决策**基于 Layer② 上涨结构：广泛上涨→ETF、龙头主导→龙头个股、扩散→ETF核心+龙头卫星、未确认→仅观察
- ETF 候选动态从 Layer① rotation 全市场按子主题关键词选（趋势门控 + 流动性 + 评分 + 去重）
- 个股趋势由 **Trend Engine** 现场计算（`trend_engine`，原 stock_trend 底层能力重组，无独立业务入口）
- 分层资产池：`config/stock_universe.yaml`（theme → tier → assets），已废弃扁平 `stock_pool.csv`
- 命令：
  ```bash
  make select          # 构建交易候选
  make select-offline  # 仅缓存
  python src/main.py select universe   # 查看分层池
  ```

## 常用命令

```bash
make run-day          # 每日全流程
make etf-pipeline     # 仅 ETF 发现链路
make sw-rps-run-day   # SW-RPS 全流程：update(含probe)→calculate→report→confirm
make select           # Layer 3 交易候选（调用 trend_engine）
make test             # 全部测试
```

## 产物

- ETF：`outputs/etf_signal/etf_rotation_{date}.html`（Layer ① A股全市场 ETF 轮动，替换原 funnel_report）+ `candidate_cards_{date}.json`
- ETF 轮动数据：`data/etf_signal/daily/rotation_{date}.parquet`（全市场横截面 RPS15/20/60 + 5日排名变动）
- SW-RPS：`outputs/sw_industry_rps/sw_industry_rps_{date}.html`（+ `_latest.html` 指向最新）
- Layer ② 行业确认：`outputs/sw_industry_rps/sw_industry_confirmation_{date}.html` + `data/processed/sw_industry/confirmation_{date}.parquet`（AI/科技/半导体 10 个重点行业群共振/龙头广度/背离）
- Layer 3 交易候选：`outputs/selection/tradable_candidates_{date}.json`（结构化候选对象）+ `.html`（可视化）
