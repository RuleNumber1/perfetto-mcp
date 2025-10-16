"""ANR检测工具，用于分析应用程序无响应事件。

注意事项：
- 需要跟踪文件中包含android.anrs数据源；否则返回ANR_DATA_UNAVAILABLE。
- 严重性启发式：如果ANR附近的GC事件>10则为CRITICAL；如果ANR时主线程状态
  指示睡眠/IO等待(S/D)或GC>5则为HIGH；否则为MEDIUM。系统关键进程
  至少升级为HIGH。
- 参数`min_duration_ms`目前仅为信息性，不用于过滤结果。
"""

import json
import logging
from typing import Optional, Dict, Any
from .base import BaseTool, ToolError
from ..utils.query_helpers import format_query_result_row

logger = logging.getLogger(__name__)


class AnrDetectionTool(BaseTool):
    """用于检测和分析Perfetto跟踪中的ANR事件的工具。"""

    def detect_anrs(
        self,
        trace_path: str,
        process_name: Optional[str] = None,
        min_duration_ms: int = 5000,
        time_range: Optional[Dict[str, int]] = None,
    ) -> str:
        """检测ANR事件并返回统一的JSON信封。"""

        def _execute_anr_detection(tp):
            """执行ANR检测查询的内部操作。"""

            # 基于文档构建SQL查询
            sql_query = """
            INCLUDE PERFETTO MODULE android.anrs;

            SELECT 
              process_name,
              pid,
              upid,
              error_id,
              ts,
              subject,
              -- Find main thread state at ANR time
              (SELECT state FROM thread_state ts
               JOIN thread t USING(utid)
               WHERE t.upid = android_anrs.upid 
                 AND t.is_main_thread = 1
                 AND ts.ts <= android_anrs.ts
               ORDER BY ts.ts DESC LIMIT 1) as main_thread_state,
              -- Check for concurrent GC events
              (SELECT COUNT(*) FROM slice s
               WHERE s.name LIKE '%GC%'
                 AND s.ts BETWEEN android_anrs.ts - 5e9 AND android_anrs.ts) as gc_events_near_anr
            FROM android_anrs
            WHERE 1=1
            """

            # 如果指定了进程名称过滤器，则添加
            if process_name:
                sql_query += f" AND process_name GLOB '{process_name}'"

            # 如果指定了时间范围过滤器，则添加
            if time_range:
                if 'start_ms' in time_range:
                    sql_query += f" AND ts >= {time_range['start_ms']} * 1e6"
                if 'end_ms' in time_range:
                    sql_query += f" AND ts <= {time_range['end_ms']} * 1e6"

            sql_query += " ORDER BY ts"

            # 执行查询
            try:
                qr_it = tp.query(sql_query)
            except Exception as e:
                # 检查是否为ANR模块可用性问题
                error_msg = str(e).lower()
                if 'android.anrs' in error_msg or 'no such table' in error_msg:
                    raise ToolError(
                        "ANR_DATA_UNAVAILABLE",
                        "This trace does not contain ANR data. ANR events are typically only available in Android system traces that include the 'android.anrs' data source.",
                    )
                raise

            # 收集并格式化结果
            anrs = []
            columns = None

            for row in qr_it:
                # 从第一行获取列名
                if columns is None:
                    columns = list(row.__dict__.keys())

                # 将行转换为字典
                row_dict = format_query_result_row(row, columns)

                # 将时间戳从纳秒转换为毫秒
                if 'ts' in row_dict and row_dict['ts'] is not None:
                    row_dict['timestampMs'] = int(row_dict['ts'] / 1e6)

                # 添加严重性分析
                severity = self._analyze_anr_severity(row_dict)
                row_dict['severity'] = severity

                anrs.append(row_dict)

            # 仅结果负载；信封由run_formatted添加
            return {
                "totalCount": len(anrs),
                "anrs": anrs,
                "filters": {
                    "process_name": process_name,
                    "min_duration_ms": min_duration_ms,
                    "time_range": time_range,
                },
            }

        return self.run_formatted(trace_path, process_name, _execute_anr_detection)

    def _analyze_anr_severity(self, anr_data: Dict[str, Any]) -> str:
        """
        基于上下文数据分析ANR事件的严重性。
        
        参数：
            anr_data: 包含ANR事件数据的字典
            
        返回：
            str: 严重性级别("CRITICAL", "HIGH", "MEDIUM", "LOW")
        """
        # 从基础严重性开始
        severity = "MEDIUM"
        
        # 检查主线程状态 - 阻塞的主线程更严重
        main_thread_state = anr_data.get('main_thread_state', '')
        if main_thread_state in ['D', 'S']:  # Disk sleep or interruptible sleep
            severity = "HIGH"
        elif main_thread_state == 'R':  # Running - less severe, likely CPU bound
            severity = "MEDIUM"
        
        # 检查GC压力 - 高GC活动表示内存问题
        gc_events = anr_data.get('gc_events_near_anr', 0)
        if gc_events > 10:
            severity = "CRITICAL"
        elif gc_events > 5:
            if severity == "MEDIUM":
                severity = "HIGH"
        
        # 检查进程名称是否为系统关键进程
        process_name = anr_data.get('process_name', '')
        system_critical_processes = [
            'system_server', 'com.android.systemui', 'com.android.launcher'
        ]
        if any(critical in process_name for critical in system_critical_processes):
            if severity in ["LOW", "MEDIUM"]:
                severity = "HIGH"
        
        return severity
