"""
Backtest — 交易层 + 组合层模拟。

分层职责：
  trade/        Trade Simulation（v0.5.2）— 一笔交易好不好
                  strategy/（入场/退出） execution/（T+1 开盘） trades.py metrics.py
  portfolio/    Portfolio Simulation（v0.6）— 整个账户好不好
                  engine/（账户引擎） allocation/（仓位分配） nav/（NAV+绩效+基准） metrics/（报告）
  sensitivity.py / matrix.py  — 交易层稳健性扫描与四组对比
"""
