# AccuFlow

AccuFlow 是一个面向美股的主力积累迹象监测工具。项目以 2C2G VPS 为统一运行目标，使用 `ib_async + IBKR API` 作为唯一行情来源。

当前包含：

- 统一检测机制与开发准备文档
- 可交互 Web 控制台
- 跟踪股票管理、历次报告、运行状态与设置页面

## Web 控制台

```bash
cd web-console
npm install
npm run dev
```

详细检测规则见 [`docs/detection-mechanism-v1.md`](docs/detection-mechanism-v1.md)。
