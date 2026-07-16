# 项目状态

## 架构重组 P0-B ✅

运行数据/用户输出/项目文档已按职责分离。

### 目录结构

```text
AKsignal/
├── config/                       # 配置（版本控制）
├── data/                         # 运行数据与状态（gitignored）
│   ├── raw/processed/
│   └── state/asset_state.csv
├── outputs/                      # 用户消费的产物（gitignored）
│   ├── stock_trend/
│   └── sw_industry_rps/
├── docs/                         # 项目设计文档
├── src/
│   ├── main.py                   # 纯路由
│   ├── stock_trend/              # 个股趋势监控子系统
│   └── sw_industry_rps/          # 行业 RPS 子系统
└── tests/
```

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

| 指标 | 值 |
|------|-----|
| 历史行业 master | 131 |
| 当前有效行业 | 124 |
| 退休行业 | 7 |
| 当前有效行业覆盖率 | 100% |
| 历史数据 | 409,977 行 |
| 日期范围 | 1999-12-30 至 2026-07-14 |

bootstrap / update / calculate / validate / report / run-day 均已验证。
46 个测试通过。个股趋势模块无回归。

### CLI

```bash
python src/main.py industry <command>       # 显式
python src/main.py run-day                  # 向后兼容
python main.py bootstrap|update|...         # 向后兼容
```
