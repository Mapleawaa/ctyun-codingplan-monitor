"""通过 CDP 调试浏览器自动捕获用量接口的签名请求头与 Cookie。

流程：
1. 启动带 --remote-debugging-port 的 Chrome/Edge，打开天翼云登录页；
2. 用户手动完成登录并进入 Token 用量页面；
3. 页面请求 codingplan/usage/detail 时，通过 CDP Network 域截获完整请求头，
   再用 Network.getCookies 取出登录 Cookie；
4. 持久化后即可离线回放该签名请求。
"""

from __future__ import annotations

import itertools
import json
import socket
import subprocess
import tempfile
import time
import urllib.request

import websocket

from . import api

# 由 HTTP 客户端或浏览器自动管理的头，回放时剔除
DROP_HEADERS = {
    "host",
    "content-length",
    "connection",
    "accept-encoding",
    "cookie",  # Cookie 单独经 Network.getCookies 获取
    ":authority",
    ":method",
    ":path",
    ":scheme",
}

BROWSER_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Users\{user}\AppData\Local\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]


def find_browser() -> str | None:
    import os

    for candidate in BROWSER_CANDIDATES:
        path = candidate.format(user=os.environ.get("USERNAME", ""))
        if os.path.exists(path):
            return path
    return None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _list_targets(port: int) -> list[dict]:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=3) as resp:
        return json.loads(resp.read().decode("utf-8"))


def capture_via_browser(
    page_url: str = api.PAGE_URL,
    timeout: float = 600.0,
    browser_path: str | None = None,
) -> dict | None:
    """启动调试浏览器并等待捕获，成功返回 {"url","headers","cookie"}，超时返回 None。"""
    exe = browser_path or find_browser()
    if not exe:
        raise RuntimeError(
            "未找到 Chrome/Edge，请先安装，或使用 cURL 手动粘贴方式配置"
        )

    port = _free_port()
    profile = tempfile.mkdtemp(prefix="ctun_debug_profile_")
    print(f"[捕获] 启动调试浏览器：{exe}")
    print("[捕获] 请在打开的浏览器中完成登录，登录成功后会自动进入用量页并触发请求。")
    print(f"[捕获] 等待捕获中（最长 {int(timeout)} 秒）……")

    proc = subprocess.Popen(
        [
            exe,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--start-maximized",
            page_url,
        ]
    )
    try:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                targets = [
                    t
                    for t in _list_targets(port)
                    if t.get("type") == "page" and "eaichat.ctyun.cn" in t.get("url", "")
                ]
            except (OSError, ValueError):
                time.sleep(1.0)
                continue
            if not targets:
                time.sleep(1.0)
                continue
            result = _watch_target(targets[0], deadline)
            if result:
                print("[捕获] 已捕获到签名请求。")
                return result
        return None
    finally:
        try:
            proc.terminate()
        except OSError:
            pass


def _watch_target(target: dict, deadline: float) -> dict | None:
    """监听一个页面目标的网络事件，直到捕获目标请求或连接断开。"""
    try:
        ws = websocket.create_connection(
            target["webSocketDebuggerUrl"], timeout=5, suppress_origin=True
        )
    except (OSError, websocket.WebSocketException):
        return None

    pending: dict[int, dict | None] = {}
    events: list[dict] = []
    ids = itertools.count(1)

    def recv_until(ts: float) -> None:
        ws.settimeout(max(0.1, ts - time.time()))
        message = json.loads(ws.recv())
        if "id" in message and message["id"] in pending:
            pending[message["id"]] = message.get("result") or {}
        elif message.get("method") == "Network.requestWillBeSent":
            events.append(message["params"])

    def rpc(method: str, params: dict | None = None, wait: float = 10.0) -> dict:
        mid = next(ids)
        pending[mid] = None
        ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        end = time.time() + wait
        while time.time() < end and pending.get(mid) is None:
            recv_until(end)
        return pending.pop(mid) or {}

    try:
        rpc("Network.enable")
        while time.time() < deadline:
            while events:
                params = events.pop(0)
                request = params.get("request") or {}
                url = request.get("url", "")
                if api.API_MARK in url:
                    cookies = rpc("Network.getCookies", {"urls": [url]})
                    cookie = "; ".join(
                        f"{c['name']}={c['value']}" for c in cookies.get("cookies", [])
                    )
                    headers = {
                        k.lower(): v
                        for k, v in (request.get("headers") or {}).items()
                        if k.lower() not in DROP_HEADERS
                    }
                    return {"url": url, "headers": headers, "cookie": cookie}
            try:
                recv_until(deadline)
            except (websocket.WebSocketTimeoutException, TimeoutError):
                pass
            except (websocket.WebSocketConnectionClosedException, ConnectionError, OSError):
                return None
        return None
    finally:
        try:
            ws.close()
        except OSError:
            pass
