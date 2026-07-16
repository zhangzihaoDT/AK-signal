# 申万二级行业 RPS 监控 — 数据源审计报告

## 审计日期

2026-07-15

## 审计方法

使用现有 `.venv` 环境（Python 3.13, akshare 1.18.62）对 AKShare 中所有申万相关接口进行真实调用验证。

---

## 1. 行业分类信息接口

### sw_index_second_info()

- **数据源**：legulegu.com（乐咕乐股）
- **状态**：✅ **可用**
- **返回字段**：行业代码、行业名称、上级行业、成份个数、静态市盈率、TTM市盈率、市净率、静态股息率
- **行业数量**：131
- **代码格式**：`801016.SI`
- **响应时间**：~1-2s
- **结论**：可直接用于行业元数据初始化

### sw_index_first_info()

- **数据源**：legulegu.com
- **状态**：✅ **可用**
- **行业数量**：31（一级行业）
- **结论**：可用于行业层级映射（可选）

---

## 2. 历史行情接口（关键）

### index_hist_sw()

- **数据源**：swsresearch.com（申万宏源研究）
- **状态**：❌ **不可用**
- **错误**：HTTP 508 — 服务器端错误
- **URL**：`https://www.swsresearch.com/institute-sw/api/index_publish/trend/`
- **参数**：`swindexcode=801030&period=DAY`
- **尝试次数**：6 次，间隔 5-10s
- **结论**：该 API 在审计期间持续返回 508 错误，无法获取行业指数历史 OHLCV
- **风险**：这是 AKShare 中获取申万行业指数历史行情的**唯一原生接口**

### index_analysis_daily_sw()

- **数据源**：swsresearch.com
- **状态**：❌ **不可用**
- **错误**：JSONDecodeError（同域名 508）

### index_component_sw()

- **数据源**：swsresearch.com
- **状态**：❌ **不可用**

### stock_industry_clf_hist_sw()

- **数据源**：swsresearch.com（XLS 文件）
- **状态**：❌ **不可用**
- **错误**：SSL 证书验证失败

---

## 3. 替代数据源验证

### East Money 行业板块（stock_board_industry_hist_em）

- **状态**：⚠️ **网络不稳定，且非申万口径**
- **行业数量**：496（EM 自有分类）
- **申万相关性**：0 — 板块名称为 EM 自建，不包含申万分类
- **结论**：不适合作为申万二级行业数据的替代来源

### Sina / Tencent 指数接口

- **状态**：❌ **不支持申万指数代码**
- 验证了 `sh801030`、`sz801030`、`801030.SI` 等格式，均返回空数据

### East Money 指数接口（stock_zh_index_daily_em）

- **状态**：❌ **不支持申万指数代码**
- 仅支持 CSI、沪深、北交所指数

---

## 4. 汇总

| 接口类别 | 接口 | 可用性 | 数据源 |
|---------|------|--------|--------|
| 行业列表（二级） | `sw_index_second_info` | ✅ | legulegu.com |
| 行业列表（一级） | `sw_index_first_info` | ✅ | legulegu.com |
| 行业列表（三级） | `sw_index_third_info` | ✅ | legulegu.com |
| 历史行情 | `index_hist_sw` | ❌ 508 | swsresearch.com |
| 历史行情（分析） | `index_analysis_daily_sw` | ❌ 508 | swsresearch.com |
| 成分股 | `index_component_sw` | ❌ 508 | swsresearch.com |
| 成分股列表 | `sw_index_third_cons` | ⚠️ 列宽不匹配 | swsresearch.com |
| 行业分类（个股） | `stock_industry_clf_hist_sw` | ❌ SSL 错误 | swsresearch.com |
| EM 行业板块 | `stock_board_industry_hist_em` | ⚠️ 非 SW | East Money |

---

## 5. 应对策略

1. **行业元数据**：使用 `sw_index_second_info()`（legulegu.com）获取 131 个二级行业代码和名称
2. **历史行情**：当前 swsresearch.com API 不可用，需要实施以下策略：
   - 在 `data_source.py` 中实现重试机制（最多 5 次，指数退避）
   - 如重试仍失败，记录错误并抛出明确异常
   - 项目通过测试夹具提供合成数据，确保离线计算和报告逻辑可验证
   - 待 swsresearch.com 恢复后，数据源适配器可无缝切换到真实数据
3. **文档标记**：所有报告标注数据来源和获取时间
4. **未来方案**：如果 swsresearch.com 长期不可用，可考虑：
   - 使用东方财富申万行业板块（需要确认 BK 代码到 SW 代码的映射关系）
   - 通过成分股加权模拟行业指数（计算量大，需验证准确性）
