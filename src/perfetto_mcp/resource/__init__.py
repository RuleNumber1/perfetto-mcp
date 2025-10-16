"""MCP资源注册包。

暴露单个入口点`register_resources(mcp)`，用于连接
服务器的所有MCP资源。
"""

from mcp.server.fastmcp import FastMCP

from .concepts import register_concepts_resource
from .trace_analysis import register_trace_analysis_resource


def register_resources(mcp: FastMCP) -> None:
    """在给定的服务器实例上注册所有MCP资源。"""
    register_concepts_resource(mcp)
    register_trace_analysis_resource(mcp)

