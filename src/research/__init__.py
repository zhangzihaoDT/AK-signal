"""
v0.5 Research — 历史信号研究链路（与 daily pipeline 分离）。

职责：用共享规则函数重放/生成历史信号，并验证其与正式产物的一致性，
供后续事件研究与交易模拟（backtest，v0.5.2）消费。

子包：
  replay      历史信号重放（单日期 / 区间）
  signals     historical_signals 产物契约（schema / config_hash）
  validation  重放与正式产物的 parity 校验
"""
