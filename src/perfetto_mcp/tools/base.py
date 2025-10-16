"""所有Perfetto MCP工具的基础工具类。"""

import json
import logging
from typing import Callable, Any, Dict, Optional
from ..connection_manager import ConnectionManager

logger = logging.getLogger(__name__)


class ToolError(Exception):
    """携带结构化错误代码和消息的自定义异常。"""

    def __init__(self, code: str, message: str, details: Optional[str] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


class BaseTool:
    """所有具有连接管理和格式化的Perfetto工具的基础类。"""

    def __init__(self, connection_manager: ConnectionManager):
        """使用连接管理器初始化工具。

        参数：
            connection_manager: 共享连接管理器实例
        """
        self.connection_manager = connection_manager

    def execute_with_connection(self, trace_path: str, operation: Callable) -> Any:
        """使用托管连接和自动重连接执行操作。

        参数：
            trace_path: 跟踪文件路径
            operation: 接受TraceProcessor并返回结果的函数

        返回：
            Any: 操作的结果

        抛出：
            FileNotFoundError: 如果跟踪文件不存在
            ConnectionError: 如果连接失败
            Exception: 操作中的任何其他错误
        """
        try:
            tp = self.connection_manager.get_connection(trace_path)
            return operation(tp)
        except (ConnectionError, Exception) as e:
            # 检查是否为可能从重连接中受益的连接相关错误
            if self._should_retry_on_error(e):
                logger.info(f"Attempting reconnection due to error: {e}")
                try:
                    tp = self.connection_manager._reconnect(trace_path)
                    return operation(tp)
                except Exception as reconnect_error:
                    logger.error(f"Reconnection attempt failed: {reconnect_error}")
                    # Raise the original error if reconnection fails
                    raise e
            else:
                # Don't retry for errors like FileNotFoundError
                raise e

    def _should_retry_on_error(self, error: Exception) -> bool:
        """确定错误是否应触发重连接尝试。

        参数：
            error: 发生的异常

        返回：
            bool: 如果应尝试重连接则为True
        """
        # 对于文件未找到错误不重试
        if isinstance(error, FileNotFoundError):
            return False

        # 对于连接错误或其他可能为连接相关的异常进行重试
        if isinstance(error, ConnectionError):
            return True

        # 检查错误消息是否暗示连接问题
        error_str = str(error).lower()
        connection_indicators = [
            'connection', 'broken pipe', 'socket', 'network', 'timeout',
            'disconnected', 'closed', 'reset', 'refused'
        ]

        for indicator in connection_indicators:
            if indicator in error_str:
                return True

        return False

    # -------------------------
    # 统一响应助手
    # -------------------------
    def _make_envelope(
        self,
        *,
        trace_path: Optional[str],
        process_name: Optional[str],
        success: bool,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """创建标准响应信封。"""
        return {
            "processName": process_name or "not-specified",
            "tracePath": trace_path,
            "success": success,
            "error": error,
            "result": result or {},
        }

    def _error(self, code: str, message: str, details: Optional[str] = None) -> Dict[str, Any]:
        """创建标准化的错误对象。"""
        err: Dict[str, Any] = {"code": code, "message": message}
        if details:
            err["details"] = details
        return err

    def run_formatted(
        self,
        trace_path: str,
        process_name: Optional[str],
        op: Callable[[Any], Dict[str, Any]],  # (tp) -> Dict[str, Any] (result payload)
    ) -> str:
        """使用连接管理运行操作并返回JSON信封字符串。"""
        try:
            def wrapped(tp):
                result = op(tp)
                return self._make_envelope(
                    trace_path=trace_path,
                    process_name=process_name,
                    success=True,
                    result=result,
                )

            envelope = self.execute_with_connection(trace_path, wrapped)
        except ToolError as te:
            envelope = self._make_envelope(
                trace_path=trace_path,
                process_name=process_name,
                success=False,
                error=self._error(te.code, te.message, te.details),
            )
        except FileNotFoundError as fnf:
            envelope = self._make_envelope(
                trace_path=trace_path,
                process_name=process_name,
                success=False,
                error=self._error("FILE_NOT_FOUND", "Trace file not found", str(fnf)),
            )
        except ConnectionError as ce:
            envelope = self._make_envelope(
                trace_path=trace_path,
                process_name=process_name,
                success=False,
                error=self._error("CONNECTION_FAILED", "Could not connect to trace processor", str(ce)),
            )
        except Exception as e:
            envelope = self._make_envelope(
                trace_path=trace_path,
                process_name=process_name,
                success=False,
                error=self._error("INTERNAL_ERROR", str(e)),
            )

        return json.dumps(envelope, indent=2)
