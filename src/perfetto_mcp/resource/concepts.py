"""使用装饰器API将Perfetto文档注册为具体的MCP资源。"""

import logging
from importlib.resources import files as resource_files
from pathlib import Path

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)


def _read_concepts_markdown() -> str:
    """从已安装的包或开发仓库中读取概念markdown文档。

    优先级：
    1) 打包位置：perfetto_mcp/docs/Perfetto-MCP-Concepts.md
    2) 开发环境回退：<repo>/docs/Perfetto-MCP-Concepts.md
    """
    # 首先尝试打包文件
    try:
        packaged_concepts = resource_files("perfetto_mcp") / "docs" / "Perfetto-MCP-Concepts.md"
        if packaged_concepts.is_file():
            return packaged_concepts.read_text(encoding="utf-8")
    except Exception as e:
        logger.debug(f"包概念介绍文件读取失败, 即将尝试dev回退操作: {e}")

    # 开发环境回退
    try:
        repo_root = Path(__file__).resolve().parents[3]
        concepts_file = (repo_root / "docs" / "Perfetto-MCP-Concepts.md").resolve()
        return concepts_file.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"从包和dev路径下读取概念文档失败: {e}")
        raise


def register_concepts_resource(mcp: FastMCP) -> None:
    """为Perfetto概念文档注册具体资源。

    - 通过list_resources()快速发现的具体资源
      URI: resource://perfetto-mcp/concepts
    """

    # resource://perfetto-mcp/concepts
    @mcp.resource(
        "resource://perfetto-mcp/concepts",
        name="perfetto-mcp-concepts",
        title="Perfetto MCP Concepts",
        description="对Perfetto追踪分析和MCP工具用途的参考指导",
        mime_type="text/markdown",
    )
    def read_concepts() -> str:
        return _read_concepts_markdown()
