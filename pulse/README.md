# Pulse Console

直播数据监听与 AI 陪伴中台 —— 第一阶段「直播数据监听」的 Web 控制台。

## 快速开始

```bash
# 方式一：快捷脚本
启动Pulse控制台.bat

# 方式二：手动
.venv\Scripts\python -m pulse.server
# 浏览器打开 http://localhost:8765
```

## 端口

| 端口 | 用途 |
| --- | --- |
| 8765 | 静态前端页面（`pulse/static`） |
| 8766 | WebSocket（实时推送直播间事件 + start/stop 控制） |

## 数据流

```
dy_live/server.py (DouyinLive, on_live_event 结构化回调)
        │
        ▼
pulse/server.py (LiveHub：线程安全广播)
        │  WebSocket {type:"event", data:{...}}
        ▼
pulse/static/app.js (ingest → KPI / 趋势图 / 词云 / TOP10 / 时间线 / 弹幕流)
```

事件类型：`chat` / `gift` / `member` / `like` / `follow` / `room`。

## 说明

- 未配置 `.env` 的 `DY_COOKIES` 时，页面自动以「演示数据」运行，用于预览整套视觉与交互。
- 配置 `DY_COOKIES` 后重启，在右栏输入直播间链接/ID 点「一键监听」即可接入真实直播间。
- 顶部灵动岛：悬停展开数据波形 / 出现异常变红色警示条 / 点击展开 AI 控制台占位（阶段二、三解锁）。
- AI 分身、AI 伴侣为后续阶段模块，UI 已预留入口与设计语言。
