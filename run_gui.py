"""一键启动 GUI：打开浏览器并运行 FastAPI 服务。"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn

from app import HOST, PORT, app
URL = f"http://localhost:{PORT}"
URL_IP = f"http://{HOST}:{PORT}"


def _wait_until_ready(timeout: float = 20.0) -> bool:
    """轮询直到 uvicorn 真正可访问。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{URL_IP}/api/status", timeout=1.0) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(0.25)
    return False


def _chrome_like_browsers() -> list[str]:
    """查找本机 Edge / Chrome 可执行文件。"""
    candidates = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Google/Chrome/Application/chrome.exe",
    ]
    found: list[str] = []
    for path in candidates:
        if path.is_file():
            found.append(str(path))
    for name in ("msedge", "chrome", "google-chrome"):
        which = shutil.which(name)
        if which:
            found.append(which)
    seen: set[str] = set()
    uniq: list[str] = []
    for item in found:
        if item not in seen:
            seen.add(item)
            uniq.append(item)
    return uniq


def _open_browser() -> None:
    os.environ["NO_PROXY"] = "localhost,127.0.0.1,::1"
    os.environ["no_proxy"] = "localhost,127.0.0.1,::1"

    if not _wait_until_ready():
        print("[warn] 服务启动超时，仍尝试打开浏览器…")

    bypass = "<-loopback>;localhost;127.0.0.1;::1"
    for browser in _chrome_like_browsers():
        try:
            subprocess.Popen(
                [
                    browser,
                    f"--proxy-bypass-list={bypass}",
                    "--new-window",
                    URL,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print(f"[ok] 已用绕过代理方式打开: {browser}")
            print(f"[ok] 地址: {URL}")
            return
        except OSError as exc:
            print(f"[warn] 无法启动 {browser}: {exc}")

    webbrowser.open(URL)
    print(f"[ok] 已用默认浏览器打开: {URL}")
    print("若仍无法访问，请关闭系统代理，或把 localhost/127.0.0.1 加入代理绕过列表。")


def main() -> None:
    print("=" * 56)
    print(" UAV Traffic Congestion Analyzer")
    print(f" Open: {URL}")
    print(f" Bind: {HOST}:{PORT}")
    print(" Models: my_uav_vehicle_single/**/*.{onnx,pt}")
    print(" Tip: set PORT=8001 to force a specific port")
    print("=" * 56)
    print("提示: 若浏览器报「连接被拒绝」，请关闭代理或启用")
    print("      「跳过本地地址 / Bypass localhost」。")
    threading.Thread(target=_open_browser, daemon=True).start()
    uvicorn.run(app, host=HOST, port=PORT, reload=False, log_level="info")


if __name__ == "__main__":
    main()
