# 项目状态

## 个股技术趋势监控

### AKSignal v0.3 ✅

AKShare 拉取 → raw 缓存 → 指标计算 → 趋势评分 → 相对强度(沪深300) → watch_level / action / portfolio_summary / watchlist → HTML/CSV 报告

监控标的：寒武纪、中际旭创、科大讯飞；理想汽车(US/HK)、蔚来、小鹏汽车(US/HK)；上汽集团；宁德时代、德赛西威、韦尔股份、Mobileye、速腾聚创、地平线；中证500ETF、黄金ETF

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
46 个新增测试全部通过。个股趋势模块无回归。
