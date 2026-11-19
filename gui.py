# -*- coding: utf-8 -*-
"""抖音数据采集 / 直播间监听 —— 现代化桌面界面

启动： .venv\\Scripts\\python gui.py
"""
import os
import queue
import re
import sys
import threading

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox

from PIL import Image, ImageDraw, ImageTk
from loguru import logger

from builder.auth import DouyinAuth
from dy_apis.douyin_api import DouyinAPI
from dy_apis.login_api import DYLoginApi
from dy_live.server import DouyinLive
from dy_live.transcribe import LiveTranscriber
from utils import common_util

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
MEDIA_DIR = os.path.abspath(os.path.join(ROOT_DIR, "datas", "media_datas"))
EXCEL_DIR = os.path.abspath(os.path.join(ROOT_DIR, "datas", "excel_datas"))

# ---------- 主题配色 ----------
BG = "#f4f5f8"          # 内容区背景
CARD = "#ffffff"        # 卡片
BORDER = "#e8eaf0"      # 描边
TEXT = "#1f2328"        # 正文
MUTED = "#8a90a0"       # 次要文字
PRIMARY = "#fe2c55"     # 主色（抖音红）
PRIMARY_DARK = "#e01d47"
CYAN = "#25f4ee"        # 抖音青
SIDEBAR = "#121826"     # 侧边栏
SIDEBAR_HOVER = "#1d2637"
SIDEBAR_ACTIVE = "#2a3550"
DARK = "#0f141e"        # 深色（消息区）
DARK_TEXT = "#d7dce6"
GREEN = "#22c55e"
AMBER = "#f59e0b"
FONT = "Microsoft YaHei"

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(s):
    return _ANSI_RE.sub("", s)


def hex_rgb(h):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def mix(c1, c2, t):
    """在两个颜色间插值，t ∈ [0,1]。"""
    a = hex_rgb(c1)
    b = hex_rgb(c2)
    return "#%02x%02x%02x" % tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def gradient_image(w, h, c1, c2, vertical=True):
    """生成一张渐变图，返回 ImageTk.PhotoImage。"""
    img = Image.new("RGB", (max(1, w), max(1, h)))
    d = ImageDraw.Draw(img)
    a, b = hex_rgb(c1), hex_rgb(c2)
    n = h if vertical else w
    for i in range(n):
        t = i / max(1, n - 1)
        color = tuple(int(a[j] + (b[j] - a[j]) * t) for j in range(3))
        if vertical:
            d.line([(0, i), (w, i)], fill=color)
        else:
            d.line([(i, 0), (i, h)], fill=color)
    return ImageTk.PhotoImage(img)


class QueueStream:
    """把 print 输出转成事件队列，供主线程轮询刷新。"""

    def __init__(self, events, kind):
        self.events = events
        self.kind = kind
        self._buf = ""

    def write(self, s):
        if not s:
            return
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = strip_ansi(line).rstrip("\r")
            if line.strip():
                self.events.put((self.kind, line))

    def flush(self):
        if self._buf.strip():
            self.events.put((self.kind, strip_ansi(self._buf).rstrip("\r")))
            self._buf = ""

    def isatty(self):
        return False


class GuiDouyinLive(DouyinLive):
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


class RoundedButton(tk.Canvas):
    """圆角按钮，带悬停变色。"""

    def __init__(self, master, text, command=None, bg=PRIMARY, hover=None,
                 fg="#ffffff", width=120, height=38, radius=10,
                 font=None, canvas_bg=None):
        canvas_bg = canvas_bg if canvas_bg is not None else master.cget("bg")
        super().__init__(master, width=width, height=height, bg=canvas_bg,
                         highlightthickness=0, bd=0)
        self._bg = bg
        self._hover = hover or mix(bg, "#000000", 0.12)
        self._fg = fg
        self._command = command
        self._radius = radius
        self._text = text
        self._font = font or (FONT, 10, "bold")
        self.configure(cursor="hand2")
        self._draw(bg)
        self.bind("<Enter>", lambda e: self._draw(self._hover))
        self.bind("<Leave>", lambda e: self._draw(self._bg))
        self.bind("<Button-1>", lambda e: self._click())

    def _draw(self, color):
        self.delete("all")
        w = int(self["width"])
        h = int(self["height"])
        r = self._radius
        pts = [r, 0, w - r, 0, w, 0, w, r, w, h - r, w, h, w - r, h,
               r, h, 0, h, 0, h - r, 0, r, 0, 0]
        self.create_polygon(pts, smooth=True, fill=color, outline=color)
        self.create_text(w // 2, h // 2, text=self._text, fill=self._fg,
                         font=self._font)

    def _click(self):
        if self._command:
            self._command()

    def set_enabled(self, enabled):
        self.configure(state="normal" if enabled else "disabled")

    def set_text(self, text):
        self._text = text
        self._draw(self._bg)

    def set_color(self, bg, hover=None):
        self._bg = bg
        self._hover = hover or mix(bg, "#000000", 0.12)
        self._draw(bg)
class DouyinGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("抖音数据采集 · 直播间监听")
        self.root.geometry("1100x760")
        self.root.minsize(980, 640)
        self.root.configure(bg=BG)

        self.events = queue.Queue()
        self.auth = None
        self.live = None
        self.live_thread = None
        self.transcriber = None
        self._active_panel = "login"
        self._nav_labels = {}
        self._panels = {}
        self._pulse = 0

        self._old_stdout = sys.stdout
        self._stdout_active = False
        self._logger_sink_id = logger.add(
            self._loguru_sink, level="INFO",
            format="{time:HH:mm:ss} | {message}",
        )

        for d in (MEDIA_DIR, EXCEL_DIR):
            os.makedirs(d, exist_ok=True)

        self._build_ui()
        self._prefill_cookie()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._poll_events)
        self.root.after(600, self._pulse_dot)
        self._check_existing_login()

    # ---------- 日志 ----------
    def _loguru_sink(self, message):
        text = str(message).rstrip("\n")
        if text.strip():
            self.events.put(("log", text))

    # ---------- 界面骨架 ----------
    def _build_ui(self):
        self._build_header()
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True)
        self._build_sidebar(body)
        self.content = tk.Frame(body, bg=BG)
        self.content.pack(side="left", fill="both", expand=True)
        self._build_login_panel()
        self._build_live_panel()
        self._build_crawl_panel()
        self._switch("login")

    def _build_header(self):
        header = tk.Frame(self.root, bg=SIDEBAR, height=62)
        header.pack(fill="x")
        header.pack_propagate(False)

        # 渐变 logo 色块
        logo = tk.Canvas(header, width=44, height=44, bg=SIDEBAR, highlightthickness=0)
        logo.pack(side="left", padx=(18, 10), pady=9)
        self._grad_photo = gradient_image(44, 44, CYAN, PRIMARY, vertical=True)
        logo.create_image(22, 22, image=self._grad_photo)
        logo.create_text(22, 22, text="抖", fill="#ffffff", font=(FONT, 16, "bold"))

        title_box = tk.Frame(header, bg=SIDEBAR)
        title_box.pack(side="left")
        tk.Label(title_box, text="抖音数据采集 · 直播间监听", bg=SIDEBAR, fg="#ffffff",
                 font=(FONT, 13, "bold")).pack(anchor="w")
        tk.Label(title_box, text="DOUYIN DATA STUDIO", bg=SIDEBAR, fg="#5c677d",
                 font=(FONT, 8)).pack(anchor="w")

        # 右侧登录状态
        right = tk.Frame(header, bg=SIDEBAR)
        right.pack(side="right", padx=18)
        self.status_dot = tk.Canvas(right, width=18, height=18, bg=SIDEBAR,
                                    highlightthickness=0)
        self.status_dot.pack(side="left", padx=(0, 6))
        self.status_dot_id = self.status_dot.create_oval(4, 4, 14, 14, fill=MUTED, outline="")
        self.header_status = tk.Label(right, text="未登录", bg=SIDEBAR, fg="#c6cbd6",
                                      font=(FONT, 10))
        self.header_status.pack(side="left")

    def _build_sidebar(self, body):
        self.sidebar = tk.Frame(body, bg=SIDEBAR, width=200)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        tk.Label(self.sidebar, text="功能导航", bg=SIDEBAR, fg="#5c677d",
                 font=(FONT, 9), anchor="w").pack(fill="x", padx=20, pady=(20, 6))

        self._nav_item("login", "Cookie 登录", "🔑")
        self._nav_item("crawl", "数据爬取", "🧲")
        self._nav_item("live", "直播间监听", "📺")

        # 底部说明
        tk.Label(self.sidebar, text="仅用于学习研究\n禁止发布违法内容", bg=SIDEBAR,
                 fg="#3a4356", font=(FONT, 8), justify="left").pack(
            side="bottom", fill="x", padx=20, pady=16)

    def _nav_item(self, key, text, icon):
        lbl = tk.Label(self.sidebar, text=f"   {icon}  {text}", bg=SIDEBAR,
                       fg="#c6cbd6", font=(FONT, 11), anchor="w", padx=12, pady=13,
                       cursor="hand2")
        lbl.pack(fill="x", padx=10, pady=3)
        lbl.bind("<Button-1>", lambda e: self._switch(key))
        lbl.bind("<Enter>", lambda e: lbl.configure(bg=SIDEBAR_HOVER)
                 if self._active_panel != key else None)
        lbl.bind("<Leave>", lambda e: lbl.configure(
            bg=SIDEBAR_ACTIVE if self._active_panel == key else SIDEBAR))
        self._nav_labels[key] = lbl

    def _switch(self, key):
        self._active_panel = key
        for k, lbl in self._nav_labels.items():
            if k == key:
                lbl.configure(bg=SIDEBAR_ACTIVE, fg="#ffffff")
            else:
                lbl.configure(bg=SIDEBAR, fg="#c6cbd6")
        for k, panel in self._panels.items():
            if k == key:
                panel.pack(fill="both", expand=True, padx=18, pady=18)
            else:
                panel.pack_forget()

    # ---------- 状态点动画 ----------
    def _pulse_dot(self):
        self._pulse += 1
        if self.auth is not None:
            colors = [GREEN, mix(GREEN, "#000000", 0.25)]
            self.status_dot.itemconfigure(self.status_dot_id,
                                          fill=colors[self._pulse % 2])
        self.root.after(600, self._pulse_dot)
    # ---------- 通用组件 ----------
    def _card(self, parent):
        return tk.Frame(parent, bg=CARD, highlightbackground=BORDER,
                        highlightthickness=1)

    def _title(self, parent, text, sub=""):
        box = tk.Frame(parent, bg=BG)
        box.pack(fill="x", pady=(0, 12))
        tk.Label(box, text=text, bg=BG, fg=TEXT, font=(FONT, 16, "bold")).pack(anchor="w")
        if sub:
            tk.Label(box, text=sub, bg=BG, fg=MUTED, font=(FONT, 9)).pack(anchor="w")

    # ---------- 登录面板（仅 Cookie） ----------
    def _build_login_panel(self):
        p = tk.Frame(self.content, bg=BG)
        self._panels["login"] = p
        self._title(p, "Cookie 登录", "在浏览器登录抖音后，把请求头里的 Cookie 粘进来即可")

        # 状态卡
        status_card = self._card(p)
        status_card.pack(fill="x", pady=(0, 12))
        row = tk.Frame(status_card, bg=CARD)
        row.pack(fill="x", padx=16, pady=14)
        dot = tk.Canvas(row, width=14, height=14, bg=CARD, highlightthickness=0)
        dot.pack(side="left", padx=(0, 10))
        self.login_dot = dot
        self.login_dot_id = dot.create_oval(2, 2, 12, 12, fill=MUTED, outline="")
        self.login_status_var = tk.StringVar(value="正在检测登录状态 ...")
        tk.Label(row, textvariable=self.login_status_var, bg=CARD, fg=TEXT,
                 font=(FONT, 11), anchor="w").pack(side="left", fill="x", expand=True)
        RoundedButton(row, "重新检测", self.verify_login, bg=mix(PRIMARY, "#000", 0.1),
                      hover=mix(PRIMARY, "#000", 0.2), width=96, height=32,
                      canvas_bg=CARD).pack(side="right")

        # Cookie 输入卡
        ck_card = self._card(p)
        ck_card.pack(fill="x")
        tk.Label(ck_card, text="粘贴完整 Cookie（支持整段请求头，自动提取 cookie 行）",
                 bg=CARD, fg=MUTED, font=(FONT, 9), anchor="w").pack(
            fill="x", padx=16, pady=(14, 6))
        self.cookie_text = scrolledtext.ScrolledText(
            ck_card, height=7, font=("Consolas", 9), bg="#fafbfc", fg=TEXT,
            relief="flat", highlightbackground=BORDER, highlightthickness=1,
            insertbackground=PRIMARY, padx=10, pady=10)
        self.cookie_text.pack(fill="x", padx=16, pady=(0, 14))
        RoundedButton(ck_card, "保存并登录", self.do_cookie_login, width=150, height=40,
                      canvas_bg=CARD, font=(FONT, 11, "bold")).pack(
            anchor="w", padx=16, pady=(0, 14))

        # 获取 cookie 提示
        hint = self._card(p)
        hint.pack(fill="x", pady=(12, 0))
        tk.Label(hint, text="如何获取 Cookie：", bg=CARD, fg=TEXT,
                 font=(FONT, 10, "bold"), anchor="w").pack(fill="x", padx=16, pady=(14, 4))
        steps = ("1. 浏览器登录 www.douyin.com 后按 F12 打开开发者工具",
                 "2. 切到 Network 页签，点任意 /aweme/v1/web/ 请求",
                 "3. 在 Request Headers 里复制 cookie 那一整行，粘贴到上方即可")
        for s in steps:
            tk.Label(hint, text=s, bg=CARD, fg=MUTED, font=(FONT, 9),
                     anchor="w", justify="left").pack(fill="x", padx=16)

    # ---------- 直播间监听面板 ----------
    def _build_live_panel(self):
        p = tk.Frame(self.content, bg=BG)
        self._panels["live"] = p
        self._title(p, "直播间监听", "实时接收弹幕 / 礼物 / 进场 / 点赞 / 关注")

        top = self._card(p)
        top.pack(fill="x", pady=(0, 12))
        row = tk.Frame(top, bg=CARD)
        row.pack(fill="x", padx=16, pady=14)
        tk.Label(row, text="直播间链接 / ID", bg=CARD, fg=MUTED,
                 font=(FONT, 10)).pack(side="left", padx=(0, 10))
        self.live_input = tk.Entry(row, font=(FONT, 11), relief="flat",
                                   highlightbackground=BORDER, highlightthickness=1,
                                   fg=TEXT, insertbackground=PRIMARY)
        self.live_input.pack(side="left", fill="x", expand=True, ipady=7, padx=(0, 10))
        RoundedButton(row, "开始监听", self.start_live, bg=PRIMARY, width=110, height=34,
                      canvas_bg=CARD).pack(side="left", padx=(0, 8))
        RoundedButton(row, "停止", self.stop_live, bg=mix("#94a3b8", "#000", 0.0),
                      hover="#64748b", width=72, height=34, canvas_bg=CARD).pack(side="left")
        self.asr_btn = RoundedButton(row, "🎤 语音转写", self.toggle_asr, bg=PRIMARY,
                                     width=122, height=34, canvas_bg=CARD)
        self.asr_btn.pack(side="left", padx=(8, 0))

        self._build_live_legend(p)

        self.live_status_var = tk.StringVar(value="尚未监听。输入直播间链接后点击「开始监听」")
        tk.Label(p, textvariable=self.live_status_var, bg=BG, fg=MUTED,
                 font=(FONT, 9), anchor="w").pack(fill="x", pady=(0, 8))

        self.live_text = scrolledtext.ScrolledText(
            p, bg=DARK, fg=DARK_TEXT, font=("Consolas", 10), relief="flat",
            highlightbackground=BORDER, highlightthickness=1, padx=12, pady=12,
            insertbackground=PRIMARY)
        self.live_text.pack(fill="both", expand=True)
        for tag, color in [("anchor", "#f472b6"), ("gift", CYAN), ("chat", "#ffffff"),
                           ("enter", "#8a90a0"), ("like", GREEN), ("follow", GREEN),
                           ("room", AMBER), ("err", PRIMARY)]:
            self.live_text.tag_configure(tag, foreground=color)
        # 结构化消息的丰富配色（徽章用背景色，字段用前景色）
        for tag, kw in {
            "badge_chat":   {"foreground": "#ffffff", "background": "#5865f2"},
            "badge_gift":   {"foreground": "#0f141e", "background": CYAN},
            "badge_member": {"foreground": "#ffffff", "background": "#6b7280"},
            "badge_like":   {"foreground": "#0f141e", "background": GREEN},
            "badge_follow": {"foreground": "#0f141e", "background": "#fbbf24"},
            "badge_room":   {"foreground": "#0f141e", "background": "#f59e0b"},
            "nick":         {"foreground": "#f472b6"},
            "content":      {"foreground": "#ffffff"},
            "gift_name":    {"foreground": CYAN},
            "lv":           {"foreground": "#fbbf24"},
            "iron":         {"foreground": "#f43f5e"},
            "fans":         {"foreground": "#fb923c"},
            "is_fan":       {"foreground": "#22c55e"},
            "meta":         {"foreground": "#8a90a0"},
            "meta_label":   {"foreground": "#5c677d"},
            "room_info":    {"foreground": AMBER},
        }.items():
            self.live_text.tag_configure(tag, **kw)

    def _build_live_legend(self, parent):
        card = self._card(parent)
        card.pack(fill="x", pady=(12, 0))
        tk.Label(card, text="字段说明 · 图例", bg=CARD, fg=TEXT,
                 font=(FONT, 9, "bold"), anchor="w").pack(fill="x", padx=12, pady=(10, 4))
        grid = tk.Frame(card, bg=CARD)
        grid.pack(fill="x", padx=12, pady=(0, 10))
        items = [
            ("💬 弹幕", "#5865f2", "观众弹幕消息"),
            ("🎁 礼物", CYAN, "观众送出礼物"),
            ("👤 进场", "#6b7280", "观众进入直播间"),
            ("❤️ 点赞", GREEN, "观众点赞主播"),
            ("⭐ 关注", "#fbbf24", "观众关注主播（成为粉丝）"),
            ("📊 房间", "#f59e0b", "直播间热度 / 在线人数"),
            ("SEC_UID", MUTED, "用户唯一 ID（加密标识，用于区分用户）"),
            ("昵称", "#f472b6", "用户显示名称"),
            ("Lv", "#fbbf24", "用户等级"),
            ("粉丝团", "#fb923c", "付费粉丝团等级"),
            ("铁粉", "#f43f5e", "主播的铁粉标识"),
            ("关注主播", "#22c55e", "该用户是否已关注主播"),
        ]
        for i, (name, color, desc) in enumerate(items):
            cell = tk.Frame(grid, bg=CARD)
            cell.grid(row=i // 3, column=i % 3, sticky="w", padx=(0, 16), pady=2)
            tk.Label(cell, text=name, bg=CARD, fg=color, font=(FONT, 9, "bold")).pack(side="left")
            tk.Label(cell, text=f" = {desc}", bg=CARD, fg=MUTED, font=(FONT, 9)).pack(side="left")

    def _fans_club_level(self, u):
        for b in u.get("badges", []) or []:
            text = ((b.get("name", "") or "") + (b.get("alt", "") or "")).lower()
            if any(k in text for k in ("粉丝团", "fans", "club", "粉丝")):
                if b.get("level"):
                    return b["level"]
        return None

    def _user_chips(self, u):
        chips = []
        if u.get("level"):
            chips.append((f"Lv{u['level']}", "lv"))
        badge_text = " ".join(
            ((b.get("name", "") or "") + (b.get("alt", "") or "")).lower()
            for b in (u.get("badges") or []))
        if "铁粉" in badge_text or "iron" in badge_text:
            chips.append(("铁粉", "iron"))
        lvl = self._fans_club_level(u)
        if lvl:
            chips.append((f"粉丝团{lvl}级", "fans"))
        elif u.get("fan_ticket_count"):
            chips.append((f"粉丝团票{u['fan_ticket_count']}", "fans"))
        if u.get("is_following"):
            chips.append(("关注主播", "is_fan"))
        return chips

    def _append_live_rich(self, ev):
        t = self.live_text
        t.configure(state="normal")
        etype = ev.get("type")
        u = ev.get("user") or {}
        if etype == "room":
            t.insert("end", "📊 房间  ", "badge_room")
            t.insert("end", str(ev.get("display", "")) + "\n", "room_info")
        else:
            badges = {
                "chat":   ("💬 弹幕", "badge_chat"),
                "gift":   ("🎁 礼物", "badge_gift"),
                "member": ("👤 进场", "badge_member"),
                "like":   ("❤️ 点赞", "badge_like"),
                "follow": ("⭐ 关注", "badge_follow"),
            }
            badge, btag = badges.get(etype, ("·", "badge_member"))
            t.insert("end", f"{badge} ", btag)
            t.insert("end", (u.get("nickname") or "未知用户") + " ", "nick")
            for chip_text, ctag in self._user_chips(u):
                t.insert("end", f"[{chip_text}] ", ctag)
            if etype == "chat":
                t.insert("end", f": {ev.get('content', '')}\n", "content")
            elif etype == "gift":
                t.insert("end", "送给 ", "content")
                to = ev.get("to_user") or {}
                t.insert("end", (to.get("nickname") or "主播") + " ", "nick")
                t.insert("end", f"💎{ev.get('gift_name', '')} x{ev.get('gift_count', 1)}\n", "gift_name")
            elif etype == "member":
                t.insert("end", "进入直播间\n", "content")
            elif etype == "like":
                t.insert("end", f"点赞 x{ev.get('count', 0)}", "content")
                if ev.get("total"):
                    t.insert("end", f"（累计 {ev['total']}）", "meta")
                t.insert("end", "\n", "content")
            elif etype == "follow":
                t.insert("end", "关注了主播 🎉\n", "content")
            else:
                t.insert("end", "\n", "content")
            # 第二行：字段解释式元信息
            meta = []
            if u.get("sec_uid"):
                meta.append(("SEC_UID", u["sec_uid"]))
            if u.get("display_id"):
                meta.append(("抖音号", u["display_id"]))
            if u.get("city"):
                meta.append(("城市", u["city"]))
            g = u.get("gender")
            if g:
                meta.append(("性别", {1: "男", 2: "女"}.get(g, "未知")))
            if meta:
                t.insert("end", "    ", "meta")
                for i, (k, v) in enumerate(meta):
                    t.insert("end", f"{k}=", "meta_label")
                    t.insert("end", str(v), "meta")
                    if i != len(meta) - 1:
                        t.insert("end", "  ·  ", "meta")
                t.insert("end", "\n", "meta")
        lines = int(t.index("end-1c").split(".")[0])
        if lines > 2000:
            t.delete("1.0", f"{lines - 1000}.0")
        t.see("end")
        t.configure(state="disabled")

    def _append_live(self, text):
        tag = "chat"
        if "[主播]" in text:
            tag = "anchor"
        elif "[礼物]" in text:
            tag = "gift"
        elif "[消息]" in text:
            tag = "chat"
        elif "[进入]" in text:
            tag = "enter"
        elif "[点赞]" in text or "[关注]" in text:
            tag = "like"
        elif "[房间" in text:
            tag = "room"
        elif "失败" in text or "error" in text.lower():
            tag = "err"
        self.live_text.configure(state="normal")
        self.live_text.insert("end", text + "\n", tag)
        lines = int(self.live_text.index("end-1c").split(".")[0])
        if lines > 2000:
            self.live_text.delete("1.0", f"{lines - 1000}.0")
        self.live_text.see("end")
        self.live_text.configure(state="disabled")
    # ---------- 数据爬取面板 ----------
    def _build_crawl_panel(self):
        p = tk.Frame(self.content, bg=BG)
        self._panels["crawl"] = p
        self._title(p, "数据爬取", "搜索关键词 / 指定作品链接 / 用户主页全部作品")

        # 模式切换
        seg = self._card(p)
        seg.pack(fill="x", pady=(0, 12))
        segrow = tk.Frame(seg, bg=CARD)
        segrow.pack(fill="x", padx=8, pady=8)
        self._mode_labels = {}
        self.crawl_mode = tk.StringVar(value="search")
        for key, text in [("search", "搜索关键词"), ("work", "指定作品链接"), ("user", "用户主页")]:
            lbl = tk.Label(segrow, text=text, bg=CARD, fg=MUTED, font=(FONT, 10),
                           padx=20, pady=9, cursor="hand2")
            lbl.pack(side="left", padx=4)
            lbl.bind("<Button-1>", lambda e, k=key: self._set_mode(k))
            self._mode_labels[key] = lbl

        # 搜索输入
        self.search_box = self._card(p)
        grid = tk.Frame(self.search_box, bg=CARD)
        grid.pack(fill="x", padx=16, pady=14)
        tk.Label(grid, text="关键词", bg=CARD, fg=MUTED, font=(FONT, 10)).grid(
            row=0, column=0, sticky="e", pady=6)
        self.search_query = tk.Entry(grid, font=(FONT, 11), relief="flat",
                                     highlightbackground=BORDER, highlightthickness=1,
                                     insertbackground=PRIMARY)
        self.search_query.insert(0, "榴莲")
        self.search_query.grid(row=0, column=1, sticky="we", padx=10, ipady=6)
        tk.Label(grid, text="数量", bg=CARD, fg=MUTED, font=(FONT, 10)).grid(
            row=0, column=2, sticky="e", pady=6)
        self.search_num = tk.Entry(grid, width=6, font=(FONT, 11), relief="flat",
                                   highlightbackground=BORDER, highlightthickness=1,
                                   insertbackground=PRIMARY)
        self.search_num.insert(0, "5")
        self.search_num.grid(row=0, column=3, sticky="w", padx=10, ipady=6)

        self.save_choice_var = tk.StringVar(value="all")
        tk.Label(grid, text="保存方式", bg=CARD, fg=MUTED, font=(FONT, 10)).grid(
            row=1, column=0, sticky="e", pady=6)
        ttk.Combobox(grid, textvariable=self.save_choice_var, width=15, state="readonly",
                     values=["all", "media", "media-video", "media-image", "excel"],
                     font=(FONT, 10)).grid(row=1, column=1, sticky="w", padx=10)

        self.sort_var = tk.StringVar(value="0 - 综合排序")
        tk.Label(grid, text="排序", bg=CARD, fg=MUTED, font=(FONT, 10)).grid(
            row=1, column=2, sticky="e", pady=6)
        ttk.Combobox(grid, textvariable=self.sort_var, width=15, state="readonly",
                     values=["0 - 综合排序", "1 - 最多点赞", "2 - 最新发布"],
                     font=(FONT, 10)).grid(row=1, column=3, sticky="w", padx=10)

        self.publish_var = tk.StringVar(value="0 - 不限")
        tk.Label(grid, text="发布时间", bg=CARD, fg=MUTED, font=(FONT, 10)).grid(
            row=2, column=0, sticky="e", pady=6)
        ttk.Combobox(grid, textvariable=self.publish_var, width=15, state="readonly",
                     values=["0 - 不限", "1 - 一天内", "7 - 一周内", "180 - 半年内"],
                     font=(FONT, 10)).grid(row=2, column=1, sticky="w", padx=10)
        grid.columnconfigure(1, weight=1)

        # 作品链接输入
        self.work_box = self._card(p)
        tk.Label(self.work_box, text="作品链接（每行一个）", bg=CARD, fg=MUTED,
                 font=(FONT, 9), anchor="w").pack(fill="x", padx=16, pady=(14, 6))
        self.works_text = scrolledtext.ScrolledText(
            self.work_box, height=3, font=("Consolas", 9), bg="#fafbfc", fg=TEXT,
            relief="flat", highlightbackground=BORDER, highlightthickness=1,
            insertbackground=PRIMARY, padx=10, pady=10)
        self.works_text.pack(fill="x", padx=16, pady=(0, 14))

        # 用户主页输入
        self.user_box = self._card(p)
        tk.Label(self.user_box, text="用户主页链接", bg=CARD, fg=MUTED,
                 font=(FONT, 9), anchor="w").pack(fill="x", padx=16, pady=(14, 6))
        self.user_url = tk.Entry(self.user_box, font=(FONT, 11), relief="flat",
                                 highlightbackground=BORDER, highlightthickness=1,
                                 insertbackground=PRIMARY)
        self.user_url.pack(fill="x", padx=16, pady=(0, 14), ipady=7)

        # 开始 + 状态
        run = tk.Frame(p, bg=BG)
        run.pack(fill="x", pady=12)
        RoundedButton(run, "开始爬取", self.start_crawl, width=140, height=40,
                      font=(FONT, 11, "bold")).pack(side="left")
        self.crawl_status_var = tk.StringVar(value="")
        tk.Label(run, textvariable=self.crawl_status_var, bg=BG, fg=MUTED,
                 font=(FONT, 9), anchor="w").pack(side="left", fill="x", expand=True, padx=12)

        self.crawl_text = scrolledtext.ScrolledText(
            p, bg=DARK, fg=DARK_TEXT, font=("Consolas", 9), relief="flat",
            highlightbackground=BORDER, highlightthickness=1, padx=12, pady=12,
            insertbackground=PRIMARY)
        self.crawl_text.pack(fill="both", expand=True)

        self._set_mode("search")

    def _set_mode(self, key):
        self.crawl_mode.set(key)
        for k, lbl in self._mode_labels.items():
            if k == key:
                lbl.configure(bg=PRIMARY, fg="#ffffff")
            else:
                lbl.configure(bg=CARD, fg=MUTED)
        for k, box in [("search", self.search_box), ("work", self.work_box),
                       ("user", self.user_box)]:
            if k == key:
                box.pack(fill="x", pady=(0, 12))
            else:
                box.pack_forget()
    # ---------- 登录逻辑（仅 Cookie） ----------
    def _set_login_state(self, authed, text):
        color = GREEN if authed else MUTED
        self.status_dot.itemconfigure(self.status_dot_id, fill=color)
        self.login_dot.itemconfigure(self.login_dot_id, fill=color)
        self.header_status.configure(text="已登录" if authed else "未登录", fg=color)
        self.login_status_var.set(text)

    @staticmethod
    def _extract_cookie(raw):
        lines = raw.splitlines()
        for i, line in enumerate(lines):
            if line.strip().lower() == "cookie" and i + 1 < len(lines):
                val = lines[i + 1].strip()
                if val:
                    return val
        return raw.strip()

    @staticmethod
    def _verify_login(auth):
        """用轻量的 /query/user/ 接口验证登录态（比 get_my_sec_uid 可靠）。"""
        try:
            DouyinAPI.get_my_uid(auth)
            return True
        except Exception:
            return False

    def _prefill_cookie(self):
        """启动时把上次保存的 Cookie 填回输入框，方便查看/复用。"""
        try:
            from dotenv import load_dotenv
            load_dotenv(".env")
            ck = os.getenv("DY_COOKIES") or ""
        except Exception:
            ck = ""
        if ck:
            self.cookie_text.delete("1.0", "end")
            self.cookie_text.insert("1.0", ck)

    def do_cookie_login(self):
        raw = self.cookie_text.get("1.0", "end")
        ck = self._extract_cookie(raw)
        if not ck:
            messagebox.showwarning("提示", "请先粘贴 Cookie")
            return
        if ck != raw.strip():
            self.cookie_text.delete("1.0", "end")
            self.cookie_text.insert("1.0", ck)
        if "sessionid=" not in ck:
            messagebox.showwarning(
                "提示",
                "粘贴的 Cookie 里没有 sessionid，说明浏览器没登录、或复制不完整。\n\n"
                "请先确保浏览器已登录 www.douyin.com，\n"
                "再复制 Request Headers 里的完整 cookie 行。",
            )
            return
        self.login_status_var.set("正在登录并验证 ...")

        def work():
            try:
                auth = DouyinAuth.from_cookie(ck, bootstrap_creator=False)
                if not self._verify_login(auth):
                    self.events.put(("login_fail", "Cookie 无效或已过期（抖音未识别为登录状态）"))
                    return
                self.events.put(("login_ok", auth))
            except Exception as e:
                self.events.put(("login_fail", str(e)))

        threading.Thread(target=work, daemon=True).start()

    def verify_login(self):
        self.login_status_var.set("正在检测 ...")
        self._check_existing_login()

    def _check_existing_login(self):
        def work():
            try:
                auth = common_util.load_env()
            except Exception as e:
                self.events.put(("login_status", f"加载 .env 失败：{e}"))
                return
            if not (auth.cookie or {}).get("sessionid"):
                self.events.put(("login_status", "未登录：请粘贴 Cookie 登录"))
                return
            if self._verify_login(auth):
                self.auth = auth
                self.events.put(("login_ok_quiet", auth))
            else:
                self.events.put(("login_status", "上次的 Cookie 已失效，请重新粘贴登录"))
                self.events.put(("log", "上次登录的 Cookie 已失效"))

        threading.Thread(target=work, daemon=True).start()

    def _on_login_ok(self, auth, quiet=False):
        self.auth = auth
        common_util.dy_auth = auth
        if not quiet:
            try:
                DYLoginApi().save_credential(auth)
            except Exception:
                pass
        uid = ""
        try:
            uid = str(DouyinAPI.get_my_uid(auth))
        except Exception:
            uid = ""
        self._set_login_state(True, f"已登录（uid={uid}）" if uid else "已登录")
        self.events.put(("log", f"登录成功 {('，uid=' + uid) if uid else ''}；凭证已写入 .env"))

    def _on_login_fail(self, msg):
        self._set_login_state(False, f"登录失败：{msg}")
        hint = self._friendly_login_hint(msg)
        self.events.put(("log", f"登录失败：{msg}"))
        if hint:
            self.events.put(("log", hint))

    @staticmethod
    def _friendly_login_hint(msg):
        text = str(msg)
        if "2046" in text:
            return "错误码 2046：操作过于频繁，抖音对登录做了限频。请等 10~30 分钟再试。"
        if "4031" in text:
            return "错误码 4031：登录域或 SDK 版本不对（网站安全风险）。"
        if "error_code=7" in text or "访问太频繁" in text:
            return "访问太频繁（error_code=7），请等几分钟再试。"
        return ""
    # ---------- 直播间 ----------
    @staticmethod
    def _parse_live_id(raw):
        raw = (raw or "").strip()
        if not raw:
            return ""
        m = re.search(r"\d{5,}", raw)
        return m.group(0) if m else raw

    def _start_stdout_capture(self):
        if not self._stdout_active:
            self._stdout_active = True
            sys.stdout = QueueStream(self.events, "live")

    def _stop_stdout_capture(self):
        if self._stdout_active:
            self._stdout_active = False
            sys.stdout = self._old_stdout

    def start_live(self):
        raw = self.live_input.get().strip()
        live_id = self._parse_live_id(raw)
        if not live_id:
            messagebox.showwarning("提示", "请输入直播间链接或直播间 ID")
            return
        if self.auth is None:
            messagebox.showwarning("提示", "请先在「Cookie 登录」页完成登录")
            return
        if self.live_thread and self.live_thread.is_alive():
            messagebox.showwarning("提示", "已在监听中，请先停止")
            return

        self.live_status_var.set(f"正在连接直播间 {live_id} ...")
        self._start_stdout_capture()

        def work():
            try:
                live = GuiDouyinLive(live_id, self.auth,
                                     on_live_event=lambda ev: self.events.put(("live_rich", ev)))
                self.live = live
                self.events.put(("live_status", f"正在监听直播间 {live_id} ..."))
                live.start_ws()
            except Exception as e:
                self.events.put(("live_status", f"直播间连接失败：{e}"))
                self.events.put(("live", f"直播间连接失败：{e}"))
            finally:
                self.events.put(("live_stopped", live_id))

        self.live_thread = threading.Thread(target=work, daemon=True)
        self.live_thread.start()

    def stop_live(self):
        if self.live is not None:
            self.live.stop()
        self._stop_stdout_capture()
        self.live_status_var.set("已停止监听")

    def toggle_asr(self):
        if self.transcriber is not None:
            self.transcriber.stop()
            self.transcriber = None
            self.asr_btn.set_text("🎤 语音转写")
            self.asr_btn.set_color(PRIMARY)
            self.live_status_var.set("已停止语音转写")
            return
        live_id = self._parse_live_id(self.live_input.get().strip())
        if not live_id:
            messagebox.showwarning("提示", "请先输入直播间链接 / ID")
            return
        if self.auth is None:
            messagebox.showwarning("提示", "请先在「Cookie 登录」页完成登录")
            return
        self.live_status_var.set("正在准备语音转写...")
        self.asr_btn.set_text("⏹ 停止转写")
        self.asr_btn.set_color(AMBER)
        self.transcriber = LiveTranscriber(
            self.auth, live_id,
            on_text=lambda t: self.events.put(("transcribe", t)),
            on_status=lambda s: self.events.put(("live_status", s)))

        def work():
            try:
                self.transcriber.run()
            except Exception as e:
                self.events.put(("live_status", f"语音转写失败：{e}"))
                self.events.put(("transcribe", f"（语音转写失败：{e}）"))
            finally:
                self.events.put(("asr_stopped", None))

        threading.Thread(target=work, daemon=True).start()

    # ---------- 爬取 ----------
    def _append_crawl(self, text):
        self.crawl_text.configure(state="normal")
        self.crawl_text.insert("end", text + "\n")
        lines = int(self.crawl_text.index("end-1c").split(".")[0])
        if lines > 2000:
            self.crawl_text.delete("1.0", f"{lines - 1000}.0")
        self.crawl_text.see("end")
        self.crawl_text.configure(state="disabled")

    def start_crawl(self):
        if self.auth is None:
            messagebox.showwarning("提示", "请先在「Cookie 登录」页完成登录")
            return
        mode = self.crawl_mode.get()
        base_path = {"media": MEDIA_DIR, "excel": EXCEL_DIR}

        if mode == "search":
            query = self.search_query.get().strip()
            if not query:
                messagebox.showwarning("提示", "请输入搜索关键词")
                return
            try:
                num = int(self.search_num.get().strip())
            except ValueError:
                num = 5
            save_choice = self.save_choice_var.get()
            sort_type = self.sort_var.get().split()[0]
            publish_time = self.publish_var.get().split()[0]
            self.crawl_status_var.set(f"正在搜索「{query}」...")
            args = ("search", query, num, save_choice, sort_type, publish_time, "", "", "")
        elif mode == "work":
            works = [ln.strip() for ln in self.works_text.get("1.0", "end").splitlines()
                     if ln.strip()]
            if not works:
                messagebox.showwarning("提示", "请至少填写一个作品链接")
                return
            save_choice = self.save_choice_var.get()
            self.crawl_status_var.set(f"正在爬取 {len(works)} 个作品 ...")
            args = ("work", works, save_choice)
        else:
            user_url = self.user_url.get().strip()
            if not user_url:
                messagebox.showwarning("提示", "请输入用户主页链接")
                return
            save_choice = self.save_choice_var.get()
            self.crawl_status_var.set(f"正在爬取用户 {user_url} 的全部作品 ...")
            args = ("user", user_url, save_choice)

        def work():
            from main import Data_Spider
            spider = Data_Spider()
            try:
                if args[0] == "search":
                    _m, query, num, save_choice, sort_type, publish_time, fd, sr, ct = args
                    spider.spider_some_search_work(
                        self.auth, query, num, base_path, save_choice,
                        sort_type, publish_time, fd, sr, ct)
                    self.events.put(("crawl_status", f"搜索「{query}」完成"))
                elif args[0] == "work":
                    _m, works, save_choice = args
                    spider.spider_some_work(self.auth, works, base_path, save_choice, "works")
                    self.events.put(("crawl_status", f"爬取 {len(works)} 个作品完成"))
                else:
                    _m, user_url, save_choice = args
                    spider.spider_user_all_work(self.auth, user_url, base_path, save_choice)
                    self.events.put(("crawl_status", f"爬取用户 {user_url} 完成"))
            except Exception as e:
                self.events.put(("crawl_status", f"爬取失败：{e}"))
                self.events.put(("crawl", f"爬取失败：{e}"))

        threading.Thread(target=work, daemon=True).start()
    # ---------- 事件轮询 ----------
    def _poll_events(self):
        try:
            while True:
                self._handle_event(self.events.get_nowait())
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def _handle_event(self, event):
        kind = event[0]
        if kind == "login_status":
            self._set_login_state(False, event[1])
        elif kind == "login_ok":
            self._on_login_ok(event[1])
        elif kind == "login_ok_quiet":
            self._on_login_ok(event[1], quiet=True)
        elif kind == "login_fail":
            self._on_login_fail(event[1])
        elif kind == "live":
            self._append_live(event[1])
        elif kind == "live_rich":
            self._append_live_rich(event[1])
        elif kind == "live_status":
            self.live_status_var.set(event[1])
        elif kind == "live_stopped":
            self.live_status_var.set(f"监听已结束（直播间 {event[1]}）")
            self._stop_stdout_capture()
        elif kind == "transcribe":
            self._append_live("[主播] " + event[1])
        elif kind == "asr_stopped":
            self.transcriber = None
            self.asr_btn.set_text("🎤 语音转写")
            self.asr_btn.set_color(PRIMARY)
        elif kind == "crawl_status":
            self.crawl_status_var.set(event[1])
        elif kind in ("crawl", "log"):
            self._append_crawl(event[1])

    # ---------- 退出 ----------
    def _on_close(self):
        try:
            self.stop_live()
        except Exception:
            pass
        sys.stdout = self._old_stdout
        try:
            logger.remove(self._logger_sink_id)
        except Exception:
            pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    DouyinGUI().run()


if __name__ == "__main__":
    main()






