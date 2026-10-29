# 核心指数观察验收 · 2026-10-28

目标交易日：20261027。规则 `core-index-facts-v1`；日报布局 `daily_observation_modules_v11`。

- 相关测试：171 passed（`tests/market_facts/test_core_indices.py tests/observation tests/daily_run`）。覆盖固定注册、真实指数身份、NoAdjust、独立窗口缺口、历史不足、缺目标日、盘中拒绝、原始OHLC拒绝、静态身份核验失败、单项失败隔离、纯离线、不可变回执、文件/价格/日期/配置篡改、下跌指数固定展示及Hub分离摘要。
- 实际长桥取数：沪深300、中证500、中证1000、创业板指、科创50、中证全指、中证A500各243个日线样本，真实末日2026-10-27；原始文件摘要、manifest及注册签名核验通过。
- 中证2000与红利低波缺少已核验的长桥映射，保留UNAVAILABLE固定行，不产生价格或收益。
- A500的800自然日探索窗口因无效开盘价拒绝；统一近365自然日正式窗口通过OHLC验收，不认证更早历史。
- 正式执行：`make run-day-offline RUN_DATE=20261027 RUN_FLAGS=--restart`，从空批次完成ETF计算、股票同日快照消费、指数与TDX构建、检查和原子发布。
- 发布ID：`2a427288608a4c4aa560cfe8fd15f6b4`；`ok=true / status=PARTIAL`；核心指数7/9可用（宽基7/8、风格0/1），ETF事实1339行，TDX行业128行，个股69条注册资产（含1条既有政策排除）。
- 发布后重读并重算core_indices JSON，核对parquet和HTML/Markdown一致性；Hub摘要正确区分固定指数与ETF汇总。
- 本批保留既有ETF未更新5只、个股价格依据未独立认证、个股位置1条未知及ETF位置442条未知提示。新增核心指数部分不可用提示。
- `git diff --check` 通过。浏览器安全策略拒绝file协议本地HTML预览，未进行截图验收；不能将内容一致性测试表述为视觉验收通过。

原始快照与早期取数失败批次均保留在 `data/sources/longbridge/raw/core_indices/`，实际取数审计位于 `data/audits/core_indices/20261027/acquisition_check.json`。正式报告位于该发布批次的 `outputs/observation/observation_20261027.html`。
