# 衍生品观察

2026-10-28。首期仅注册IC中证500股指期货。配置真源 `config/derivatives.yaml`，来源真源 `config/observation_sources.yaml` 的 `derivatives`。日报01顺序：核心宽基 → 衍生品 → 风格参照 → ETF分类概览。

## 产品与报价

[中金所官方合约页](http://www.cffex.com.cn/zz500/)确认：品种代码IC、标的中证500指数、每点200元、指数点报价；挂牌当月、下月及随后两个季月。日间交易9:30—11:30、13:00—15:00。本模块为市场事实观察，不交易、不输出仓位或保证金收益。

IC是品种而非单一到期合约。表格保留官方目标日日文件的四个具体合约，不按活跃程度隐藏其他合约，不生成IC0或主力连续价格。持仓最多只表示目标日持仓量达到最大值；换月不跨合约计算收益。新挂牌合约缺少自身前收盘时，当日涨跌保持未知。

## 来源与原始数据

正式源：`http://www.cffex.com.cn/sj/hqsj/rtj/{YYYYMM}/{DD}/{YYYYMMDD}_1.csv`，中金所官网公开按日期发布的GB编码CSV。20261027测试中HTTPS连接失败，官方HTTP成功；同日文件与官方月包 `http://www.cffex.com.cn/sj/historysj/202610/zip/202610.zip` 对应成员逐字节一致。HTTP为当前明示来源协议，未声称TLS认证；不传凭证。新浪具体合约接口仅作候选测试，不进入生产备用。

原始文件无逐行日期列，日期证据明确为交易所日期文件路径/文件名（`exchange_dated_filename`），不伪装为源端时间戳。每批保存目标日与前交易日CSV、请求URL、状态码、抓取时间和文件摘要；manifest摘要由同日事实JSON绑定。存储：`data/sources/cffex/raw/daily/runs/{trade_date}/{run_id}/`；日期引用在 `partitions/{trade_date}.json`。

日期以已有CN有界独立规则日历为代理，明确 `CN_independent_rule_model_proxy_not_CFFEX_certified`，不是独立中金所全历史认证。非交易日、尚未收盘拒绝构建；原始获取时间早于目标日收盘时保持不可用。新日缺失不能取旧日文件重命名。

## 指标

- 收盘、结算、前结算分别保留，不能互相填充。
- 当日涨跌=`同一合约 close[t]/close[t-1]-1`；分母为前收盘，不是前结算。
- `close_vs_pre_settle`另存为`close/pre_settle-1`，不混入上述当日涨跌。
- 成交量与持仓量按合约手数；成交金额原样保留于CSV，不转换未核验单位。
- 以同批已验证csi500指数收盘为现货：基差点数=`future.close-spot.close`；基差率=`future.close/spot.close-1`。期货CFFEX与现货长桥的来源/快照身份分别记录。基差不作分红调整或年化，不直接归因为市场情绪。
- 现货目标日缺失时基差未知，不抹除已验证的期货自身报价。

## 门控与发布

规则 `listed-derivative-facts-v1`。检查CSV字段、重复表头、具体合约唯一性、四合约覆盖、代码月份、非有限/非正价格、原始OHLC关系、非负整数成交/持仓及前后日持仓变化一致性。前结算必须与该合约前日结算一致。异常不修复、不前填；失败时保留IC固定不可用行，禁止用连续合约兜底。

`observation build`在线获取、离线只消费精确目标日引用。输出 `derivatives_{date}.json/.parquet/.csv`；JSON记录注册签名、输入回执、现货快照引用及全部合约事实。`observation check`从不可变CSV重算，核对事实/回执/HTML/Markdown及面板顺序；摘要或身份篡改拒绝发布。模块不可用进入PARTIAL；`--require-complete`拒绝不完整发布。单日展示不改ETF/指数横截面或策略资格。
