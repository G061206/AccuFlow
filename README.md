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

详细检测规则见 [`docs/detection-mechanism-v1.md`](docs/detection-mechanism-v1.md)。

## 后端 API

Python 3.10 及以上：

```bash
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
cp .env.example .env
.venv/bin/accuflow serve
```

Windows PowerShell 对应命令为 `.venv\Scripts\python.exe -m pip install -e ".[dev]"` 和 `.venv\Scripts\accuflow.exe serve`。

默认监听 `127.0.0.1:8000`，SQLite 写入 `data/accuflow.db`。本地开发时 Vite 会把 `/api` 代理到该地址。

真实连接前请启动 IB Gateway 或 TWS，启用 Socket API，并在 `.env` 中确认端口。默认值 `4002` 对应常见的 IB Gateway paper 会话；所有连接固定使用 `readonly=True`，项目没有订单接口。

当前 API 支持：

- `/api/stocks`：跟踪股票增删、暂停和恢复
- `/api/reports`：确定性报告写入、查询和详情
- `/api/ibkr/connect`：建立 IBKR 只读会话
- `/api/stocks/{symbol}/qualify`：解析并保存 IBKR `conId`
- `/api/stocks/{symbol}/backfill`：串行补充日线和 1 分钟 `TRADES` bars
- `/api/health`：SQLite 和 IBKR 连接状态
