"""配置读写。

开发态：配置文件位于项目根目录 config.json；
打包后（PyInstaller frozen）：位于 EXE 同目录，便于绿色便携使用。
配置含敏感凭证，已加入 .gitignore，绝不随程序分发。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _config_dir() -> Path:
    if getattr(sys, "frozen", False):  # PyInstaller 打包后
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


CONFIG_PATH = _config_dir() / "config.json"
LOCK_PATH = _config_dir() / "ctyun_monitor.lock"

DEFAULTS = {
    "interval": 300,  # 刷新间隔（秒）
    "primary_period": "近5小时",  # 悬浮窗主显示的周期
    "url": "",
    "headers": {},
    "cookie": "",
    "captured_at": 0,  # 捕获时间戳
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg.update(data)
        except (OSError, ValueError):
            pass
    if not isinstance(cfg.get("headers"), dict):
        cfg["headers"] = {}
    return cfg


def save(cfg: dict) -> None:
    data = {k: cfg.get(k, DEFAULTS.get(k)) for k in DEFAULTS}
    CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def is_configured(cfg: dict) -> bool:
    return bool(cfg.get("url") and cfg.get("headers"))
