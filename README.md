# AKSignal — 三层决策信号链：ETF 轮动 × 行业确认 × 交易候选

## 项目架构

```text
src/
├── main.py                       # 顶层命令路由
├── common/                       # 公共层
│   ├── paths.py                  # 路径单一事实源
│   ├── run_context.py            # 运行上下文
│   └── manifest.py               # 产物发现契约
├── etf_signal/                   # Layer ① A股全市场 ETF 轮动
│   ├── cli.py                    # 全流程编排
│   ├── rotation.py               # 全市场横截面 RPS15/20/60 + 排名变动
│   ├── rotation_report.py        # Layer ① 报告
│   └── ...（master/classifier/account/card 等）
├── sw_industry_rps/              # Layer ② 申万二级行业确认
│   ├── cli.py                    # 行业 CLI（bootstrap/update/.../confirm）
│   ├── confirmation.py           # 重点行业群共振 + 子主题 + 龙头广度
│   ├── confirmation_report.py    # Layer ② 报告
│   └── ...（metrics/regimes/contribution 等）
├── selection/                    # Layer ③ 交易标的筛选（执行对象压缩）
│   ├── cli.py                    # select 命令
│   ├── universe.py               # 分层资产池（theme→tier→assets）
│   ├── selection.py              # 表达方式决策 + 候选对象构建
│   └── report.py                 # 候选 HTML 可视化
└── trend_engine/                 # Trend Engine（selection 的内部依赖）
    ├── engine.py                 # 批量趋势计算 API
    ├── data_provider.py          # AKShare 多市场数据提供器（多源/限流/退避）
    ├── indicators.py             # 技术指标（MA/RSI/MACD）
    ├── scoring.py                # 趋势评分（0-100）
    └── ...（asset/fetch_data）
```

三层信号主链，共用 `.venv` Python 环境和 `data/` 目录规划：
**Layer ① ETF 发现 → Layer ② 行业确认 → Layer ③ 交易候选（调用 Trend Engine）→ 未来 Layer 4 执行**

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Layer 3 交易标的筛选（selection）

### 功能简介

把 Layer ①（ETF 轮动）与 Layer ②（行业确认）的结论，压缩成「这个已确认方向用哪只 ETF、哪类股票交易」。核心是**执行对象压缩层**，不是又一层强弱排名。

- 输入：Layer① rotation + Layer② confirmation + 分层资产池 `config/stock_universe.yaml`
- 个股趋势由 **Trend Engine**（`trend_engine`）现场计算，不读独立报告
- 表达方式决策基于上涨结构：广泛上涨→ETF、龙头主导→龙头个股、扩散→ETF核心+龙头卫星、未确认→仅观察
- **核心输出是结构化候选对象 JSON**，HTML 仅为可视化

### 运行

```bash
make select                # 构建交易候选（在线）
make select-offline        # 仅缓存行情
python src/main.py select run
python src/main.py select universe   # 查看分层资产池
```

### 输出

- `outputs/selection/tradable_candidates_{date}.json` — 候选资产对象（唯一事实源）
- `outputs/selection/tradable_candidates_{date}.html` — 可视化

Layer 4（Portfolio）边界：本层只回答「买什么」，不回答「买多少 / 何时买 / 何时卖」。

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
python src/main.py industry run-day

# 分步执行
python src/main.py industry bootstrap
python src/main.py industry update
python src/main.py industry calculate
python src/main.py industry report
python src/main.py industry validate

# 或通过 -m 直接调用
python -m src.sw_industry_rps.cli run-day
```

旧入口仍可用（向后兼容）：

```bash
python src/main.py run-day
python src/main.py bootstrap|update|calculate|report|validate
```

### 报告位置

```
outputs/sw_industry_rps/
├── sw_industry_rps_YYYYMMDD.html     # 每日报告
├── sw_industry_rps_YYYYMMDD.csv      # 每日数据
└── sw_industry_rps_latest.html       # 最新可用报告（仅数据质量 usable 时更新）
```

### 已知限制

1. **swsresearch.com API 当前不可用**（HTTP 508）—— `index_hist_sw()` 是 AKShare 中获取申万行业历史行情的唯一原生接口，在接口恢复前无法获取真实行情数据
2. 首版使用 CSV 存储，未引入数据库
3. 未实现行业成分股联动分析（预留接口）
4. 行业详情走势图属于 MVP 次优先级

## ETF 趋势资产发现

### 功能定位

基于 AKShare 构建可观察的沪深场内 ETF 数据池，按资产类别分桶计算市场热度，在国金证券可交易范围内生成趋势 Watchlist 和候选信息卡片。

### 版本状态

```text
v0.1.0-etf-discovery  ✅
  状态：
    orchestration pipeline：PASSED
    discovery signal：      PASSED
    historical coverage：   PARTIAL（255/300，85.0%）
    broker coverage：       PARTIAL（116/300 已验证）

  v0.2.0-etf-shadow-validation ⬅ 当前
    目标：连续记录每日信号，通过回测和影子运行验证信号质量
```

### 工作流

```text
300 只核心 Universe → 255 只历史行情 → 252 只指标 → 101 只活跃
    → 101 只国金可交易 → 101 张候选卡片
```

### 命令

```bash
# 数据底座
python src/main.py etf bootstrap-core

# 全链路（10 分钟）
python src/main.py etf calculate && python src/main.py etf pipeline
```

### 输出

```
outputs/etf_signal/
├── candidate_cards_{date}.json        # 完整卡片（含风险标记）
└── watchlist_active_{date}.csv        # 活跃 ETF 清单
```

详细文档见 `docs/AKsignal_ETF_P0_方案.md`。

---

## 三层信号链

| 层 | 模块 | 职责 | 入口 |
|------|------|------|------|
| Layer ① | etf_signal | A股全市场 ETF 轮动（发现主线） | `python src/main.py etf <cmd>` |
| Layer ② | sw_industry_rps | 申万二级行业确认（验证质量） | `python src/main.py industry <cmd>` |
| Layer ③ | selection | 交易标的筛选（执行对象压缩） | `python src/main.py select run` |
| Trend Engine | trend_engine | 指标与评分（selection 内部依赖） | 无独立入口 |
| Layer 4 | — | Portfolio（买多少/何时买卖） | ⬜ 未来 |

Layer ③ 严格继承 Layer①/② 产出，不重新从全市场开始；Trend Engine 只服务 selection，不暴露独立业务层。
