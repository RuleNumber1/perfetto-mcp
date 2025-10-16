"""堆支配树分析器工具。

分析进程的最新堆图快照，并按类聚合对象
实例以显示具有影响分类的内存占用类型。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .base import BaseTool, ToolError
from ..utils.query_helpers import format_query_result_row

logger = logging.getLogger(__name__)


class HeapDominatorTreeAnalyzerTool(BaseTool):
    """分析堆支配树以识别内存密集型类。

    从目标进程的最新堆图快照生成按总自身/本机大小(MB)排序的顶级类列表，
    包括可用时的可达性指标。如果跟踪中某些列/模块不可用，则回退到简化查询。
    """

    def heap_dominator_tree_analyzer(
        self,
        trace_path: str,
        process_name: str,
        max_classes: int = 20,
    ) -> str:
        """分析给定进程的堆支配树。

        参数
        ----------
        trace_path : str
            Perfetto跟踪文件路径。
        process_name : str
            要分析的精确进程名称。
        max_classes : int, optional
            要返回的最大类数(1-50)。默认：20。

        返回
        -------
        str
            包含字段的JSON信封：
            - processName, tracePath, success, error, result
            - result: {
                totalCount,
                classes: [{ display_name, instance_count, self_size_mb, native_size_mb,
                            total_size_mb, avg_reachability, min_root_distance, memory_impact }...],
                filters: { process_name, max_classes },
                notes: []
              }
        """

        def _op(tp):
            if not process_name or not isinstance(process_name, str):
                raise ToolError("INVALID_PARAMETERS", "process_name must be a non-empty string")

            try:
                limit = max(1, min(int(max_classes), 50))
            except Exception:
                raise ToolError("INVALID_PARAMETERS", "max_classes must be an integer")

            safe_proc = process_name.replace("'", "''")
            notes: List[str] = []

            # 主要查询(根据规范)，使用dominator_tree模块和扩展列
            primary_sql = f"""
            INCLUDE PERFETTO MODULE android.memory.heap_graph.dominator_tree;

            WITH latest_snapshot AS (
              SELECT MAX(graph_sample_ts) AS snapshot_ts
              FROM heap_graph_object
              WHERE upid = (SELECT upid FROM process WHERE name = '{safe_proc}' LIMIT 1)
            ),
            dominator_analysis AS (
              SELECT 
                hgc.name AS class_name,
                COALESCE(hgc.deobfuscated_name, hgc.name) AS display_name,
                COUNT(hgo.id) AS instance_count,
                SUM(hgo.self_size) / 1024.0 / 1024.0 AS total_self_size_mb,
                SUM(hgo.native_size) / 1024.0 / 1024.0 AS total_native_size_mb,
                AVG(hgo.reachability) AS avg_reachability,
                MIN(hgo.root_distance) AS min_root_distance
              FROM heap_graph_object hgo
              JOIN heap_graph_class hgc ON hgo.type_id = hgc.id
              WHERE hgo.graph_sample_ts = (SELECT snapshot_ts FROM latest_snapshot)
                AND hgo.upid = (SELECT upid FROM process WHERE name = '{safe_proc}' LIMIT 1)
              GROUP BY hgc.id
              ORDER BY total_self_size_mb DESC
              LIMIT {limit}
            )
            SELECT 
              display_name,
              instance_count,
              CAST(total_self_size_mb AS REAL) AS self_size_mb,
              CAST(total_native_size_mb AS REAL) AS native_size_mb,
              CAST(total_self_size_mb + total_native_size_mb AS REAL) AS total_size_mb,
              avg_reachability,
              min_root_distance,
              CASE
                WHEN total_self_size_mb > 50 THEN 'CRITICAL'
                WHEN total_self_size_mb > 20 THEN 'WARNING'
                ELSE 'NORMAL'
              END AS memory_impact
            FROM dominator_analysis;
            """

            # 回退查询：避免可能不存在的模块+列(native_size, reachability, root_distance)
            fallback_sql = f"""
            WITH latest_snapshot AS (
              SELECT MAX(graph_sample_ts) AS snapshot_ts
              FROM heap_graph_object
              WHERE upid = (SELECT upid FROM process WHERE name = '{safe_proc}' LIMIT 1)
            ),
            dominator_analysis AS (
              SELECT 
                hgc.name AS class_name,
                COALESCE(hgc.deobfuscated_name, hgc.name) AS display_name,
                COUNT(hgo.id) AS instance_count,
                SUM(hgo.self_size) / 1024.0 / 1024.0 AS total_self_size_mb
              FROM heap_graph_object hgo
              JOIN heap_graph_class hgc ON hgo.type_id = hgc.id
              WHERE hgo.graph_sample_ts = (SELECT snapshot_ts FROM latest_snapshot)
                AND hgo.upid = (SELECT upid FROM process WHERE name = '{safe_proc}' LIMIT 1)
              GROUP BY hgc.id
              ORDER BY total_self_size_mb DESC
              LIMIT {limit}
            )
            SELECT 
              display_name,
              instance_count,
              CAST(total_self_size_mb AS REAL) AS self_size_mb,
              NULL AS native_size_mb,
              CAST(total_self_size_mb AS REAL) AS total_size_mb,
              NULL AS avg_reachability,
              NULL AS min_root_distance,
              CASE
                WHEN total_self_size_mb > 50 THEN 'CRITICAL'
                WHEN total_self_size_mb > 20 THEN 'WARNING'
                ELSE 'NORMAL'
              END AS memory_impact
            FROM dominator_analysis;
            """

            rows: List[Any] = []

            # 尝试主要查询；如果由于缺少模块/列而失败，则尝试回退
            try:
                rows = list(tp.query(primary_sql))
            except Exception as primary_err:
                msg = str(primary_err).lower()
                logger.info("Primary dominator query failed, attempting fallback: %s", primary_err)
                try:
                    rows = list(tp.query(fallback_sql))
                    # 关于减少列的信息性注释
                    notes.append(
                        "Reduced columns: native_size_mb/reachability/root_distance unavailable; used simplified query"
                    )
                except Exception as fb_err:
                    # If even fallback fails, likely no heap graph in trace
                    if any(s in msg for s in ["heap_graph_object", "heap_graph_class", "no such", "dominator"]):
                        raise ToolError(
                            "HEAP_GRAPH_UNAVAILABLE",
                            "Heap graph data not available in this trace (no heap graph tables or module).",
                            details=f"primary_error={primary_err}; fallback_error={fb_err}",
                        )
                    raise ToolError(
                        "QUERY_FAILED",
                        "Heap dominator analysis query failed.",
                        details=f"primary_error={primary_err}; fallback_error={fb_err}",
                    )

            # 格式化结果
            classes: List[Dict[str, Any]] = []
            columns: Optional[List[str]] = None
            for r in rows:
                if columns is None:
                    columns = list(r.__dict__.keys())
                classes.append(format_query_result_row(r, columns))

            return {
                "totalCount": len(classes),
                "classes": classes,
                "filters": {"process_name": process_name, "max_classes": limit},
                "notes": notes,
            }

        return self.run_formatted(trace_path, process_name, _op)

