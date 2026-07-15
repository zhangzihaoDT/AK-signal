# 申万二级行业 RPS 监控 — 项目设计文档

## 1. 现有工程审计结论

### 1.1 项目结构

```
AKsignal/
├── config/stock_pool.csv          # 个股池配置
├── data/
│   ├── raw/                       # 原始 OHLCV 缓存（每标的一 CSV）
│   ├── processed/                 # 含指标的处理后数据
│   └── reports/                   # HTML/CSV 报告 + portfolio JSON
├── src/
│   ├── main.py                    # CLI 入口 & 主流程
│   ├── asset.py                   # Asset dataclass
│   ├── data_provider.py           # AKShareProvider（个股行情）
│   ├── fetch_data.py              # FetchConfig + hs300 / 个股工具
│   ├── indicators.py              # 技术指标（MA/RSI/MACD/return_20d）
│   ├── scoring.py                 # 趋势评分 & 风险标志
│   ├── report.py                  # HTML/CSV 报告生成
│   ├── portfolio.py               # 组合汇总
│   └── watchlist.py               # 关注清单管理
├── .gitignore                     # data/ 已排除
├── requirements.txt               # pandas, akshare, plotly
```

### 1.2 可直接复用的现有模块

| 模块 | 复用方式 |
|------|----------|
| `.venv` + `requirements.txt` | 直接复用 Python 环境和依赖 |
| `data_provider.py` - `normalize_ohlcv` | 可复用列标准化逻辑 |
| `fetch_data.py` - `FetchConfig` | 可复用日期配置模式 |
| `indicators.py` - 收益率计算（`pct_change`）| 思想复用，但行业模块独立实现 RPS 指标 |
| `report.py` - HTML 生成风格 | CSS 框架和表格渲染风格可参照 |
| `main.py` - `build_logger` / `project_root` | 可直接复用 |
| `data/` 分层：raw → processed → reports | 沿用统一分层 |
| `.gitignore` 已排除 `data/` | 无需修改 |

### 1.3 不应复用或需解耦的部分

| 模块 | 原因 |
|------|------|
| `src/indicators.py` - `add_indicators` | 针对个股 MA/RSI/MACD，与行业 RPS 目标不同 |
| `src/scoring.py` - `score_latest_row` | 个股趋势评分，行业模块使用 RPS 状态体系 |
| `src/data_provider.py` - `AKShareProvider` | 针对个股行情，行业数据使用 SW 专用接口 |
| `config/stock_pool.csv` | 不修改，行业模块使用独立配置 |
| `src/main.py` 默认运行行为 | 不破坏，行业模块通过独立 CLI 路由 |

### 1.4 风险

- 不修改任何现有 `.py` 文件的核心逻辑
- `src/main.py` 仅增加路由入口，不改变默认行为
- 不和现有个股数据目录冲突（raw/processed/reports 通过子目录分离）

---

## 2. 数据源验证结果

| 函数 | 数据源 | 状态 | 说明 |
|------|--------|------|------|
| `sw_index_second_info()` | legulegu.com | ✅ 可用 | 131 个二级行业，含代码/名称/上级行业/成分个数/估值 |
| `sw_index_first_info()` | legulegu.com | ✅ 可用 | 31 个一级行业 |
| `index_hist_sw()` | swsresearch.com | ❌ HTTP 508 | 历史行情 API 当前不可用 |
| `index_analysis_daily_sw()` | swsresearch.com | ❌ HTTP 508 | 同上域名 |
| `index_component_sw()` | swsresearch.com | ❌ HTTP 508 | 同上域名 |
| `stock_zh_index_daily_em()` | East Money | ❌ 不支持 SW | 仅 CSI/沪深/BJ 指数 |
| `stock_zh_index_daily_tx()` | Tencent | ❌ 不支持 SW | 不支持申万指数代码 |
| `stock_board_industry_hist_em()` | East Money | ⚠️ 非 SW 口径 | EM 自有行业分类，网络不稳定 |

**关键风险**：`index_hist_sw()` 是获取申万行业指数历史行情的唯一原生 AKShare 接口，当前返回 HTTP 508。需要在 `data_source.py` 中实现**重试 + 降级**逻辑。

---

## 3. 推荐目录结构

```
AKsignal/
├── config/
│   └── sw_industry_rps.yaml
├── src/
│   ├── sw_industry_rps/
│   │   ├── __init__.py
│   │   ├── cli.py
│   │   ├── data_source.py
│   │   ├── storage.py
│   │   ├── metrics.py
│   │   ├── regimes.py
│   │   ├── validator.py
│   │   └── report.py
│   └── main.py          # 仅增加路由入口
├── data/
│   ├── raw/sw_industry/
│   │   ├── industry_master.csv
│   │   └── {industry_code}.csv
│   ├── processed/sw_industry/
│   │   ├── industry_daily_metrics.csv
│   │   ├── latest_snapshot.csv
│   │   └── rotation_matrix.csv
│   └── reports/sw_industry_rps/
├── reports/
│   ├── SW_INDUSTRY_RPS_PROJECT_DESIGN.md
│   ├── SW_INDUSTRY_RPS_DATA_SOURCE_AUDIT.md
│   └── SW_INDUSTRY_RPS_MVP_VERIFICATION.md
└── tests/
    └── sw_industry_rps/
        ├── __init__.py
        ├── conftest.py
        ├── test_metrics.py
        ├── test_regimes.py
        ├── test_storage.py
        └── test_report.py
```

---

## 4. 数据存储设计

沿用项目 CSV 存储方式。每个行业一个原始行情文件：

```
data/raw/sw_industry/industry_master.csv
data/raw/sw_industry/{code}.csv   # code = 801016.SI → 801016_SI.csv
```

字段定义：

```csv
trade_date,industry_code,industry_name,open,high,low,close,volume,amount,source,fetched_at,quality_status
```

processed 输出：

```csv
# industry_daily_metrics.csv
trade_date,industry_code,industry_name,close,return_5,return_10,return_15,RPS5,RPS10,RPS15,delta_rps15,streak_80,streak_90,first_entry_90_date,new_entry,strong_streak,accelerating,falling_out,short_term_acceleration,medium_term_acceleration

# latest_snapshot.csv  — 最新交易日快照
# rotation_matrix.csv  — 最近 20 交易日 RPS15 矩阵
```

---

## 5. RPS 指标定义

```
return_N = close_t / close_{t-N} - 1    # N=5,10,15，使用交易日窗口

RPS_N = 横截面百分位排名(return_N)       # 越高越好，范围 0-100
                                         # 并列使用平均排名
                                         # 缺少历史窗口的行业不参与该周期排名
delta_rps15 = RPS15_t - RPS15_{t-1}

short_term_acceleration = RPS5 - RPS15
medium_term_acceleration = RPS10 - RPS15
```

---

## 6. 状态识别规则

| 状态 | 条件 |
|------|------|
| `new_entry` | RPS15 ≥ 90 且 上日 RPS15 < 90 |
| `strong_streak` | RPS15 ≥ 90 且连续 ≥ 3 日 |
| `accelerating` | RPS15 ≥ 80 且 delta_rps15 ≥ 10 |
| `falling_out` | RPS15 < 90 且 上日 RPS15 ≥ 90 |

所有阈值集中配置在 `config/sw_industry_rps.yaml`。

---

## 7. CLI 设计

```bash
# 全部按顺序执行
python -m src.sw_industry_rps.cli run-day

# 分步执行
python -m src.sw_industry_rps.cli bootstrap      # 初始化 + 拉取 250 日
python -m src.sw_industry_rps.cli update           # 增量更新
python -m src.sw_industry_rps.cli calculate        # 离线计算指标
python -m src.sw_industry_rps.cli report           # 生成报告
python -m src.sw_industry_rps.cli validate         # 数据质量检查
```

---

## 8. 报告产物

每个交易日生成：

```
data/reports/sw_industry_rps/sw_industry_rps_YYYYMMDD.html
data/reports/sw_industry_rps/sw_industry_rps_YYYYMMDD.csv
data/reports/sw_industry_rps/sw_industry_rps_latest.html  # 最新可用
```

当数据质量状态 ≠ `usable` 时，不覆盖 `latest`。

---

## 9. 实施计划

### 阶段一（当前）：工程审计 + 数据源验证
产出：`SW_INDUSTRY_RPS_PROJECT_DESIGN.md` + `SW_INDUSTRY_RPS_DATA_SOURCE_AUDIT.md`

### 阶段二：MVP 实现
1. `config/sw_industry_rps.yaml` — 阈值配置
2. `src/sw_industry_rps/__init__.py`
3. `src/sw_industry_rps/data_source.py` — 行业列表 + 历史行情获取
4. `src/sw_industry_rps/storage.py` — CSV 读写 + 增量更新
5. `src/sw_industry_rps/metrics.py` — RPS 计算
6. `src/sw_industry_rps/regimes.py` — 状态识别
7. `src/sw_industry_rps/validator.py` — 数据质量检查
8. `src/sw_industry_rps/report.py` — HTML/CSV 报告
9. `src/sw_industry_rps/cli.py` — CLI 入口
10. `tests/sw_industry_rps/` — 全部测试

### 阶段三：真实运行 + 验收
使用测试数据进行完整流程验证。

---

## 10. 对现有功能的影响评估

| 风险项 | 影响 | 缓解措施 |
|--------|------|----------|
| 修改 `src/main.py` | 低 | 只增加路由 if 判断，不改默认流程 |
| 新增数据目录 | 无 | 使用独立子目录 `sw_industry/` |
| Python 包依赖 | 无 | 复用 `.venv`，不新增依赖 |
| `.gitignore` | 无 | 已排除 `data/` |
| 现有个股运行 | 无 | 互不干扰 |
| 磁盘空间 | 低 | 每个行业约 200KB，131 个行业约 26MB raw |
