# A 股技术趋势监控（极简版）

## 项目状态

- v0.1 数据链路跑通 ✅
- 状态总结：AKShare 拉取 → raw 缓存 → 指标计算 → 趋势评分 → HTML/CSV 报告
- v0.2 相对强度与状态变化 ✅
- 状态总结：benchmark(沪深300) → relative_strength_20d → change → reason 增强 → HTML/CSV 报告
- AKSignal v0.3 ✅
- 状态总结：信号输出 → 趋势观察与行动系统（watch_level / action / portfolio_summary / watchlist）

第一版仅监控：

- 寒武纪（688256）
- 中际旭创（300308）
- 科大讯飞（002230）
- 长鑫存储（TBD，待确认代码或替代标的）

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 运行

```bash
python src/main.py
```

或：

```bash
.venv/bin/python src/main.py
```

可选参数示例：

```bash
python src/main.py --start-date 20200101 --adjust qfq --plot-last-n 240
```

## 输出

- 原始数据：`data/raw/{symbol}.csv`
- 指标数据：`data/processed/{symbol}.csv`
- 报告：`data/reports/trend_report_YYYYMMDD.html` 与 `trend_report_YYYYMMDD.csv`

---

## 申万二级行业 RPS 监控

### 功能定位

基于申万二级行业指数的日频相对强度与轮动观察。计算各行业 5/10/15 日收益率在全部二级行业中的横截面百分位排名（RPS），识别进入强势区、持续强势、加速和掉队的行业。

### 为什么作为 AKsignal 的行业模块

AKsignal 已具备 AKShare 数据源、CSV 缓存分层、指标计算和 HTML 报告生成能力。行业 RPS 模块复用同一 Python 环境、数据存储约定和报告风格，同时保持与个股份析独立的工程边界。

### 数据来源

- 行业列表：`sw_index_second_info()` — legulegu.com（乐咕乐股）
- 历史行情：`index_hist_sw()` — swsresearch.com（申万宏源研究）
- 数据口径：申万二级行业（约 131 个行业）

### RPS 计算方法

```
return_N  = close_t / close_{t-N} - 1        (N=5,10,15, 交易日窗口)
RPS_N     = 横截面百分位排名(return_N)        (0-100, 越高越好, 并列平均排名)
delta_rps15 = 当日 RPS15 - 上日 RPS15
```

状态规则（阈值可在 `config/sw_industry_rps.yaml` 中调整）：

| 状态 | 条件 |
|------|------|
| new_entry | RPS15 ≥ 90 且上日 < 90 |
| strong_streak | RPS15 ≥ 90 且连续 ≥ 3 天 |
| accelerating | RPS15 ≥ 80 且 ΔRPS15 ≥ 10 |
| falling_out | RPS15 < 90 且上日 ≥ 90 |

### 安装依赖

```bash
pip install -r requirements.txt  # 已包含 pyyaml
```

### 命令

```bash
# 全部按顺序执行
python -m src.sw_industry_rps.cli run-day

# 分步执行
python -m src.sw_industry_rps.cli bootstrap        # 初始化行业列表 + 历史数据
python -m src.sw_industry_rps.cli update            # 增量更新行情
python -m src.sw_industry_rps.cli calculate         # 离线计算指标（不联网）
python -m src.sw_industry_rps.cli report            # 生成报告（不联网）
python -m src.sw_industry_rps.cli validate          # 数据质量检查
```

也支持从 `src/main.py` 自动路由：

```bash
python src/main.py run-day
```

### 报告位置

```
data/reports/sw_industry_rps/
├── sw_industry_rps_YYYYMMDD.html     # 每日报告
├── sw_industry_rps_YYYYMMDD.csv      # 每日数据
└── sw_industry_rps_latest.html       # 最新可用报告（仅数据质量 usable 时更新）
```

### 已知限制

1. **swsresearch.com API 当前不可用**（HTTP 508）—— `index_hist_sw()` 是 AKShare 中获取申万行业历史行情的唯一原生接口，在接口恢复前无法获取真实行情数据
2. 首版使用 CSV 存储，未引入数据库
3. 未实现行业成分股联动分析（预留接口）
4. 行业详情走势图属于 MVP 次优先级

### 与现有个股趋势监控的关系

- 互不干扰的独立模块
- 共用 `.venv` Python 环境和 `data/` 目录规划
- 共用 `config/` 配置目录
- 未来可在 "强势行业 → 强势个股" 方向联动
