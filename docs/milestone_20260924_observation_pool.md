# 项目里程碑 · A 股观察池扩展至 59 只（2026-09-24）

> 类型：观察池 / 研究候选扩展（Reference & Research，**不进入生产决策链**）
> 日期：2026-09-24
> 变更：观察池 51 → 59（+8）

---

## 1. 一句话

在原有 **51 只 A 股观察池**基础上，新增 **8 只存储 / 记忆主题（`memory_and_storage`）候选**，观察池扩至 **59 只**。
8 只最初为 `candidate_pending_quotes`（待行情核验）；**已于同日补齐 2026-09-23 日线行情与技术指标（`data_status=current`）**。原有 51 只的行情与技术指标**原样保留（逐字段校验 0 差异）**。

## 2. 边界（明确不做什么）

- 不修改 `config/theme_registry.yaml`（主题与确认 Gate 不变）；`config/selection_universe.yaml` 仅新增 1 个 monitor_only tier（§8）。
- 其余 5 只候选（佰维/德明利/普冉/聚辰/香农芯创）**未注册**，不进入 Layer② 主题确认、Layer③ 交易候选、stock-metrics 主链路。
- 所有 8 只候选不产生任何买入信号：已注册 3 只为 `participation=monitor_only`（仅监控），未注册 5 只此时点仅存在于参考文件。
- 行情的补齐是一次性核验（Engine 直跑，未注册 5 只不在主 universe）；已注册 3 只进入日常 stock-metrics 流程（§8/§9）。

## 3. 新增 8 只候选

| 代码 | 名称 | tier | 定位 | 证据 |
|---|---|---|---|---|
| 603986 | 兆易创新 | `flash_memory_design` | NOR/NAND/利基型 DRAM 设计，存储产品线较完整 | [gigadevice](https://www.gigadevice.com.cn/product/flash) |
| 301308 | 江波龙 | `memory_modules_and_solutions` | 嵌入式存储 / SSD / 移动存储 / 内存条 | [longsys](https://cn.longsys.com/) |
| 688525 | 佰维存储 | `memory_modules_and_solutions` | 存储方案 + 主控 + 封测/先进封测 | [biwin](https://www.biwin.com.cn/about/company) |
| 001309 | 德明利 | `storage_controller_and_modules` | 自主存储主控与固件方案 + 模组 | [twsc](https://twsc.com.cn/about/index.html) |
| 688766 | 普冉股份 | `nonvolatile_memory_design` | NOR Flash / EEPROM 等非易失存储 | [puyasemi](https://www.puyasemi.com/about.html) |
| 688123 | 聚辰股份 | `memory_interface_adjacent` | EEPROM + 服务器内存条 SPD/SPD+TS | [giantec-semi](https://www.giantec-semi.com/spdxilie) |
| 688449 | 联芸科技 | `ssd_controller` | SATA / PCIe·NVMe SSD 控制芯片 | [maxio-tech](https://www.maxio-tech.com/product/11628/) |
| 300475 | 香农芯创 | `memory_distribution_and_products` | 存储芯片代理分销 + 自主品牌 | [shannonxsemi](https://www.shannonxsemi.com/) |

> 均为主题初筛候选，非纯 DRAM/HBM 原厂，属研究观察，不作为买入信号。

## 4. 主题定位

`memory_and_storage`（存储 / 记忆）——覆盖 **设计**（兆易 / 普冉 / 聚辰）、**模组与方案**（江波龙 / 佰维）、**主控**（德明利 / 联芸）、**分销**（香农芯创）四个环节。

## 5. 事实源与产物

| 用途 | 路径 |
|---|---|
| 原始变更文件（51 继承 + 8 新增） | `data/reference/ashare_stock_list_20260924_candidate_update.csv` |
| 合并观察池（59 行，6 位代码归一；8 只已回填 0923 行情/指标） | `data/reference/ashare_stock_observation_pool_20260924.csv` |
| 前序池（51 行） | `outputs/stock_metrics/ashare_stock_list_20260923.csv` |

> `data/`、`outputs/` 均 gitignored → 变更文件本地保留；里程碑以 `docs/` + `STATUS.md` 记录。

## 6. 校验

- 合并池 **59 行、symbol 唯一**。
- **51 只继承行逐字段一致**（close / return_5d / return_20d / trend_score / watch_level / action / participation / data_status / risk_flags），仅补回被算术去零的 6 位代码 —— 本轮补齐**再次校验 0 差异**（含新增列前的继承列全字段）。
- 8 只新行：`data_status=current`、`participation=monitor_only`（tier 语义保留在行内；Engine 类别字段仅原子管道的过滤位）。

## 7. 行情核验结果（2026-09-23，已回填）

技术指标一次补齐（`score_trend` 0-100、`watch_level`、`action`、`data_status=current`）；**均为技术评分快照，不代表买入信号**，且部分已命中「风险警戒」（强康/长下跌缺口需先看 MA / log 判据）。

| 代码 | 名称 | close | return_5d | return_20d | score_trend | watch_level | action | 风险 |
|---|---|---|---|---|---|---|---|---|
| 603986 | 兆易创新 | 398.72 | 0.0686 | 0.0158 | 55 | B | 风险警戒 | 跌破MA60/MA120 |
| 301308 | 江波龙 | 346.00 | 0.0208 | -0.0500 | 20 | C | 风险警戒 | 破MA20/60/120，RS负 |
| 688525 | 佰维存储 | 222.69 | 0.0445 | -0.0011 | 35 | B | 风险警戒 | 破MA60/120，RS负 |
| 001309 | 德明利 | 410.32 | 0.0232 | 0.0502 | 55 | B | 风险警戒 | 破MA60/120 |
| 688766 | 普冉股份 | 431.00 | 0.0806 | 0.0852 | 65 | B | 风险警戒 | 破MA60 |
| 688123 | 聚辰股份 | 168.11 | 0.1918 | 0.2882 | 90 | A | 风险警戒 | — |
| 688449 | 联芸科技 | 64.36 | 0.0248 | 0.0159 | 80 | A | 风险警戒 | — |
| 300475 | 香农芯创 | 179.03 | 0.0780 | 0.1742 | 80 | A | 风险警戒 | 破MA120 |

> 全部 8 只 action=风险警戒：`drawdown_from_high ≥ 0.15` 处的判定（未写回生产链路）；技术评分对存储主题分化明显（聚辰/联芸/香农 score≥80 vs 江波龙/佰维 <50）。

## 8. 后续

1. ✅ **行情核验**（2026-09-24 完成）：8 只候选日线行情与技术指标已补齐（online fetch，as_of=20260923，`data_status=current`）。
2. ✅ **首批注册 3 只 monitor_only**（2026-09-24）：兆易创新（存储芯片）/ 联芸科技（SSD 主控）/ 江波龙（存储产品）→ `config/selection_universe.yaml` `ai_infrastructure.memory_and_storage`（`participation=monitor_only`，`sw_industry=801081.SI` 半导体）；`src/trend_engine/inputs.py` `STOCK_TIERS` 同步补 `memory_and_storage`。只进 stock-metrics「核心资产监控」，不参与主题排名/四段信号/候选资格；`theme_registry.yaml` 不改（不进主题确认 Gate）。
3. 其余 5 只（佰维/德明利/普冉/聚辰/香农芯创）留候选池：行情指标已补齐，待评估。
4. 注册后进入 stock-metrics「核心资产监控」随 run-day 每日刷新（`make stock-metrics` 已验证 58/58 current）。

## 9. 校验（注册后）

- `selection_universe.yaml` 加载 76 assets / 3 themes；`detect_unregistered_themes=[]`；`cross_theme_assets` 仅原有 拓普/三花（`601689`/`002050`）。
- `memory_and_storage` tier 3 只：theme=ai_infrastructure、participation=monitor_only、STOCK_TIERS 放行（stock items 58/76）。
- `make stock-metrics`（离线）重建 `stock_metrics_20260923.parquet` **58 行 / 58 current**，3 只与新池数值一致（兆易 398.72/55/B、联芸 64.36/80/A、江波龙 346.00/20/C）。
- **角色映射修复**：`src/selection/selection.py` `TIER_ROLE_MAP` 补 `memory_and_storage → LEADER`（注册时遗漏，导致 Layer③ 观察池按 `role=None` 静默跳过）。修复后 engine（`include_stocks=True`，research/parity 路径）监控组 monitor_only 8 只 = 5 先例（auto_thermal：中鼎/银轮/飞龙/拓普/三花）+ 3 只（兆易/联芸/江波龙），全部 state=WATCH / recommended=False / reason_codes=`['monitor_only']`；每日 ETF-only 发布（`include_stocks=False`）个股容器为空，行为不受影响。`tests/selection` 196 + `tests/trend_engine` 131 全绿。

## 10. 观察池口径（2026-09-24 固化，participation 是 stock×tier 层属性）

**避免「48+6」|「46+8」歧义**：以「unique stocks / confirmation-capable / monitor-involved / overlap / pure monitor-only」分层表述（与 AGENTS.md「participation 是 stock×tier 层属性」段一致，见本文件 §2+§9 联动）：

| 口径 | 数量 | 定义 | 明细 |
|---|---|---|---|
| unique stocks | 54 | 观察池 ∩ `selection_universe.yaml`（59−5 未注册候选） | 51 原池 + 3 新注册 |
| confirmation-capable | 48 | 存在 ≥1 个 tier 注册 tradeable 且该 tier 进入 Theme→Tier 确认 | 20 ai（AI 基建 tier 正式）+ 11 auto + 15 hc + **2 overlap（拓普/三花）** |
| monitor-involved | 8（tier 注册条目数） | 存在 ≥1 个 tier 注册 monitor_only | auto_thermal 5（中鼎/银轮/飞龙/**拓普/三花**）+ memory 3（兆易/联芸/江波龙） |
| overlap 双身份 | 2 | 同时进 confirmation-capable 与 monitor-involved | 拓普/三花：ai_infra.auto_thermal(monitor_only) + auto.global_ev(tradeable) |
| pure monitor-only | 6 | 只有 monitor_only 注册、无 tradeable 注册 | 中鼎/银轮/飞龙 + 兆易/联芸/江波龙 |

口径要点：**confirmation-capable(48) 含 overlap**；**pure monitor-only(6) 不含 overlap**；**monitor-involved(8) = pure 6 + overlap 2**。禁止写「48+6」（把两层相加）与「46+8」（把 overlap 排除出 confirmation-capable 的隐含假设）。观察池 CSV 中 002050/601689 行 participation 列保留 `tradeable`（表示 china_auto 身份），screen_note 注明 stock×tier 双身份，不压成股票级单一标签。
