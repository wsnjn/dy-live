# -*- coding: utf-8 -*-
"""Pulse Console —— Web 控制台服务端。

职责：
    1. 8765 端口：提供静态前端页面（pulse/static）。
    2. 8766 端口：WebSocket 实时推送直播间结构化事件，并接收 start/stop 控制。

启动：
    .venv\\Scripts\\python -m pulse.server
    （或） python pulse/server.py
"""
import asyncio
import json
import os
import sys
import threading
import time
import urllib.parse
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
HTTP_PORT = 8765
WS_PORT = 8766

from dy_live.server import DouyinLive  # noqa: E402
import utils.common_util as common_util  # noqa: E402


# 认证：只在 .env 存在 DY_COOKIES 时才构建 Auth，避免触发扫码登录流程卡住启动。
def _try_load_auth():
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(_ROOT, ".env"))
        if not (os.getenv("DY_COOKIES") or "").strip():
            return None
        common_util.load_env()  # 设置 dy_auth + dy_live_auth
        return common_util.get_live_auth()
    except Exception:
        return None


class StoppableLive(DouyinLive):
    """带停止标志的直播监听器，避免 on_close 无限重连。"""

    def __init__(self, live_id, auth_, on_live_event=None):
        super().__init__(live_id, auth_, on_live_event=on_live_event)
        self._stopping = False

    def on_close(self, ws, close_status_code, close_msg):
        if self._stopping:
            return
        try:
            super().on_close(ws, close_status_code, close_msg)
        except Exception:
            pass

    def stop(self):
        self._stopping = True
        ws = getattr(self, "ws", None)
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass


class LiveHub:
    """直播事件中心：把监听线程的结构化事件广播给所有 WebSocket 客户端。"""

    def __init__(self):
        self.loop = None
        self.clients = set()
        self.lock = threading.Lock()
        self.lives = {}       # live_id -> StoppableLive
        self.threads = {}     # live_id -> Thread
        self.states = {}      # live_id -> state
        self.msgs = {}        # live_id -> 状态文案
        self.seq = 0
        self.auth = None
        self.auth_ready = False

    def _enqueue(self, payload):
        text = json.dumps(payload, ensure_ascii=False)
        if self.loop is None:
            return
        try:
            self.loop.call_soon_threadsafe(self._broadcast_text, text)
        except Exception:
            pass

    def _broadcast_text(self, text):
        for ws in list(self.clients):
            asyncio.create_task(self._send(ws, text))

    async def _send(self, ws, text):
        try:
            await ws.send(text)
        except Exception:
            pass

    def rooms_payload(self):
        out = []
        for lid in sorted(self.states):
            st = self.states.get(lid, "idle")
            if st == "stopped":
                continue
            out.append({"live_id": lid, "state": st, "message": self.msgs.get(lid, "")})
        return out

    def push_rooms(self):
        self._enqueue({"type": "rooms", "rooms": self.rooms_payload(), "ts": time.time()})

    def push_status(self, live_id, state=None, message=None):
        if state is not None:
            self.states[live_id] = state
        if message is not None:
            self.msgs[live_id] = message
        self._enqueue({
            "type": "status", "live_id": live_id,
            "state": self.states.get(live_id, "idle"),
            "message": self.msgs.get(live_id, ""), "ts": time.time(),
        })

    def on_live_event(self, live_id, ev):
        self.seq += 1
        self._enqueue({
            "type": "event", "seq": self.seq, "ts": time.time(),
            "live_id": live_id, "data": ev,
        })

    def register(self, ws):
        with self.lock:
            self.clients.add(ws)

    def unregister(self, ws):
        with self.lock:
            self.clients.discard(ws)

    def start(self, live_id):
        live_id = (live_id or "").strip()
        if not live_id:
            self._enqueue({"type": "error", "message": "直播间 ID 为空"})
            return
        if not self.auth_ready:
            self.auth = _try_load_auth()
            self.auth_ready = self.auth is not None
        if not self.auth_ready:
            self._enqueue({"type": "error", "live_id": live_id,
                           "message": "未登录：请在 .env 配置 DY_COOKIES 后重启服务"})
            return
        self.stop(live_id, quiet=True)
        self.states[live_id] = "connecting"
        self.msgs[live_id] = f"正在连接直播间 {live_id} ..."
        self.push_status(live_id, state="connecting", message=self.msgs[live_id])
        self.push_rooms()

        def work():
            live = StoppableLive(live_id, self.auth,
                                 on_live_event=lambda ev, lid=live_id: self.on_live_event(lid, ev))
            self.lives[live_id] = live
            try:
                self.push_status(live_id, state="listening", message=f"正在监听直播间 {live_id}")
                live.start_ws()
            except Exception as e:
                self.push_status(live_id, state="error", message=f"直播间连接失败：{e}")
            finally:
                self.lives.pop(live_id, None)
                self.push_status(live_id, state="stopped", message=f"监听已结束（直播间 {live_id}）")

        t = threading.Thread(target=work, daemon=True)
        self.threads[live_id] = t
        t.start()

    def stop(self, live_id=None, quiet=False):
        if live_id is None:
            for lid in list(self.lives):
                self.stop(lid, quiet=True)
            return
        live = self.lives.get(live_id)
        if live is not None:
            live.stop()
        if not quiet:
            self.push_status(live_id, state="stopped", message="已停止监听")


async def ws_handler(hub, ws):
    hub.register(ws)
    try:
        await ws.send(json.dumps({
            "type": "hello", "rooms": hub.rooms_payload(), "ts": time.time(),
        }, ensure_ascii=False))
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            t = (msg or {}).get("type")
            if t == "start":
                hub.start(msg.get("live_id") or "")
            elif t == "stop":
                hub.stop(msg.get("live_id") or None)
            elif t == "rooms":
                hub.push_rooms()
    finally:
        hub.unregister(ws)


_CTYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json; charset=utf-8",
}


class StaticHandler(BaseHTTPRequestHandler):
    static_dir = STATIC_DIR

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", ""):
            path = "/index.html"
        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        full = os.path.normpath(os.path.join(self.static_dir, path.lstrip("/")))
        if not full.startswith(self.static_dir) or not os.path.isfile(full):
            self.send_error(404)
            return
        try:
            with open(full, "rb") as f:
                body = f.read()
        except OSError:
            self.send_error(404)
            return
        ctype = _CTYPES.get(os.path.splitext(full)[1].lower(), "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


async def _async_main(hub):
    import websockets
    hub.loop = asyncio.get_running_loop()
    async with websockets.serve(partial(ws_handler, hub), "0.0.0.0", WS_PORT):
        await asyncio.Future()  # 永久运行


def main():
    print("=" * 56)
    print("  Pulse Console · 直播数据监听与 AI 陪伴中台")
    print("=" * 56)
    print(f"  控制台页面 :  http://localhost:{HTTP_PORT}")
    print(f"  WebSocket  :  ws://localhost:{WS_PORT}/ws")

    httpd = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), StaticHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    hub = LiveHub()
    hub.auth = _try_load_auth()
    hub.auth_ready = hub.auth is not None
    if hub.auth_ready:
        print("  已加载 .env 登录态 —— 可监听真实直播间")
    else:
        print("  未检测到 DY_COOKIES —— 页面为空态（无数据）。")
        print("  配置 .env 的 DY_COOKIES 后重启即可监听直播间。")
    print("  按 Ctrl+C 退出")
    print("-" * 56)

    try:
        asyncio.run(_async_main(hub))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()


