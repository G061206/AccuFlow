# M3 开发与验收记录

M3 的通知队列、SMTP 适配器、固定双格式模板、资源采样和控制台已实现。真实 SMTP 到达验证与 Gateway/TWS 同机整日运行仍需要外部配置，尚不能标记整个 M3 实盘验收完成。

## 交付

| 项目 | 实现与验证 |
| --- | --- |
| 原子 outbox | migration 005、`storage/delivery.py`；与报告/事件同事务，失败注入验证回滚 |
| 固定邮件 | `notifications/templates.py`；固定 Message-ID、Date、MIME boundary、HTML 转义、纯文本替代；重试字节不变 |
| 四类日报 | 正常异动、无新异动、数据不足、运行失败均有明确内容；盘中收盘按钮强制仅预览 |
| 合并与冷却 | 同检查点事件合并；按 event_id 全局去重；沿用 M2 episode 两小时冷却、每日上限与失效例外 |
| TLS SMTP | `notifications/smtp.py`；证书验证、STARTTLS/隐式 TLS、全体 RCPT 接受后才进入 DATA；无明文回退 |
| 投递状态 | `services/delivery.py`；租约隔离、有限指数退避、发送不确定状态和人工处理审计 |
| 资源采样 | `services/telemetry.py`、`soak.py`；CPU/RSS、Gateway RSS、数据库/WAL 大小、磁盘余量、队列数量、任务耗时、事件循环延迟 |
| 控制台 | 新队列状态、沙箱 HTML 邮件与纯文本预览、不确定状态处理、实时资源信息 |

## 投递语义

默认 preview。启用 SMTP 后新记录进入 pending；旧 preview 不自动转为待发送。SMTP 参数通过私有部署环境设置，数据库只冻结 envelope 与非秘密连接配置指纹，不存 SMTP 密码。

状态流：pending/retry → preparing → sending → sent。preparing 只执行连接、TLS、认证及 MAIL/RCPT；持久化 sending 后才允许 DATA。提交前失败或明确的 4xx 拒收按 30、60、120…秒退避，最多尝试 5 次（可配置 1—10）；明确永久拒收进入 failed。DATA 后连接中断、任务取消、崩溃租约到期进入 uncertain，停止自动重发。人工可标记服务器已接收或取消，均记审计。相同 Message-ID 无法保证接收方去重，因此不依赖它消除不确定投递。

收件人部分拒绝时关闭会话，不向其余人发送半份通知。正文与内容校验和冻结；配置指纹改变时阻止静默切换服务器。小时通知超过原检查点 15 分钟尚未提交则取消。邮件发送独立于行情检查点；SMTP 网络操作在线程中执行，不阻塞事件循环。

实现依据：[Python SMTP 事务和异常文档](https://docs.python.org/3/library/smtplib.html)、[EmailMessage 双格式文档](https://docs.python.org/3/library/email.message.html)。服务器的接收确认不代表最终进入收件箱。

## 本地验证

- 本地后端 **66 项通过**（Python 3.14，2 条上游测试依赖弃用提示）；新增覆盖投递前/后失败边界、明确拒收、固定字节重试、预览不补发、租约、人工处理、队列回滚、四类日报、采样及盘中保护。
- Playwright 7 项通过，含 M2/M3 详情、偏好持久化、邮件沙箱与人工处理。
- 生产构建、4 项静态托管测试通过。
- 真实 FastAPI + 独立 SQLite：无行情股票 → 数据不足日报 → 邮件 HTML 预览 → 采样正常；未连接 SMTP。
- 实际运行 10 秒本地 soak，正确标为 needs_review，未冒充完整交易日。

## 目标主机隔离验收

用户提供并授权的 2C2G Ubuntu 主机，验收目录为 `/home/ubuntu/accuflow-m3-validation-20260915`。只上传 src、tests、pyproject.toml、README；不上传私钥、环境文件或业务数据库。独立 Python 虚拟环境，不改动已有服务，不开放 Web 端口。

主机初检为 2 核、约 1.9GB 内存、2GB swap；未观察到 Gateway/TWS。最终后端 **66 项通过**（Python 3.14.4）。固定快照负载设置 120 秒，实际样本覆盖 119.34 秒，19 次回放全部一致；峰值检测进程 RSS **236.05 MiB**，CPU 采样峰值 76.1%（按单核口径），单轮最慢 **2.87 秒**，事件循环最大延迟 **1.13 秒**。末尾任务队列无积压，仅保存 1 条通知预览。

原始摘要：[目标主机回放结果](validation/m3-vps-replay.json)。可审阅模板：[数据不足邮件 HTML 样例](validation/m3-mail-preview.html)。结果为 `needs_review`：不足一个完整交易日，且目标主机未运行 Gateway/TWS。不能据此宣称整日实时采集和 SMTP 验收完成。

## 整日验收入口与限制

`accuflow soak --seconds 25200 --interval 30 --output data/session-acceptance.json` 产生逐样本 JSONL 和汇总 JSON，不改变监测/发送偏好。`--replay-latest` 是合成/固定快照负载，不是实时采集证明。摘要检查观测时长、资源规格、Gateway 存在性、内存和事件循环延迟；即使资源检查通过也需要人工核查真实行情覆盖、任务及时性与投递记录。

采样默认每 30 秒，数据库保存 14 日，可配置上限 90 日。soak 单次最长 24 小时，最短采样 5 秒，输出文件不能覆盖旧记录。报告与采样均记录原始指标，不声称短测完成整个交易日。

尚缺：SMTP 主机、端口、发件人与收件人及本地私密凭据；目标主机上已登录并持续采集的 Gateway/TWS；覆盖完整交易日的实时资源与任务验收。收到并完成相应实测后，才能将 M3 总体验收改为完成。
