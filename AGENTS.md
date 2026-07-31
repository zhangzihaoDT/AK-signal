# AKsignal — Agent 操作手册

## 每日运行（run-day）

- **唯一入口**：`make run-day`（etf-update → sw-rps-update → etf-calculate → sw-rps-calculate → etf-pipeline → sw-rps-report）
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

## 常用命令

```bash
make run-day          # 每日全流程
make etf-pipeline     # 仅 ETF 发现链路
make sw-rps-run-day   # 仅 SW-RPS 全流程
make test             # 全部测试
```

## 产物

- ETF：`outputs/etf_signal/funnel_report_{date}.html` + `candidate_cards_{date}.json`
- SW-RPS：`outputs/sw_industry_rps/sw_industry_rps_{date}.html`（+ `_latest.html` 指向最新）
