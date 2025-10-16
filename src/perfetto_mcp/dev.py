"""MCP 开发工具的开发入口点。"""

import os
import sys
from pathlib import Path

# 确保在作为原始文件运行时项目 `src` 目录在 sys.path 上
current_file = Path(__file__).resolve()
src_dir = current_file.parents[1]  # .../repo/src
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from perfetto_mcp.server import create_server  # type: ignore


# `mcp dev` 工具期望的顶级服务器实例
mcp = create_server()

__all__ = ["mcp"]
