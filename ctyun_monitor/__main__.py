"""命令行入口。

用法：
    uv run ctyun-monitor                 # 首次进入配置向导，随后启动托盘
    uv run ctyun-monitor --capture       # 重新走调试浏览器捕获流程
    uv run ctyun-monitor --curl-file a.txt   # 从文件读取 cURL 命令
    uv run ctyun-monitor --interval 60   # 指定初始刷新间隔（秒）
    uv run ctyun-monitor --once          # 只拉取一次并打印，不启动悬浮窗
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from . import api, capture, config
from .curl_parser import CurlParseError, parse_curl

LOCK_FILE = config.LOCK_PATH


def _setup_stdio() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass


def _apply_captured(cfg: dict, captured: dict) -> dict:
    cfg.update(captured)
    cfg["captured_at"] = time.time()
    config.save(cfg)
    return cfg


def _wizard() -> dict:
    cfg = config.load()
    if config.is_configured(cfg):
        return cfg

    print("=" * 56)
    print("天翼云用量监视器 - 首次配置")
    print("=" * 56)
    print("说明：接口签名 (web-signature) 与登录身份绑定，")
    print("需要捕获一次浏览器发出的真实签名请求。")
    print()
    print("  [1] 调试浏览器自动捕获（推荐：登录一次即可）")
    print("  [2] 手动粘贴 cURL 命令（DevTools -> 复制为 cURL）")
    print()

    choice = input("请选择 [1/2]（回车默认 1）: ").strip() or "1"
    while True:
        try:
            if choice == "1":
                captured = capture.capture_via_browser()
            else:
                captured = _read_curl_interactive()
            if captured:
                break
            print("未能捕获到签名请求，请重试。")
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"出错：{exc}")
        choice = input("请重新选择 [1/2]: ").strip() or "1"

    _apply_captured(cfg, captured)
    print(f"[OK] 配置已保存到 {config.CONFIG_PATH}")
    return cfg


def _read_curl_interactive() -> dict:
    print("请在浏览器打开用量页面 -> F12 -> Network -> 找到 usage/detail 请求")
    print("右键 -> Copy -> Copy as cURL，然后粘贴到下方，最后单独一行输入 end 结束：")
    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip().lower() == "end" or (line.strip() == "" and lines):
            break
        lines.append(line)
    return parse_curl("\n".join(lines))


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _acquire_single_instance() -> int | None:
    """文件锁防止多实例叠窗。返回已运行实例的 pid（冲突时），成功获锁返回 None。"""
    try:
        if LOCK_FILE.exists():
            try:
                old_pid = int(LOCK_FILE.read_text().strip())
            except ValueError:
                old_pid = 0
            if old_pid and old_pid != os.getpid() and _pid_alive(old_pid):
                return old_pid
        LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass
    return None


def _release_single_instance() -> None:
    try:
        if LOCK_FILE.exists() and LOCK_FILE.read_text().strip() == str(os.getpid()):
            LOCK_FILE.unlink()
    except OSError:
        pass


def _print_usage(cfg: dict) -> None:
    data = api.fetch_usage(cfg)
    usages = api.summarize(data)
    print(f"接口地址: {cfg['url']}")
    expiry = api.token_expiry(cfg)
    if expiry is not None:
        print(f"登录凭证(YL-Token)过期时间: {expiry:%Y-%m-%d %H:%M:%S}")
    print("-" * 48)
    for usage in usages:
        print(
            f"{usage['period']:<6} 已用 {usage['usage'] * 100:5.1f}%"
            f"  剩余 {usage['remaining'] * 100:5.1f}%   {usage['tips']}"
        )


def main(argv: list[str] | None = None) -> int:
    _setup_stdio()
    parser = argparse.ArgumentParser(prog="ctyun-monitor", description="天翼云用量监视器：Coding 套餐剩余用量悬浮窗监控")
    parser.add_argument("--once", action="store_true", help="只请求一次并打印结果，不启动悬浮窗")
    parser.add_argument("--capture", action="store_true", help="重新通过调试浏览器捕获登录凭证")
    parser.add_argument("--curl-file", help="从文件读取 cURL 命令并保存为配置")
    parser.add_argument("--interval", type=int, help="刷新间隔（秒）")
    args = parser.parse_args(argv)

    cfg = config.load()

    if args.curl_file:
        try:
            captured = parse_curl(open(args.curl_file, encoding="utf-8", errors="replace").read())
        except (OSError, CurlParseError) as exc:
            print(f"解析 cURL 失败: {exc}")
            return 1
        cfg = _apply_captured(cfg, captured)
        print(f"[OK] 配置已保存到 {config.CONFIG_PATH}")

    if args.capture:
        try:
            captured = capture.capture_via_browser()
        except Exception as exc:  # noqa: BLE001
            print(f"捕获失败: {exc}")
            return 1
        if not captured:
            print("捕获超时，未捕获到签名请求。")
            return 1
        cfg = _apply_captured(cfg, captured)
        print(f"[OK] 配置已保存到 {config.CONFIG_PATH}")

    if not config.is_configured(cfg):
        try:
            cfg = _wizard()
        except KeyboardInterrupt:
            print("\n已取消配置。")
            return 1

    if args.interval:
        cfg["interval"] = max(10, args.interval)
        config.save(cfg)

    try:
        if args.once:
            _print_usage(cfg)
            return 0
    except api.ApiError as exc:
        print(f"请求失败: {exc}")
        return 1

    # 启动悬浮窗前先验证一次凭证有效性
    try:
        _print_usage(cfg)
    except api.ApiError as exc:
        print(f"警告: {exc}")
        print("凭证可能已失效，是否重新捕获登录? [y/N]")
        if input().strip().lower() == "y":
            return main(["--capture"])

    from .widget import FloatingWidget

    running = _acquire_single_instance()
    if running is not None:
        print(f"已有实例在运行 (pid={running})，请勿重复启动；可在悬浮窗展开后点「退出」。")
        return 0

    print("天翼云用量监视器已启动：点击挂件展开用量图表，拖拽可移动，展开态可调整刷新间隔。")
    try:
        FloatingWidget(cfg).run()
    except KeyboardInterrupt:
        pass
    finally:
        _release_single_instance()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
