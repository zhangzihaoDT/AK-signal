# 项目状态

## 架构重组 P0-A ✅

个股趋势监控已收拢为 `stock_trend` 子系统；顶层 `main.py` 已降级为双子系统命令路由；指标、评分、数据路径和输出口径保持不变。

### 目录结构

```text
src/
├── main.py                       # 纯路由
├── stock_trend/                  # 个股趋势监控子系统
│   ├── cli.py → pipeline.py      # CLI → 业务编排
│   ├── asset.py / fetch_data.py / data_provider.py
│   ├── indicators.py / scoring.py
│   ├── portfolio.py / report.py  # 报告生成
│   └── watchlist.py
└── sw_industry_rps/              #（未变更）
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
