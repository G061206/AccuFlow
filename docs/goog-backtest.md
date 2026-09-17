# GOOG 最近一年历史回测

新增 `accuflow backtest`，复用冻结的 `unified-v1-m2.1` 检测器，不调整阈值。它是收盘信号事件研究，不是含仓位、止损与交易成本的组合收益回测。

## 数据和运行

- 默认区间：最近一个已完成交易日向前一年；之前增加 65 个交易日的日线和一分钟线作为预热。
- 标的 GOOG，参照 SPY，均使用 IBKR 常规交易时段的 TRADES 历史数据。
- 下载需要已登录并开启 API 的 Gateway/TWS，且账户具有所需历史行情权限。仅执行只读行情请求。
- 每个标的按最多五个交易日的有界批次串行下载一分钟线（每次约 2,000 条），成功后立即缓存；重跑跳过覆盖率已合格的日期。遇到空数据、权限或网络异常停止，保留缓存。
- `--download` 只能写入带回测标记的专用数据库；拒绝迁移或写入普通业务数据库。不启动 API、调度器和邮件服务。
- 不自动认可成交量单位、复权或历史事件背景。未核验时保留数据不足或分项限制。

```powershell
.\.venv\Scripts\python.exe -m accuflow.cli backtest --symbol GOOG --download --host 127.0.0.1 --port 4002 --cache data/backtests/goog/history.db --review data/backtests/goog/review.json --output data/backtests/goog/run-001
```

每次运行使用新的输出目录。已有完整缓存时去掉 `--download`。可用 `--start YYYY-MM-DD --end YYYY-MM-DD` 固定区间；一次最多一年。`--client-id` 默认 71。

核验 JSON 示例（只有完成核验后才将对应字段改为 true）：

```json
{
  "units_verified": false,
  "adjustment_verified": false,
  "verification_notes": "",
  "events": {}
}
```

历史事件记录按交易日填写 `events[YYYY-MM-DD]`，字段为 `known_at`、`review_through`（均含时区）、`major_event`、`notes`。事件信息的已知时间不得晚于该日收盘。没有真实历史事件核验时不补造记录，较强状态被禁止升级。

IBKR 的 TRADES 历史口径和数据限制须在运行前核验，特别是拆股、分红和成交量单位。IBKR 的 [Historical Volume Scaling](https://www.interactivebrokers.com/docs/tws-api/doc/market-data-historical/historical-data-limitations/historical-volume-scaling) 说明，美股历史成交量受 API 的按手数发送设置控制，未勾选时为股数。下载后还需核对日线与分钟合计成交量、高低价的一致性。TRADES 调整拆股但不调整分红，收益统计只报告价格变动。本程序不会凭未核实的假设自动勾选核验。

## 计算口径

1. 用预热末段五个收盘检查点初始化跨日状态，再逐日处理目标区间；较早的异动阶段没有完整重建。
2. 每个快照只包含该日收盘前已结束的分钟线及此前完整日线。未来行情只用于独立的后续表现统计，不能进入评分。
3. 每段异动第一次出现“新异动”或“持续迹象增强”计一个样本，后续增强不重复计数。预热期已触发的阶段不作为区间内新样本。
4. 因为信号在收盘后可得，统一使用下一交易日开盘价起算，统计之后第 1、5、10 个交易日收盘价格变化，以及同期 SPY 价格变化之差、窗口内最大有利和不利幅度。
5. 缺少窗口内任一标的日线，该期限样本不可用；未来窗口尚未结束则标为 `right_censored`。不跳过缺失交易日拼接收益，不把无样本写成零收益。
6. 同时输出所有可评估日期的后续表现，作为描述性比较。不同窗口可能重叠，样本不独立；不输出未经验证的显著性或“主力身份准确率”。

## 输出

- `summary.json`：版本、区间、有效日期数、信号数、分期限统计、核验信息与限制。
- `daily.json`：每日分数、状态、事件、质量原因、后续价格表现。
- `snapshots.jsonl.gz`：包括预热期在内的完整输入和输出，可用冻结检测器逐条复算；摘要保存压缩文件 SHA-256。
- `report.md`：可读摘要。

真实数据不可用时输出 `status=blocked`，不生成模拟 GOOG 收益。质量门槛未全部满足时为 `needs_review`。`completed` 只表示本次收盘事件研究完成，不表示逐笔模型、实时链路或策略盈利能力通过验证。

当前历史逐笔与同步报价未接入本回测，核心证据仅覆盖分钟量价路径。历史行情是现在下载的修订版本，不是当时实时存档，因此仅保证按历史时间切片，不能宣称完全复现当时可得数据。统计不包含分红、手续费、滑点、仓位与可成交性。

## 工程验证

测试使用明确标注的合成 fixtures，验证未来行情隔离、次日开盘起算、缺失/未成熟窗口、核验门槛、逐日状态、快照回放以及业务库保护；不作为 GOOG 实测结果。

## 首次真实运行

2026-09-16 已完成 GOOG 最近一年真实 IBKR 历史行情回测。252 日均可评估，首次异动 0 次，最高 25 分；257 个快照全部复算一致。零样本无法证明预测有效性。详见 [真实回测记录](validation/goog-backtest-20260916.md)。
