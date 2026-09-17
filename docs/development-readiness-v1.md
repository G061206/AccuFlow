# AccuFlow 开发准备方案 v1

> 2026-09-17 更新：识别模型的后续设计以 [v2 设计](detection-mechanism-v2.md) 为准。本文保留为 v1 历史设计与实现依据；Web 与自动监测仍为 v1；v2 已有独立收盘研究原型，识别效果尚未验证。

日期：2026-09-12。本文把 `detection-mechanism-v1.md` 转换为可执行工程范围。目标环境固定为 2 vCPU、2 GB RAM；系统只有一个运行模式和一个检测流程。

## 1. 已冻结的技术约束

- Python 3.11+。
- 行情唯一来自 IBKR API。
- Python 连接库固定使用 [`ib_async`](https://github.com/ib-api-reloaded/ib_async)，不安装官方 `ibapi`，不实现其他行情 provider。
- 运行前需要可连接的 IB Gateway，或启用 API 的 TWS。
- 只读连接，首版不实现任何自动交易或订单接口。
- SQLite 保存配置、任务、状态、事件和 outbox；原始行情按日分区保存。
- 邮件是第一个通知渠道，但检测与通知适配器解耦。
- 所有股票使用同一评分、状态机和报告格式；字段覆盖不同只体现在 evidence coverage 和质量报告中。

## 2. 首个可交付闭环

1. 用 `ib_async.connectAsync(..., readonly=True)` 建立连接并生成能力报告。
2. 解析并固化股票合约的 conId，补齐日线和 1 分钟 TRADES bars。
3. 在 IBKR 实际额度内订阅 `Last/AllLast` 与 `BidAsk`，持续写入原始分区。
4. 按美股交易日历生成每小时检查点与收盘任务。
5. 对每个检查点完成质量检查、统一特征、评分与状态迁移。
6. 事件与邮件 outbox 在同一 SQLite 事务中保存。
7. 生成确定性邮件和收盘日报；默认 dry-run 预览，确认后启用 SMTP。
8. 同一数据快照和规则版本可以离线重放出同一结果。

逐笔相关分项可以在第一版中采集和计算，但在覆盖率或历史基线未达门槛时不参与信号升级，不能因此切换到另一档或另一套分数。

## 3. 2C2G 资源策略

`ib_async` 仓库建议批量加载数据时为 Gateway 配置更大的 Java 内存，这高于本项目整机预算。因此 AccuFlow 不采用批量示例的运行方式：

- 单个 Python 主进程承载连接、调度、写入和检测。
- 历史补数按股票、日期分片，并发默认为 1。
- `ib_async` 回调立即转成最小领域对象，进入有界队列；不长期保留 `Ticker.tickByTicks` 的无限列表。
- 一个 SQLite 写入协程串行提交批次，避免锁竞争和额外服务。
- 检测按股票顺序运行，一次只装载一个股票所需的有限历史窗口。
- 分位数和跨日特征保存日级摘要，不在每个整点重扫全部逐笔历史。
- 逐笔按固定条数或固定秒数刷盘；内存仅保留报价匹配窗口和当前 5 分钟聚合。
- 图表、Parquet 压缩和收盘日报使用短生命周期子进程，完成后释放内存。
- 不引入 PostgreSQL、Redis、Kafka、Celery 或常驻 Web 前端。
- 所有队列、并发、重试、图表数和单次查询范围必须有硬上限。

资源验收以实测为准：IB Gateway/TWS 与 AccuFlow 在同一 2C2G 主机运行完整交易日，RSS 不持续增长，整点任务不堆积，收盘报表不阻塞行情写入，断线恢复不重复发信号。若该验收失败，应先缩小自选股和逐笔订阅数量或优化实现，不能新增另一套产品模式掩盖问题。

## 4. 建议的代码边界

```text
src/accuflow/
  config/                 配置加载、校验和密钥引用
  domain/                 Bar、Quote、Trade、SignalEvent、质量对象
  providers/ibkr_async/   唯一行情入口、能力探测、限流和重连
  providers/events/       IBKR 事件能力及人工事件表；不提供行情
  storage/                SQLite schema、仓储、原始分区与证据快照
  calendar/               交易日、提前收盘和检查点
  ingestion/              补数、实时队列、单位与会话标准化
  quality/                完整度、延迟、断线和口径检查
  features/               量价、成交方向、承接、跨日和基线特征
  detectors/unified/      一个检测流程
  scoring/                一个版本化规则分数、证据族和反证
  state/                  一个 episode 状态机和幂等
  notifications/          策略、模板、EmailChannel
  reports/                收盘日报
  replay/                 固定快照重放
  cli/                    probe、backfill、run、replay、report
tests/
```

检测器只接收标准化快照，不直接调用 `ib_async` 或邮件。行情、检测、状态和通知分别测试。

## 5. `ib_async` 接入原则

- 使用异步高层接口，不直接依赖内部 socket client。
- 建连使用 `connectAsync`，并设置唯一 clientId、超时和只读模式。
- 合约先经 `qualifyContractsAsync`，后续以 conId 为主键。
- 历史 bars 使用 `reqHistoricalDataAsync`，显式指定 `TRADES`、RTH、时区和超时。
- 实时逐笔使用 `reqTickByTickData`，分别订阅成交和 `BidAsk`，退出或换订阅时显式取消。
- 订阅返回的 Ticker 只作为事件入口；每次消费后主动清理累计 tick 列表，防止全天增长。
- 记录 `apiError`、断开、throttle start/end、IBKR farm 状态和服务端错误码。
- `ib_async` 自带客户端请求节流不等于覆盖所有 IBKR 接口限制；应用层仍按请求类型维护额度、截止时间和退避。
- 接入层保持薄封装，库升级时通过 contract、bar、tick 和错误码契约测试验证兼容性。

## 6. 里程碑

### M0：工程骨架与确定性领域模型

- Python 包、依赖锁定、配置模型、结构化日志和 SQLite migrations。
- 交易时钟、checkpoint、SignalEvent、QualityReport、FeatureSnapshot 和 job_key。
- fake/replay provider，仅用于测试，不是第二行情源。
- 单元测试覆盖时区、提前收盘、窗口边界和重启幂等。

验收：固定 fixture 可从行情快照运行到事件/outbox，重复运行不会产生重复记录。

### M1：`ib_async` 能力探测与采集

- 只读连接、合约解析、服务器时间、行情类型和权限错误采集。
- 日线、1 分钟 bars、成交与 BidAsk 探测。
- 请求调度、有界队列、有限重试、断线恢复和增量补数。
- 生成人可读及机器可读的 capability report。

验收：逐项输出可用性、延迟、单位、覆盖和失败原因，不把无权限或延迟数据当实时数据。

### M2：统一检测闭环

- 5 分钟和小时聚合、同时间段基线、成交方向、压力持续性、支撑承接、相对强弱和成交重心。
- 统一质量门槛、统一评分、支持证据和反证。
- 跨日记录与 episode 状态机。
- 规则回放和坏数据 fixtures。

验收：字段缺失只影响对应证据；同日多次检测只算一个日期；缺失数据不产生假信号；重放结果一致。

### M3：邮件、日报与整日压测

- outbox、固定 Message-ID、冷却、合并和不确定投递状态。
- 确定性 HTML/纯文本模板及收盘日报。
- 2C2G 上与 Gateway/TWS 同机运行整日，记录 CPU、RSS、队列水位、任务延迟和磁盘增长。

验收：无异动、数据不足、运行失败和正常异动四条路径均有输出；资源满足第 3 节要求。

## 7. 开工前所需输入

不需要在聊天中提供账户密码。真实接入前需要：

1. 首批股票、预计最大数量，以及逐笔订阅不足时的优先顺序。
2. 使用 IB Gateway 还是 TWS，paper 还是真实账户会话。
3. IBKR 账户已有的美股行情订阅。
4. 2C2G VPS 的操作系统和是否具备 Gateway/TWS 所需的登录方式。
5. 后续使用的发件方式和收件邮箱；M0 阶段可以暂不提供。

这些信息未齐时仍可直接开发 M0；M1 的真实能力报告必须等待可连接的 IBKR 会话。


## 8. 2026-09-15 实现进度

M2 统一检测闭环已实现并完成工程验收：完整 5 分钟/小时聚合、同时间段基线、保守成交方向、压力持续、条件卖压冲击、冻结支撑与恢复、相对强弱、累计 VWAP；统一质量门槛、五族固定权重与反证；跨日记录、episode、通知去重；不可变快照、重启回放；API 与控制台详情和核验配置。

具体规则、验证命令和证据映射见 [M2 验收记录](m2-acceptance.md)。真实行情不会因历史基线数量通过就自动获得实时双流资格；缺失字段的权重不重新分配。

仍需完成 M1 真实 Last/BidAsk 持续采集、单位/权限/断线恢复及整日覆盖验收；M2 逐笔路径已实现标准化批次入口和固定行情测试，但未把短时能力探测当作全天采集。M3 已实现 SMTP 投递状态与邮件模板，真实投递及 2C2G 同机整日验收待完成。规则回测与前向有效性评估另行推进。


M3 已实现 outbox/固定 Message-ID/SMTP 不确定投递状态、HTML+纯文本日报、资源采样和控制台，详见 [M3 验收记录](m3-acceptance.md)。目标 2C2G 主机已完成隔离环境回归；真实邮件到达和 Gateway/TWS 同机整日运行尚待外部配置与实测，不标记为已完成。
