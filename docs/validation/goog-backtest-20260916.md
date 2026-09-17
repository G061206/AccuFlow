# GOOG 真实历史行情回测记录：2026-09-16

通过用户已登录的本地 IB Gateway（127.0.0.1:4002）实际下载，未使用合成行情替代。本次为收盘信号事件研究，不是交易执行或组合收益回测。

| 项目 | 结果 |
|---|---|
| 目标区间 | 2025-09-15 至 2026-09-15，252 个交易日 |
| 预热行情起点 | 2025-06-11，额外 65 个交易日 |
| GOOG 合约 | NASDAQ，conId 208813720 |
| SPY 参照合约 | ARCA，conId 756733 |
| 一分钟行情 | 两者各 123,090 条，预期覆盖率均 100% |
| 历史日线 | 两者各 501 条 |
| 数据门槛通过 | 252/252 |
| 规则 | unified-v1-m2.1，未调整阈值 |
| 首次异动信号 | 0 |
| 最高分 | 25.00；观察阈值 55，新异动阈值 65 |
| 回放核验 | 257 个快照全部一致，含 5 个预热检查点；时间截断及状态链通过 |
| 工程测试 | 后端 75 项通过；真实行情运行与复算另行完成 |

## 零信号原因

252 日中，223 日没有满足规则的支撑恢复，27 日仅一次，2 日两次。两次恢复分别发生于 2026-03-06 与 2026-05-22，成交量历史百分位仅为 20% 与 36.67%。另有 46 日达到第 80 百分位的放量门槛，但没有同时出现两次恢复。

因此分钟量价核心条件全年没有成立，核心行为、价格响应及跨日持续性没有贡献分数。最高分来自相对强弱 15 分及成交分布 10 分。没有调低门槛来制造信号。

零信号意味着没有样本可计算信号后的 1/5/10 日收益或胜率，不代表零收益，更不代表 GOOG 没有机构吸筹。此次不能证明当前规则的预测能力。

## 数据核验与范围

用户提供的 Gateway API 设置截图显示两个美股按手数发送开关均未勾选，单位为股，未人工乘除倍率。日线与分钟线均来自 IBKR RTH TRADES，拆股调整而不调整分红。

GOOG 的日线与分钟聚合开高低完全一致；SPY 在 2025-06-12、2025-07-23、2025-10-30 的高低价分别存在 0.03、0.05、0.03 美元差异，已审阅并保留。两者分钟合计成交量与日线最大相对差分别约 0.000110% 和 0.000020%。日线与末分钟收盘价最大相对差分别为 0.239852% 和 0.125289%，各自按原规则及收益统计的用途保留，没有强行对齐。

历史逐笔成交和同步买卖报价未参与；历史事件背景未逐日核验，因此不允许升级较强迹象。数据是目前下载的历史修订版本，不能宣称当时实时可得。仅评估收盘检查点，价格表现从信号后下一交易日开盘起算，不含分红、手续费、滑点与仓位。

## 本地证据

大体量行情、凭据和快照不纳入 Git。完整中文报告和图表位于 `data/backtests/goog/run-20260916/`，包括 `report-zh.md`、`price-and-score.png`、`daily.csv`、`daily.json`、`summary.json`、`snapshots.jsonl.gz`、`diagnostics.json`、`replay-verification.json`。

来源清单、核验过程及行情缓存位于 `data/backtests/goog/` 的 `download-manifest.json`、`data-audit.json`、`unit-evidence.json`、`review.json`、`history.db`。

- 规则校验和：`1b048336bd13c40a4d9c512bbdc5faa3f9a05f2531c668e8109c88df034949fc`
- 行情规范化行校验和：`9de68ec53a8fa0a170b1f6c11df4963c8c71e8256b8153246ce1debbdd71fcc8`
- 快照归档校验和：`b57dc8f8c95988194398befa17eb951eb40b8a3a3a8b0aeed5053a759548d4aa`

官方口径：[成交量单位](https://www.interactivebrokers.com/docs/tws-api/doc/market-data-historical/historical-data-limitations/historical-volume-scaling)、[TRADES 调整](https://www.interactivebrokers.com/docs/tws-api/doc/market-data-historical/historical-bar-what-to-show/trades)。
