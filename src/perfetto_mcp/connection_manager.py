"""用于持久化 TraceProcessor 连接的连接管理器。"""

import threading
import logging
from typing import Optional
from perfetto.trace_processor import TraceProcessor

logger = logging.getLogger(__name__)


class ConnectionManager:
    """管理持久化 TraceProcessor 连接，支持重连功能。"""
    
    def __init__(self):
        self._current_trace_path: Optional[str] = None
        self._current_connection: Optional[TraceProcessor] = None
        self._lock = threading.Lock()  # Thread safety
        
    def get_connection(self, trace_path: str) -> TraceProcessor:
        """获取或创建 trace_path 的连接，支持自动重连。
        
        Args:
            trace_path: Perfetto 跟踪文件的路径
            
        Returns:
            TraceProcessor: 到跟踪的活动连接
            
        Raises:
            FileNotFoundError: 如果跟踪文件不存在
            ConnectionError: 如果连接失败
        """
        with self._lock:
            # 如果路径不同，关闭现有连接并打开新连接
            if self._current_trace_path != trace_path:
                logger.info(f"切换跟踪连接从 {self._current_trace_path} 到 {trace_path}")
                self._close_current_unsafe()
                self._current_trace_path = trace_path
                self._current_connection = self._create_connection(trace_path)
                
            # 如果相同路径但没有连接，创建新连接
            elif self._current_connection is None:
                logger.info(f"创建新连接到 {trace_path}")
                self._current_connection = self._create_connection(trace_path)
                
            # 返回前测试连接健康状态
            if not self._is_connection_healthy():
                logger.warning(f"连接到 {trace_path} 的连接似乎不健康，重新连接")
                self._current_connection = self._reconnect_unsafe(trace_path)
                
            return self._current_connection
    
    def _create_connection(self, trace_path: str) -> TraceProcessor:
        """创建新的 TraceProcessor 连接。
        
        Args:
            trace_path: 跟踪文件的路径
            
        Returns:
            TraceProcessor: 新连接
            
        Raises:
            FileNotFoundError: 如果跟踪文件不存在
            ConnectionError: 如果连接失败
        """
        try:
            tp = TraceProcessor(trace=trace_path)
            logger.info(f"Successfully connected to trace: {trace_path}")
            return tp
        except FileNotFoundError as e:
            logger.error(f"Trace file not found: {trace_path}")
            raise FileNotFoundError(
                f"Failed to open the trace file. Please double-check the trace_path "
                f"you supplied. Underlying error: {e}"
            )
        except Exception as e:
            logger.error(f"Failed to connect to trace: {trace_path}, error: {e}")
            raise ConnectionError(f"Could not connect to trace processor: {e}")
    
    def _is_connection_healthy(self) -> bool:
        """检查当前连接是否健康。
        
        Returns:
            bool: 如果连接健康则为 True，否则为 False
        """
        if self._current_connection is None:
            return False
            
        try:
            # 尝试简单查询来测试连接健康状态
            qr_it = self._current_connection.query('SELECT 1 as test_query LIMIT 1;')
            # 消费迭代器以确保查询执行
            list(qr_it)
            return True
        except Exception as e:
            logger.warning(f"连接健康检查失败: {e}")
            return False
    
    def _reconnect(self, trace_path: str) -> TraceProcessor:
        """在连接失败后重新连接到跟踪文件。
        
        Args:
            trace_path: 跟踪文件的路径
            
        Returns:
            TraceProcessor: 新连接
            
        Raises:
            ConnectionError: 如果重连失败
        """
        with self._lock:
            return self._reconnect_unsafe(trace_path)
    
    def _reconnect_unsafe(self, trace_path: str) -> TraceProcessor:
        """不获取锁进行重连（仅供内部使用）。
        
        Args:
            trace_path: 跟踪文件的路径
            
        Returns:
            TraceProcessor: 新连接
        """
        logger.info(f"尝试重新连接到 {trace_path}")
        
        # 关闭现有连接
        self._close_current_unsafe()
        
        # 创建新连接
        try:
            self._current_connection = self._create_connection(trace_path)
            self._current_trace_path = trace_path
            logger.info(f"成功重新连接到 {trace_path}")
            return self._current_connection
        except Exception as e:
            logger.error(f"重连失败 {trace_path}: {e}")
            raise ConnectionError(f"重连失败: {e}")
    
    def close_current(self):
        """如果存在当前连接，则关闭它。"""
        with self._lock:
            self._close_current_unsafe()
    
    def _close_current_unsafe(self):
        """不获取锁关闭当前连接（仅供内部使用）。"""
        if self._current_connection is not None:
            try:
                logger.info(f"Closing connection to {self._current_trace_path}")
                self._current_connection.close()
            except Exception as e:
                logger.warning(f"Error closing connection: {e}")
            finally:
                self._current_connection = None
                self._current_trace_path = None
    
    def cleanup(self):
        """清理方法，由 MCP 服务器关闭生命周期调用。"""
        logger.info("Cleaning up connection manager")
        self.close_current()
    
    def get_current_trace_path(self) -> Optional[str]:
        """获取当前连接的跟踪路径。
        
        Returns:
            Optional[str]: 当前跟踪路径，如果没有连接则为 None
        """
        with self._lock:
            return self._current_trace_path
    
    def is_connected(self) -> bool:
        """检查是否存在活动连接。
        
        Returns:
            bool: 如果已连接则为 True，否则为 False
        """
        with self._lock:
            return self._current_connection is not None
