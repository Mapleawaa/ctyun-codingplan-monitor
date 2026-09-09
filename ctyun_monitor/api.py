"""用量接口请求与结果解析。"""

from __future__ import annotations

import base64
import json
import re
from datetime import datetime

import httpx

API_MARK = "codingplan/usage/detail"
PAGE_URL = "https://eaichat.ctyun.cn/chat/#/aitoken"


class ApiError(RuntimeError):
    pass


def fetch_usage(cfg: dict, timeout: float = 15.0) -> dict:
    """按已捕获的配置回放请求，返回接口 data 字段。

    服务端会校验 web-signature 与时间戳/随机数/登录身份的绑定关系，
    因此这里必须原样回放捕获时的全部签名头与 Cookie。
    """
    headers = {k: v for k, v in (cfg.get("headers") or {}).items()}
    if cfg.get("cookie"):
        headers["cookie"] = cfg["cookie"]

    resp = httpx.get(cfg["url"], headers=headers, timeout=timeout, follow_redirects=False)
    if resp.status_code == 401:
        raise ApiError("鉴权失败(401)：登录凭证或签名已失效，请重新运行程序捕获登录")
    resp.raise_for_status()

    try:
        payload = resp.json()
    except ValueError as exc:
        raise ApiError(f"接口返回非 JSON：HTTP {resp.status_code}") from exc

    if payload.get("resultCode") != 0:
        raise ApiError(f"接口返回异常: {payload.get('resultMsg') or payload}")

    return payload.get("data") or {}


def summarize(data: dict) -> list[dict]:
    """把 data.usages 转成 [{period, usage, remaining, tips}]，remaining 为剩余比例 0~1。"""
    result = []
    for item in data.get("usages") or []:
        try:
            usage = float(item.get("usage") or 0.0)
        except (TypeError, ValueError):
            usage = 0.0
        result.append(
            {
                "period": item.get("period") or "-",
                "usage": usage,
                "remaining": max(0.0, min(1.0, 1.0 - usage)),
                "tips": item.get("tips") or "",
            }
        )
    return result


def token_expiry(cfg: dict) -> datetime | None:
    """从 Cookie 中的 YL-Token (JWT) 解析过期时间。"""
    cookie = cfg.get("cookie") or ""
    match = re.search(r"YL-Token=([^;\s]+)", cookie)
    if not match:
        return None
    try:
        payload_b64 = match.group(1).split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = payload.get("exp")
        return datetime.fromtimestamp(exp) if exp else None
    except Exception:
        return None


def pick_primary(usages: list[dict], primary_period: str | None) -> dict | None:
    """选择主显示周期，优先匹配配置的周期名，否则取第一个。"""
    if not usages:
        return None
    if primary_period:
        for item in usages:
            if item["period"] == primary_period:
                return item
    return usages[0]
