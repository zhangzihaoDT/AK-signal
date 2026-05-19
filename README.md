# A 股技术趋势监控（极简版）

## 项目状态

- v0.1 数据链路跑通 ✅
- 状态总结：AKShare 拉取 → raw 缓存 → 指标计算 → 趋势评分 → HTML/CSV 报告
- v0.2 相对强度与状态变化 ✅
- 状态总结：benchmark(沪深300) → relative_strength_20d → change → reason 增强 → HTML/CSV 报告
- AKSignal v0.3 ✅
- 状态总结：信号输出 → 趋势观察与行动系统（watch_level / action / portfolio_summary / watchlist）

第一版仅监控：

- 寒武纪（688256）
- 中际旭创（300308）
- 科大讯飞（002230）
- 长鑫存储（TBD，待确认代码或替代标的）

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 运行

```bash
python src/main.py
```

或：

```bash
.venv/bin/python src/main.py
```

可选参数示例：

```bash
python src/main.py --start-date 20200101 --adjust qfq --plot-last-n 240
```

## 输出

- 原始数据：`data/raw/{symbol}.csv`
- 指标数据：`data/processed/{symbol}.csv`
- 报告：`data/reports/trend_report_YYYYMMDD.html` 与 `trend_report_YYYYMMDD.csv`
