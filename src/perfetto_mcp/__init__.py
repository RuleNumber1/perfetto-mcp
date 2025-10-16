"""Perfetto MCP 服务器 - 用于分析 Perfetto 跟踪文件的模型上下文协议服务器。"""

__version__ = "0.1.0"
__author__ = "Antariksh"

import logging
import os
import signal
import sys

from .connection_manager import ConnectionManager
from .server import create_server

__all__ = ["ConnectionManager", "create_server"]


logger = logging.getLogger(__name__)


def _setup_signal_handlers() -> None:
    """安装信号处理器，用于在 SIGINT/SIGTERM 时立即终止。

    这镜像了本地开发期间使用的行为，确保快速关闭而不留下后台线程挂起。
    """

    def _signal_handler(signum, frame):  # type: ignore[unused-argument]
        logger.info("接收到关闭信号，立即退出...")
        os._exit(0)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)


def main() -> None:
    """包入口点，通过 stdio 运行 MCP 服务器。

    同时启用 `python -m perfetto_mcp` 和 `perfetto-mcp` 控制台脚本。
    """
    _setup_signal_handlers()

    try:
        mcp = create_server()
        mcp.run(transport="stdio")
    except KeyboardInterrupt:
        logger.info("接收到键盘中断，优雅关闭...")
        sys.exit(0)
    except Exception as exc:  # pragma: no cover - 防御性代码
        logger.error(f"服务器错误: {exc}")
        sys.exit(1)
