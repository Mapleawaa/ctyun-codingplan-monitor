"""桌面悬浮窗（桌宠模式）。

- 折叠态：圆角小挂件，显示主周期剩余百分比与迷你进度条；
- 点击挂件展开：三个周期各自的使用进度条、剩余量与限额刷新倒计时；
- 可拖拽，位置记忆；展开态提供刷新/间隔/页面/退出按钮。

实现基于 tkinter Canvas + Windows 透明色（-transparentcolor），无需额外依赖。
"""

from __future__ import annotations

import queue
import threading
import time
import webbrowser
import tkinter as tk
from datetime import datetime

from . import api, config

CHROMA = "#010203"  # 透明色
PANEL_BG = "#1d2126"
BAR_TRACK = "#39424b"
TEXT_MAIN = "#f2f4f6"
TEXT_SUB = "#9aa4ad"
BTN_BG = "#2f363d"

INTERVAL_CHOICES = [60, 180, 300, 600, 1800, 3600]
PAGE_URL = api.PAGE_URL
REPO_URL = "https://github.com/Mapleawaa/ctyun-codingplan-monitor"
REPO_TEXT = "github.com/Mapleawaa/ctyun-codingplan-monitor"

FONT = "Microsoft YaHei UI"

WIDGET_W, WIDGET_H = 132, 48
PANEL_W = 304


def _color_for(pct: float) -> str:
    if pct >= 50:
        return "#4caf50"
    if pct >= 20:
        return "#fbbc2d"
    return "#e53935"


def _fmt_interval(seconds: int) -> str:
    if seconds % 3600 == 0:
        return f"{seconds // 3600} 小时"
    if seconds % 60 == 0:
        return f"{seconds // 60} 分钟"
    return f"{seconds} 秒"


class FloatingWidget:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.interval = int(cfg.get("interval") or 300)
        self.expanded = False
        self.state: dict = {"ok": False, "usages": [], "error": "正在获取数据…", "updated": None}
        self._queue: queue.Queue = queue.Queue()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._press: tuple[int, int, int, int] | None = None
        self._moved = False
        self._zones: list[tuple[str, int, int, int, int]] = []
        self._next_refresh: float | None = None  # 下次自动刷新的时间戳
        self._last_countdown: str | None = None

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", CHROMA)
        self.canvas = tk.Canvas(self.root, bg=CHROMA, highlightthickness=0, bd=0)
        self.canvas.pack()

        self._apply_geometry(WIDGET_W, WIDGET_H)
        self._redraw()

        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

        threading.Thread(target=self._loop, daemon=True).start()
        self.root.after(150, self._poll)

    # ---------- 窗口定位 ----------
    def _apply_geometry(self, w: int, h: int) -> None:
        if self.cfg.get("pos_x") is not None and self.cfg.get("pos_y") is not None:
            x, y = int(self.cfg["pos_x"]), int(self.cfg["pos_y"])
        else:
            self.root.update_idletasks()
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            x, y = sw - w - 48, sh - h - 140
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _remember_position(self) -> None:
        self.cfg["pos_x"] = self.root.winfo_x()
        self.cfg["pos_y"] = self.root.winfo_y()
        config.save(self.cfg)

    # ---------- 后台刷新 ----------
    def _loop(self) -> None:
        while not self._stop.is_set():
            self._queue.put(self._fetch_state())
            self._wake.wait(self.interval)
            self._wake.clear()

    def _fetch_state(self) -> dict:
        try:
            data = api.fetch_usage(self.cfg)
            usages = api.summarize(data)
            if not usages:
                raise api.ApiError("接口未返回用量数据")
            return {"ok": True, "usages": usages, "error": None, "updated": time.strftime("%H:%M:%S")}
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "usages": self.state.get("usages", []),
                "error": f"刷新失败: {exc}",
                "updated": None,
            }

    def _poll(self) -> None:
        changed = False
        latest = None
        while True:
            try:
                latest = self._queue.get_nowait()
            except queue.Empty:
                break
        if latest is not None:
            self.state = latest
            self._next_refresh = time.time() + self.interval
            changed = True
        countdown = self._countdown_text()
        # 倒计时秒数变化或状态更新时重绘（折叠态也显示倒计时，需每秒刷新）
        if changed or countdown != self._last_countdown:
            self._last_countdown = countdown
            self._redraw()
        self.root.after(200, self._poll)

    def _countdown_text(self) -> str:
        if self._next_refresh is None:
            return "--:--"
        remaining = max(0, int(self._next_refresh - time.time()))
        return f"{remaining // 60:02d}:{remaining % 60:02d}"

    def refresh_now(self) -> None:
        self._wake.set()

    def set_interval(self, seconds: int) -> None:
        self.interval = seconds
        self.cfg["interval"] = seconds
        config.save(self.cfg)
        self.refresh_now()

    def quit(self) -> None:
        self._stop.set()
        self._wake.set()
        self.root.destroy()

    # ---------- 交互 ----------
    def _on_press(self, event) -> None:
        self._press = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y())
        self._moved = False

    def _on_motion(self, event) -> None:
        if self._press is None:
            return
        px, py, wx, wy = self._press
        dx, dy = event.x_root - px, event.y_root - py
        if abs(dx) + abs(dy) > 4:
            self._moved = True
            self.root.geometry(f"+{wx + dx}+{wy + dy}")

    def _on_release(self, event) -> None:
        press, self._press = self._press, None
        if press is None:
            return
        moved, self._moved = self._moved, False
        if moved:
            self._remember_position()
            return
        zone = self._hit_test(event.x, event.y)
        if zone == "widget":
            self.expanded = not self.expanded
            self._redraw()
        elif zone == "collapse":
            self.expanded = False
            self._redraw()
        elif zone == "refresh":
            self.refresh_now()
        elif zone == "interval":
            idx = INTERVAL_CHOICES.index(self.interval) if self.interval in INTERVAL_CHOICES else 2
            self.set_interval(INTERVAL_CHOICES[(idx + 1) % len(INTERVAL_CHOICES)])
        elif zone == "open":
            webbrowser.open(PAGE_URL)
        elif zone == "repo":
            webbrowser.open(REPO_URL)
        elif zone == "exit":
            self.quit()

    def _hit_test(self, x: int, y: int) -> str | None:
        for name, x1, y1, x2, y2 in self._zones:
            if x1 <= x <= x2 and y1 <= y <= y2:
                return name
        return None

    # ---------- 绘制 ----------
    def _rrect(self, x1: int, y1: int, x2: int, y2: int, r: int, **kw) -> int:
        r = max(1, min(r, (x2 - x1) // 2, (y2 - y1) // 2))
        pts = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return self.canvas.create_polygon(pts, smooth=True, **kw)

    def _bar(self, x1: int, y1: int, x2: int, y2: int, fraction: float, color: str) -> None:
        self._rrect(x1, y1, x2, y2, (y2 - y1) // 2, fill=BAR_TRACK, outline="")
        width = int((x2 - x1 - 2) * max(0.0, min(1.0, fraction)))
        if width > 0:
            self._rrect(x1 + 1, y1 + 1, x1 + 1 + width, y2 - 1, (y2 - y1) // 2, fill=color, outline="")

    def _button(self, x1: int, y1: int, x2: int, y2: int, label: str, zone: str) -> None:
        self._rrect(x1, y1, x2, y2, 8, fill=BTN_BG, outline="")
        self.canvas.create_text(
            (x1 + x2) // 2, (y1 + y2) // 2, text=label,
            font=(FONT, 9), fill=TEXT_MAIN,
        )
        self._zones.append((zone, x1, y1, x2, y2))

    def _redraw(self) -> None:
        self.canvas.delete("all")
        self._zones = []
        if self.expanded:
            self._draw_expanded()
        else:
            self._draw_widget()

    def _draw_widget(self) -> None:
        w, h = WIDGET_W, WIDGET_H
        self.canvas.config(width=w, height=h)
        self._apply_geometry(w, h)
        self._rrect(2, 2, w - 2, h - 2, 20, fill=PANEL_BG, outline="")

        primary = api.pick_primary(self.state["usages"], self.cfg.get("primary_period"))
        if self.state["ok"] and primary is not None:
            pct = primary["remaining"] * 100
            color = _color_for(pct)
            self.canvas.create_text(
                12, 12, text=primary["period"], font=(FONT, 8), fill=TEXT_SUB, anchor="w"
            )
            self.canvas.create_text(
                w - 12, 12, text=f"⏳ {self._countdown_text()}",
                font=(FONT, 8), fill="#7fd1ff", anchor="e",
            )
            self.canvas.create_text(
                w // 2, 30, text=f"{pct:.1f}%", font=(FONT, 13, "bold"), fill=color
            )
            self._bar(14, h - 11, w - 14, h - 6, primary["usage"], color)
        else:
            self.canvas.create_text(w // 2, h // 2, text="…", font=(FONT, 14), fill=TEXT_SUB)

        # 整个挂件可点击展开
        self._zones.append(("widget", 2, 2, w - 2, h - 2))

    def _draw_expanded(self) -> None:
        w = PANEL_W
        header_h = 40
        row_h = 56
        countdown_h = 22
        n = max(1, len(self.state["usages"]) if self.state["ok"] else 0)
        controls_h = 36
        footer_h = 36
        h = header_h + n * row_h + countdown_h + controls_h + footer_h + 10

        self.canvas.config(width=w, height=h)
        self._apply_geometry(w, h)
        self._rrect(2, 2, w - 2, h - 2, 14, fill=PANEL_BG, outline="")

        # 标题栏
        self.canvas.create_text(16, 20, text="天翼云用量监视器", font=(FONT, 11, "bold"), fill=TEXT_MAIN, anchor="w")
        self.canvas.create_text(
            w - 44, 20, text="—", font=(FONT, 11, "bold"), fill=TEXT_SUB
        )
        self._zones.append(("collapse", w - 56, 8, w - 32, 32))

        if not self.state["ok"]:
            self.canvas.create_text(
                w // 2, header_h + 40, text=self.state["error"] or "正在获取数据…",
                font=(FONT, 10), fill="#e57373", width=w - 32,
            )
        else:
            y = header_h + 4
            for usage in self.state["usages"]:
                pct = usage["remaining"] * 100
                color = _color_for(pct)
                self.canvas.create_text(16, y + 8, text=usage["period"], font=(FONT, 10), fill=TEXT_MAIN, anchor="w")
                self.canvas.create_text(
                    w - 16, y + 8, text=f"剩余 {pct:.1f}%", font=(FONT, 10, "bold"), fill=color, anchor="e"
                )
                self._bar(16, y + 22, w - 16, y + 30, usage["usage"], color)
                if usage["tips"]:
                    self.canvas.create_text(
                        16, y + 42, text=f"⏱ {usage['tips']}", font=(FONT, 8), fill=TEXT_SUB, anchor="w"
                    )
                y += row_h

        # 自动刷新倒计时栏
        cy = header_h + n * row_h + 2
        self._rrect(16, cy, w - 16, cy + 20, 8, fill="#262c33", outline="")
        self.canvas.create_text(
            24, cy + 10,
            text=f"⏳ 下次自动刷新 {self._countdown_text()} 后",
            font=(FONT, 9), fill="#7fd1ff", anchor="w",
        )
        self._zones.append(("refresh", 16, cy, w - 16, cy + 20))  # 点倒计时栏也可立即刷新

        # 控制按钮行
        by = cy + countdown_h + 2
        gap = 8
        bw = (w - 32 - gap * 3) // 4
        self._button(16, by, 16 + bw, by + 24, "刷新", "refresh")
        self._button(16 + (bw + gap), by, 16 + (bw + gap) * 2 - gap, by + 24, _fmt_interval(self.interval), "interval")
        self._button(16 + (bw + gap) * 2, by, 16 + (bw + gap) * 3 - gap, by + 24, "页面", "open")
        self._button(16 + (bw + gap) * 3, by, w - 16, by + 24, "退出", "exit")

        # 底部状态行
        status = f"更新于 {self.state['updated']}" if self.state["updated"] else "等待刷新…"
        expiry = api.token_expiry(self.cfg)
        if expiry is not None and (expiry - datetime.now()).total_seconds() < 86400:
            status += "   ⚠ 凭证即将过期"
        self.canvas.create_text(16, h - footer_h + 10, text=status, font=(FONT, 8), fill=TEXT_SUB, anchor="w")

        # 最底一行：仓库地址（可点击打开）
        uy = h - 11
        self.canvas.create_text(16, uy, text=REPO_TEXT, font=(FONT, 8), fill="#6b7680", anchor="w")
        self._zones.append(("repo", 12, uy - 9, 16 + int(len(REPO_TEXT) * 5.6), uy + 9))

    # ---------- 运行 ----------
    def run(self) -> None:
        self.root.mainloop()
