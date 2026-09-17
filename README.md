# AccuFlow

AccuFlow 是一个面向美股的主力积累迹象监测工具。项目以 2C2G VPS 为统一运行目标，使用 `ib_async + IBKR API` 作为唯一行情来源。

当前包含：

- 统一检测机制与开发准备文档
- 可交互 Web 控制台
- 跟踪股票管理、历次报告、运行状态与设置页面
- FastAPI + SQLite 后端和版本化 schema
- `ib_async` 只读连接、合约解析与历史 bars 补数

## Web 控制台

```bash
cd web-console
npm install
npm run dev
```

当前运行规则见 [v1 检测机制](docs/detection-mechanism-v1.md)。新版设计见 [v2：披露前的持续买方需求](docs/detection-mechanism-v2.md)：采用连续量价证据与跨日累积，按季度内提前预警、提醒负担和简单基线对照验收。v2 已实现可运行的收盘研究原型，暂不使用 13F，使用方式见 [v2 实现说明](docs/v2-implementation.md)；Web 与自动监测仍运行 v1，新模型效果尚未验证。

## 后端 API

Python 3.11 及以上：

```bash
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
cp .env.example .env
.venv/bin/accuflow serve
```

Windows PowerShell 对应命令为 `.venv\Scripts\python.exe -m pip install -e ".[dev]"` 和 `.venv\Scripts\accuflow.exe serve`。

默认监听 `127.0.0.1:8000`，SQLite 写入 `data/accuflow.db`。本地开发时 Vite 会把 `/api` 代理到该地址。

真实连接前请启动 IB Gateway 或 TWS，启用 Socket API，并在 `.env` 中确认端口。默认值 `4002` 对应常见的 IB Gateway paper 会话；所有连接固定使用 `readonly=True`，项目没有订单接口。

首次启动会把 `ACCUFLOW_INITIAL_SYMBOLS` 写入 SQLite，目前默认为 `AAPL,NVDA`。初始化标记也会持久化，因此用户之后删除股票不会在服务重启时被自动恢复。

当前 API 支持：

- `/api/stocks`：跟踪股票增删、暂停和恢复
- `/api/reports`：确定性报告写入、查询和详情
- `/api/ibkr/connect`：建立 IBKR 只读会话
- `/api/stocks/{symbol}/qualify`：解析并保存 IBKR `conId`
- `/api/stocks/{symbol}/backfill`：串行补充日线和 1 分钟 `TRADES` bars
- `/api/stocks/{symbol}/probe`：探测快照、历史 bars、历史逐笔和实时 `Last/BidAsk` 能力
- `/api/ibkr/capabilities`：查询机器可读的历次能力报告
- `/api/reports/generate`：基于已存储 IBKR 数据生成统一检测报告
- `/api/health`：SQLite 和 IBKR 连接状态


## M2 统一检测与通知预览

M2 已完成工程闭环：IBKR 历史行情 → 完整窗口与质量检查 → 五个固定证据族 → 单一评分 → 跨日 episode → 检测报告与本地事件预览 → 不可变快照回放。规则版本为 `unified-v1-m2.1`。验收范围与阈值说明见 [M2 验收记录](docs/m2-acceptance.md)。

在「设置」开启「自动质量监测」后，服务按 XNYS 交易日历连接 IBKR、补数并运行检查点；默认关闭。小时检查点在完整小时后 2 分钟执行，收盘在结束后 20 分钟执行，支持提前收盘。未开启时可手动同步行情并生成检测报告。

从股票的「更多操作 → 检测详情与配置」查看单一评分、固定权重贡献、最近五个交易日、反证和回放；仅在核验后勾选成交量单位和复权口径。事件背景按核验时间记录，缺失或存在重大事件时禁止升级较强迹象。大盘默认 SPY，行业参照可选；参照股票需加入跟踪并同步数据。缺少逐笔、参照或 WAP 时，相关分项不可用且不重新分配权重。

历史分钟补数覆盖最近 20 个完整交易日和当前交易日，逐日串行保存、支持断点续传。检测读取最多 66 个交易日的本地数据，至少需 20 个相同时段合格基线、60 个完整日线；目标分钟基线为 60 日。当前持续 Last/BidAsk 采集仍需完成 M1 实盘接入；M2 提供有界 `SignalService.record_observations` 入口，只有实时双流、单位与方向质量合格且积累足够同口径基线，逐笔分项才参与。分钟历史不会伪造主动买卖方向。

检测快照包含实际输入行情、参照、上下文、先前状态、跨日记录和规则校验和，按股票压缩保存。报告、快照、日级记录、状态、事件和通知预览同事务提交。检查点幂等；同日多次检测不累计日期；数据缺失保留原 episode；过期盘中事件不进入新通知预览。旧质量报告仍可回放，带快照报告不能覆盖。

API：

- `GET/PATCH /api/settings`：持久化监测、日报和小时事件预览偏好。
- `GET /api/workflow/jobs`：任务状态、尝试次数和失败原因。
- `GET /api/notifications/outbox`：日报及信号通知的本地预览，状态始终为 `preview`。
- `GET /api/signals?symbol=AAPL`：最近检测、质量、证据、跨日状态。
- `GET /api/signals/{snapshot_id}/replay`：重算特征、评分、状态与事件，返回 `matches`。
- `GET/POST /api/stocks/{symbol}/analysis-context`：读取/保存人工核验记录；服务端记录可知时间，不允许回填历史可知时间。
- `GET /api/reports/{report_id}/replay`：同时回放检测器和确定性报告。

小时异动和收盘日报可独立开启本地预览。M3 已提供 TLS SMTP 投递和确定性邮件模板，默认仍为预览；整日真实环境验收待完成。M2 工程测试通过不代表 M1 真实订阅验收完成，也不代表规则效果已通过前向验证。

按单 Python 服务运行，不能使用多个 Uvicorn worker 共享同一 IBKR clientId。静态 Worker 只负责网页资源，生产部署需另配 `/api` 到 FastAPI 的反向代理。任务租约、中断恢复和状态提交校验防止重复写入；单次报告最多 100 只股票，检测逐只运行，原始输入压缩后才进入批次缓存。

## 验证

```bash
python -m pytest
cd web-console
npm run build
npm run test:sites
# 使用已启动的本地前端（4173 端口）运行交互回归
npx playwright test tests/visual-qa.spec.js tests/regressions.spec.js tests/m2.spec.js tests/m3.spec.js
```

## M3 邮件与资源验收

实现说明与验收状态见 [M3 验收记录](docs/m3-acceptance.md)。新通知使用独立 `delivery_outbox`，保留 M2 历史预览。收盘日报包含正常异动、无新异动、数据不足、运行失败四种结果；小时事件按检查点合并，沿用 M2 冷却和去重。盘中手动生成收盘报告只保存预览。

默认 `ACCUFLOW_SMTP_ENABLED=false`。准备真实发送时，在部署主机的私有环境中配置 `.env.example` 所列 SMTP 主机、端口、TLS 类型、发件人与收件人；密码使用环境变量。`starttls` 通常用于 587，`ssl` 用于提供隐式 TLS 的端口。代码不支持明文 SMTP。不要把密码提交到仓库。

启用后只处理新建的 pending 通知，历史 preview 不会被自动补发。每封邮件的收件人、内容、Date、Message-ID 和 MIME 字节被冻结。SMTP 明确临时拒收或提交前连接失败有限退避；DATA 开始后断线转为 uncertain，不自动重试。控制台先核对服务器日志/邮箱，再选择已接收或取消。服务器已接收不等于最终到达收件箱。

运行状态页显示队列、HTML/纯文本预览、CPU、RSS、Gateway 进程数和事件循环延迟。API：

- `GET /api/notifications/deliveries`：新投递队列，最多 100 条。
- `GET /api/notifications/deliveries/{id}/content`：冻结的双格式内容。
- `POST /api/notifications/deliveries/{id}/resolve`：仅允许处理 uncertain，resolution 为 confirmed_sent 或 cancelled；不触发重发。
- `GET /api/metrics`：最近资源观测的摘要，不等于整日实盘验收通过。

目标主机可运行有界资源采样（命令不启用 SMTP，也不修改自动监测开关）：

```text
accuflow soak --seconds 25200 --interval 30 --output data/session-acceptance.json
```

`--replay-latest` 选项额外重复计算最新固定快照，用于隔离负载验证。输出 `.jsonl` 样本与 `.json` 摘要。回放负载不是实时行情压力测试；正式验收需在 Gateway/TWS、持续行情采集与检查点任务实际运行时覆盖整个交易日，并核查覆盖率、断线恢复和队列延迟。输出路径必须是新的，避免覆盖以往记录。

## GOOG 历史信号回测

新增 `accuflow backtest`：使用 GOOG 与 SPY 的 IBKR 历史日线和一分钟线，按最近一年逐日收盘运行固定规则，统计每段异动首次信号后 1、5、10 个交易日的价格表现。需要额外 65 个交易日预热及单位、复权核验；无真实数据不会生成替代结果。使用独立缓存和输出目录，不发送邮件。运行方法、数据限制与统计口径见 [GOOG 回测说明](docs/goog-backtest.md)。

## V2 收盘研究模型

`accuflow backtest --model v2` 使用连续量价证据和 5/10/20 日窗口；`accuflow replay-v2 --input <输出目录>` 从原始行情重建并重放。默认阈值 65 为未校准实验值，缺失历史会标为不可评分。该入口使用隔离缓存，不发送邮件。详细命令、输出和当前边界见 [v2 实现说明](docs/v2-implementation.md)。
