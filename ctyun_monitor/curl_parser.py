"""解析 DevTools 复制的 cURL 命令，提取请求 URL、请求头与 Cookie。

支持 Chrome/Edge "Copy as cURL (cmd)"（含 ^" 转义）与 (bash) 两种格式。
"""

from __future__ import annotations

import shlex
from urllib.parse import parse_qs, urlparse

# 这些头由 HTTP 客户端自行管理，回放时不应携带
DROP_HEADERS = {
    "host",
    "content-length",
    "connection",
    "accept-encoding",
    "transfer-encoding",
    ":authority",
    ":method",
    ":path",
    ":scheme",
}


class CurlParseError(ValueError):
    pass


def _normalize(text: str) -> str:
    """去掉 Windows cmd 转义符 ^，并合并换行续行符。"""
    if "^" in text:
        text = text.replace("^", "")
    return text


def parse_curl(text: str) -> dict:
    """解析 cURL 命令。

    返回 {"url": str, "headers": {小写header名: 值}, "cookie": str}
    """
    text = text.strip().strip("`")
    if not text:
        raise CurlParseError("cURL 命令为空")

    tokens = shlex.split(_normalize(text), posix=True)
    if not tokens:
        raise CurlParseError("cURL 命令为空")

    url: str | None = None
    headers: dict[str, str] = {}
    cookie: str | None = None

    i = 0
    while i < len(tokens):
        token = tokens[i]
        low = token.lower()
        if low in ("curl", "curl.exe"):
            i += 1
        elif token in ("--url",) and i + 1 < len(tokens):
            url = tokens[i + 1]
            i += 2
        elif token in ("-H", "--header") and i + 1 < len(tokens):
            _add_header(headers, tokens[i + 1])
            i += 2
        elif token in ("-b", "--cookie") and i + 1 < len(tokens):
            cookie = tokens[i + 1]
            i += 2
        elif token.startswith(("http://", "https://")):
            if url is None:
                url = token
            i += 1
        else:
            i += 1

    if not url:
        raise CurlParseError("未能从命令中解析出请求 URL")

    # cookie 头优先于 -b 参数
    if not cookie:
        cookie = headers.pop("cookie", None) or headers.pop("set-cookie", None)

    for name in DROP_HEADERS:
        headers.pop(name, None)

    return {"url": url, "headers": headers, "cookie": cookie or ""}


def _add_header(headers: dict[str, str], raw: str) -> None:
    name, sep, value = raw.partition(":")
    if not sep:
        return
    name = name.strip().lower()
    value = value.strip()
    if not name:
        return
    if name == "cookie":
        headers[name] = value
    else:
        headers[name] = value


def extract_plan_id(url: str) -> str | None:
    """从用量接口 URL 中提取套餐 id。"""
    try:
        qs = parse_qs(urlparse(url).query)
        return qs.get("id", [None])[0]
    except Exception:
        return None
