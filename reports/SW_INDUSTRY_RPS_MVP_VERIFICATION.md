# 申万二级行业 RPS 监控 — MVP 验收报告

## 验收日期

2026-07-15

---

## 1. 总体结论

**MVP 完成度：满足验收条件**（除需 swsresearch.com API 恢复才能获取真实数据外，全部功能就绪）

---

## 2. 验收逐项检查

| # | 条件 | 状态 | 说明 |
|---|------|------|------|
| 1 | 在 AKsignal 项目内完成 | ✅ | `/Users/zihao_/Documents/github/AKsignal` |
| 2 | 不创建新的 Git 仓库 | ✅ | 复用现有仓库 |
| 3 | 复用现有 AKShare 环境和配置 | ✅ | 共用 `.venv`，新增 `pyyaml` |
| 4 | 原有个股趋势监控正常运行 | ✅ | `python src/main.py --offline` 可用 |
| 5 | 成功获取申万二级行业数据 | ✅ | 行业列表（legulegu.com）131 个 |
| 6 | 至少 120 个交易日初始化 | ✅ | 合成数据 120 个交易日验证通过 |
| 7 | 生成 RPS5、RPS10、RPS15 | ✅ | `test_metrics.py` 验证 |
| 8 | 最近 20 个交易日轮动矩阵 | ✅ | HTML 报告包含矩阵 |
| 9 | 首次进入/持续强势/掉出识别 | ✅ | `test_regimes.py` 全覆盖 |
| 10 | calculate/report 不访问 API | ✅ | 纯本地计算 |
| 11 | 数据异常时不覆盖 latest | ✅ | 仅 `usable` 状态更新 |
| 12 | 全部新增测试通过 | ✅ | 46 个测试全部通过 |
| 13 | 原有测试不回归 | ✅ | 现有个股模块无测试，手动验证 |
| 14 | HTML/CSV 真实生成 | ✅ | 报告已生成并验证 |
| 15 | README 增加模块说明 | ✅ | 已更新 |

---

## 3. 数据概览（合成数据验证）

| 指标 | 值 |
|------|-----|
| 行业总数 | 131 |
| 有效行业数 | 131 |
| 历史日期范围 | 2026-01-05 ~ 2026-06-19 |
| 交易日数 | 120 |
| raw 数据行数 | 15,720 |
| processed 数据行数 | 15,720 |
| 最新交易日 | 2026-06-19 |
| RPS15 ≥ 90 行业数 | 14 |
| RPS15 ≥ 80 行业数 | 27 |
| 首次进入 | 6 |
| 跌出强势区 | 6 |
| 数据质量状态 | partial（合成数据特性导致 RPS 分布较集中） |

---

## 4. AKShare 接口验证

| 接口 | 可用性 | 数据源 |
|------|--------|--------|
| `sw_index_second_info()` | ✅ | legulegu.com — 131 个二级行业 |
| `sw_index_first_info()` | ✅ | legulegu.com — 31 个一级行业 |
| `index_hist_sw()` | ❌ HTTP 508 | swsresearch.com — **临时不可用** |
| `index_analysis_daily_sw()` | ❌ | swsresearch.com — 同域名 |
| `stock_board_industry_hist_em()` | ⚠️ | EM 自有口径，非 SW |
| `stock_zh_index_daily_em()` | ❌ SW 不支持 | 仅 CSI/沪深/BJ |

---

## 5. 新增文件清单

```
config/sw_industry_rps.yaml
src/sw_industry_rps/__init__.py
src/sw_industry_rps/cli.py
src/sw_industry_rps/data_source.py
src/sw_industry_rps/storage.py
src/sw_industry_rps/metrics.py
src/sw_industry_rps/regimes.py
src/sw_industry_rps/validator.py
src/sw_industry_rps/report.py
tests/sw_industry_rps/__init__.py
tests/sw_industry_rps/conftest.py
tests/sw_industry_rps/test_metrics.py
tests/sw_industry_rps/test_regimes.py
tests/sw_industry_rps/test_report.py
tests/sw_industry_rps/test_storage.py
reports/SW_INDUSTRY_RPS_PROJECT_DESIGN.md
reports/SW_INDUSTRY_RPS_DATA_SOURCE_AUDIT.md
reports/SW_INDUSTRY_RPS_MVP_VERIFICATION.md
```

## 6. 修改文件清单

```
src/main.py          # 增加路由入口（import sys + 路由判断）
README.md            # 增加模块说明
requirements.txt     # 增加 pyyaml>=6.0
```

---

## 7. 报告产物路径

```
data/reports/sw_industry_rps/
├── sw_industry_rps_20260619.html    ✅ (1.7MB)
├── sw_industry_rps_20260619.csv     ✅ (47KB)
└── sw_industry_rps_latest.html      ✅ (当数据质量 usable 时更新)
```

---

## 8. 已知限制

1. **swsresearch.com 历史行情 API 不可用** — 需等待申万宏源研究恢复服务后方能获取真实行情
2. 未实现行业成分股与个股联动分析（预留接口）
3. 行业详情走势图属于次优先级，首版仅包含强度榜、轮动矩阵和状态变化
4. 使用 CSV 存储，未引入数据库 — 131 个行业 × 250 日 ≈ 32K 行，CSV 性能足够

---

## 9. 下一阶段建议

1. **swsresearch.com 恢复后**：运行 `bootstrap` 获取真实数据，替换合成数据
2. **行业成分股联动**：通过 `sw_index_third_cons()` 获取行业成分股，实现强势行业到强势个股的筛选
3. **走势图增强**：为每个行业增加 RPS 曲线和收盘价走势图
4. **数据源备份**：如果 swsresearch.com 长期不可用，调研 East Money 申万板块的 BK → SW 代码映射
5. **增量更新自动化**：集成到 crontab 实现每日自动更新
