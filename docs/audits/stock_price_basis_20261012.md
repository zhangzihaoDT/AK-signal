# 固定观察池股票历史价格复权审计 · 20261012

核对 58 只唯一股票（注册身份仍独立保留）。只读取缓存和公开参考历史，不修改行情或观察产物。参考序列是抓取时点的当前供应商基准，不是历史时点的复权快照。

## 结果

- 全历史 OHLC 在一分报价误差内与至少一个当前前复权参考一致：2 只。
- 收盘价分类：{"LOCALIZED_CLOSE_DIFFERENCES": 43, "PERSISTENT_CLOSE_DIFFERENCES": 13, "MATCH_QFQ_CLOSE": 2}。
- 360 日位置差绝对值 ≥1 个百分点：6 只。
- 120/360 ≤20 的长期低位判定变化：0 只。
- 全部缓存 SHA256 未变化：True。

匹配说明当前缓存与参考序列相容，不能逆向证明历史每次抓取的复权参数。少量局部差异不等于已通过验证；persistent 也不自动证明混合复权，因为供应商前复权算法可能不同。

## 已观察到的工程问题

A 股获取链请求 qfq，但增量窗口按最后报价日之后抓取并直接拼接旧缓存，未保存调整基准、因子或检查历史重标定。两只港股调用 stock_hk_daily 时省略 adjust，而安装版本默认 adjust=""，即不复权；未实现统一的显式复权口径。

## 位置读数影响（按最大差值）

| market   |   symbol | name   | best_reference   |   position_delta_60 |   position_delta_120 |   position_delta_360 | long_term_bottom_changed   |
|:---------|---------:|:-------|:-----------------|--------------------:|---------------------:|---------------------:|:---------------------------|
| CN       |   000429 | 粤高速A   | sina_qfq         |          -11.7296   |           -17.7111   |            -13.051   | False                      |
| CN       |   600941 | 中国移动   | sina_qfq         |          -15.9018   |           -10.1568   |            -10.375   | False                      |
| CN       |   601728 | 中国电信   | sina_qfq         |           -9.57447  |            -9.40627  |             -6.82541 | False                      |
| CN       |   600660 | 福耀玻璃   | em_qfq           |          -10.989    |            -8.48176  |             -3.75848 | False                      |
| CN       |   600104 | 上汽集团   | sina_qfq         |           -5.60685  |            -6.29699  |             -2.53971 | False                      |
| HK       |    02510 | 禾赛     | sina_qfq         |           -0.758741 |            -0.881233 |             -1.67438 | False                      |

## 逐只结果

| market   |   symbol | name   | close_class                  | best_reference   |   close_mismatch_rows |   position_delta_360 | long_term_bottom_changed   |
|:---------|---------:|:-------|:-----------------------------|:-----------------|----------------------:|---------------------:|:---------------------------|
| CN       |   000429 | 粤高速A   | PERSISTENT_CLOSE_DIFFERENCES | sina_qfq         |                  2089 |          -13.051     | False                      |
| CN       |   000887 | 中鼎股份   | MATCH_QFQ_CLOSE              | sina_qfq         |                     0 |            0         | False                      |
| CN       |   001872 | 招商港口   | LOCALIZED_CLOSE_DIFFERENCES  | sina_qfq         |                     5 |            0         | False                      |
| CN       |   001965 | 招商公路   | LOCALIZED_CLOSE_DIFFERENCES  | sina_qfq         |                     1 |            0         | False                      |
| CN       |   002050 | 三花智控   | PERSISTENT_CLOSE_DIFFERENCES | sina_qfq         |                  2022 |            0.0512533 | False                      |
| CN       |   002126 | 银轮股份   | LOCALIZED_CLOSE_DIFFERENCES  | sina_qfq         |                     1 |            0         | False                      |
| CN       |   002130 | 沃尔核材   | LOCALIZED_CLOSE_DIFFERENCES  | sina_qfq         |                     2 |            0         | False                      |
| CN       |   002463 | 沪电股份   | LOCALIZED_CLOSE_DIFFERENCES  | sina_qfq         |                     4 |            0         | False                      |
| CN       |   002475 | 立讯精密   | LOCALIZED_CLOSE_DIFFERENCES  | em_qfq           |                     4 |            0         | False                      |
| CN       |   002536 | 飞龙股份   | LOCALIZED_CLOSE_DIFFERENCES  | em_qfq           |                     1 |            0         | False                      |
| CN       |   002594 | 比亚迪    | LOCALIZED_CLOSE_DIFFERENCES  | sina_qfq         |                     1 |            0         | False                      |
| CN       |   002837 | 英维克    | LOCALIZED_CLOSE_DIFFERENCES  | sina_qfq         |                     2 |            0         | False                      |
| CN       |   002850 | 科达利    | LOCALIZED_CLOSE_DIFFERENCES  | sina_qfq         |                     3 |            0         | False                      |
| CN       |   002851 | 麦格米特   | LOCALIZED_CLOSE_DIFFERENCES  | sina_qfq         |                     3 |            0         | False                      |
| CN       |   002916 | 深南电路   | LOCALIZED_CLOSE_DIFFERENCES  | em_qfq           |                     4 |            0         | False                      |
| CN       |   003816 | 中国广核   | LOCALIZED_CLOSE_DIFFERENCES  | sina_qfq         |                     4 |            0         | False                      |
| CN       |   300014 | 亿纬锂能   | PERSISTENT_CLOSE_DIFFERENCES | em_qfq           |                  2105 |           -0.828347  | False                      |
| CN       |   300308 | 中际旭创   | PERSISTENT_CLOSE_DIFFERENCES | sina_qfq         |                  2122 |           -0.0309284 | False                      |
| CN       |   300394 | 天孚通信   | PERSISTENT_CLOSE_DIFFERENCES | sina_qfq         |                  1505 |           -0.134113  | False                      |
| CN       |   300476 | 胜宏科技   | LOCALIZED_CLOSE_DIFFERENCES  | sina_qfq         |                     2 |            0         | False                      |
| CN       |   300502 | 新易盛    | LOCALIZED_CLOSE_DIFFERENCES  | sina_qfq         |                     4 |            0         | False                      |
| CN       |   300548 | 长芯博创   | LOCALIZED_CLOSE_DIFFERENCES  | em_qfq           |                     6 |            0         | False                      |
| CN       |   300570 | 太辰光    | LOCALIZED_CLOSE_DIFFERENCES  | em_qfq           |                     6 |            0         | False                      |
| CN       |   300750 | 宁德时代   | PERSISTENT_CLOSE_DIFFERENCES | sina_qfq         |                  1980 |            0.585424  | False                      |
| CN       |   300870 | 欧陆通    | LOCALIZED_CLOSE_DIFFERENCES  | sina_qfq         |                     3 |            0         | False                      |
| CN       |   300913 | 兆龙互连   | LOCALIZED_CLOSE_DIFFERENCES  | sina_qfq         |                     4 |            0         | False                      |
| CN       |   301308 | 江波龙    | LOCALIZED_CLOSE_DIFFERENCES  | sina_qfq         |                     1 |            0         | False                      |
| CN       |   600011 | 华能国际   | LOCALIZED_CLOSE_DIFFERENCES  | sina_qfq         |                     2 |            0         | False                      |
| CN       |   600018 | 上港集团   | LOCALIZED_CLOSE_DIFFERENCES  | sina_qfq         |                     5 |            0         | False                      |
| CN       |   600025 | 华能水电   | LOCALIZED_CLOSE_DIFFERENCES  | sina_qfq         |                     3 |            0         | False                      |
| CN       |   600050 | 中国联通   | LOCALIZED_CLOSE_DIFFERENCES  | sina_qfq         |                     3 |            0         | False                      |
| CN       |   600104 | 上汽集团   | PERSISTENT_CLOSE_DIFFERENCES | sina_qfq         |                  2082 |           -2.53971   | False                      |
| CN       |   600183 | 生益科技   | LOCALIZED_CLOSE_DIFFERENCES  | sina_qfq         |                     1 |            0         | False                      |
| CN       |   600377 | 宁沪高速   | LOCALIZED_CLOSE_DIFFERENCES  | sina_qfq         |                     4 |            0         | False                      |
| CN       |   600660 | 福耀玻璃   | PERSISTENT_CLOSE_DIFFERENCES | em_qfq           |                  2117 |           -3.75848   | False                      |
| CN       |   600699 | 均胜电子   | LOCALIZED_CLOSE_DIFFERENCES  | sina_qfq         |                     3 |            0         | False                      |
| CN       |   600886 | 国投电力   | LOCALIZED_CLOSE_DIFFERENCES  | sina_qfq         |                     3 |            0         | False                      |
| CN       |   600900 | 长江电力   | LOCALIZED_CLOSE_DIFFERENCES  | sina_qfq         |                     4 |            0         | False                      |
| CN       |   600941 | 中国移动   | PERSISTENT_CLOSE_DIFFERENCES | sina_qfq         |                  1147 |          -10.375     | False                      |
| CN       |   601138 | 工业富联   | LOCALIZED_CLOSE_DIFFERENCES  | sina_qfq         |                     3 |            0         | False                      |
| CN       |   601298 | 青岛港    | LOCALIZED_CLOSE_DIFFERENCES  | sina_qfq         |                     3 |            0         | False                      |
| CN       |   601633 | 长城汽车   | LOCALIZED_CLOSE_DIFFERENCES  | sina_qfq         |                     2 |            0         | False                      |
| CN       |   601689 | 拓普集团   | LOCALIZED_CLOSE_DIFFERENCES  | sina_qfq         |                     3 |            0         | False                      |
| CN       |   601728 | 中国电信   | PERSISTENT_CLOSE_DIFFERENCES | sina_qfq         |                  1224 |           -6.82541   | False                      |
| CN       |   601985 | 中国核电   | LOCALIZED_CLOSE_DIFFERENCES  | sina_qfq         |                     5 |            0         | False                      |
| CN       |   603083 | 剑桥科技   | PERSISTENT_CLOSE_DIFFERENCES | sina_qfq         |                   695 |           -0.0314655 | False                      |
| CN       |   603228 | 景旺电子   | LOCALIZED_CLOSE_DIFFERENCES  | em_qfq           |                     3 |            0         | False                      |
| CN       |   603297 | 永新光学   | LOCALIZED_CLOSE_DIFFERENCES  | sina_qfq         |                     1 |            0         | False                      |
| CN       |   603596 | 伯特利    | LOCALIZED_CLOSE_DIFFERENCES  | sina_qfq         |                     4 |            0         | False                      |
| CN       |   603786 | 科博达    | LOCALIZED_CLOSE_DIFFERENCES  | sina_qfq         |                     4 |            0         | False                      |
| CN       |   603986 | 兆易创新   | LOCALIZED_CLOSE_DIFFERENCES  | sina_qfq         |                     1 |            0         | False                      |
| CN       |   688008 | 澜起科技   | LOCALIZED_CLOSE_DIFFERENCES  | sina_qfq         |                     7 |            0         | False                      |
| CN       |   688449 | 联芸科技   | LOCALIZED_CLOSE_DIFFERENCES  | em_qfq           |                     1 |            0         | False                      |
| CN       |   688525 | 佰维存储   | LOCALIZED_CLOSE_DIFFERENCES  | em_qfq           |                     1 |            0         | False                      |
| CN       |   688668 | 鼎通科技   | PERSISTENT_CLOSE_DIFFERENCES | em_qfq           |                  1399 |           -0.172324  | False                      |
| CN       |   688766 | 普冉股份   | LOCALIZED_CLOSE_DIFFERENCES  | em_qfq           |                     1 |            0         | False                      |
| HK       |    02510 | 禾赛     | PERSISTENT_CLOSE_DIFFERENCES | sina_qfq         |                   452 |           -1.67438   | False                      |
| HK       |     2498 | 速腾聚创   | MATCH_QFQ_CLOSE              | sina_qfq         |                     0 |            0         | False                      |

## 方法与限制

以 (market,symbol,date) 对齐缓存与参考，OHLC 和 close 分别检查，价格差超过 0.010001 才计差异。OHLC 全历史一致性与收盘价窗口位置影响分开报告。位置以缓存最后 60/120/360 个报价日期重索引参考；缺失参考或历史不足不计算，不用交集压缩窗口。局部差异阈值为 max(5, floor(共同报价行数×0.5%))，只用于描述差异范围。

复权审核不等于交易日完整性审核；窗口保留缓存报价日期，此处位置差用于隔离价格口径影响。股票此前的市场日期并集可靠性检查继续独立成立。部分参考前复权与不复权完全一致时，无法靠价格识别模式。没有除权公告或历史因子快照的证据时，不将差异断言为具体公司行为或旧基准拼接。

脚本：scripts/audit_stock_price_basis.py 与 scripts/summarize_stock_price_basis.py。机器证据：audit.json、reconciled.json/CSV、references/*.parquet（哈希在 audit.json）。

建议先统一每个市场的目标复权定义；对 persistent 与局部差异逐只全历史修复，再在抓取时保存 source/adjust/adjustment_anchor 与重标定证据。当前不应把全池 price_basis_verified 改成 true。

## 标的身份核对补充

HK 02510 被配置为禾赛，但实际为德翔海运；禾赛应为 HK 02525。本审计58条按配置代码核对，该条不能当作禾赛价格验证结果。正确禾赛历史尚未核对；日报正文排除错配条目，原配置与缓存保留供追溯。

依据：[德翔海运中期报告](https://www.hkexnews.hk/listedco/listconews/sehk/2025/0904/2025090400550_c.pdf)、[禾赛港交所公告](https://www.hkexnews.hk/listedco/listconews/sehk/2025/1024/2025102400595_c.pdf)。
