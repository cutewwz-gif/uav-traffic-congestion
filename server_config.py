"""Web 服务绑定配置：环境变量 + 端口占用自动避让。"""

from __future__ import annotations

import os
import socket

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
MAX_PORT_TRIES = 20


def _is_port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def resolve_server_bind(
    host: str | None = None,
    preferred_port: int | None = None,
) -> tuple[str, int]:
    """解析可用 ``(host, port)``。

    优先级：
    1. 函数参数 ``host`` / ``preferred_port``
    2. 环境变量 ``HOST`` / ``PORT``
    3. 默认值 ``127.0.0.1:8000``

    若首选端口被占用，依次尝试 ``port+1 … port+19``。
    """
    bind_host = host or os.environ.get("HOST", DEFAULT_HOST)
    base_port = preferred_port
    if base_port is None:
        base_port = int(os.environ.get("PORT", DEFAULT_PORT))

    for offset in range(MAX_PORT_TRIES):
        port = base_port + offset
        if _is_port_free(bind_host, port):
            if offset > 0:
                print(
                    f"[warn] 端口 {base_port} 已被占用，"
                    f"自动改用 {bind_host}:{port}"
                )
            return bind_host, port

    raise RuntimeError(
        f"无法在 {bind_host} 上绑定端口 "
        f"{base_port}~{base_port + MAX_PORT_TRIES - 1}，"
        "请关闭占用进程或设置 PORT 环境变量。"
    )
