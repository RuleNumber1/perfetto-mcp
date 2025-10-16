"""按名称过滤切片的切片信息工具。"""

import logging
from typing import Optional, Any, Dict, List
from .base import BaseTool

logger = logging.getLogger(__name__)


class SliceInfoTool(BaseTool):
    """用于检索具有给定名称的切片信息的工具。"""

    def get_slice_info(self, trace_path: str, slice_name: str, process_name: Optional[str] = None) -> str:
        """按精确名称过滤和汇总切片的所有出现。

        返回一个统一的JSON信封，包含：
        - sliceName
        - totalCount
        - durationSummary: { minMs, avgMs, maxMs }
        - timeBounds: { earliestTsMs, latestTsMs, spanMs }
        - examples: 前N个最长的切片(默认50个)及其上下文
        """

        def _to_ms(value_ns: Optional[int | float]) -> Optional[float]:
            if value_ns is None:
                return None
            try:
                return float(value_ns) / 1e6
            except Exception:
                return None

        def _get_slice_info_operation(tp):
            """内部操作，用于获取切片信息并构建结果负载。"""
            # 嵌入SQL字符串的基本清理
            safe_name = slice_name.replace("'", "''")

            # 1) 摘要和时间边界(跨进程全局)，不区分大小写的名称匹配
            summary_query = (
                "SELECT "
                "  COUNT(*) AS total_count, "
                "  MIN(dur) AS min_dur_ns, "
                "  AVG(dur) AS avg_dur_ns, "
                "  MAX(dur) AS max_dur_ns, "
                "  MIN(ts) AS earliest_ts_ns, "
                "  MAX(ts) AS latest_ts_ns "
                f"FROM slice WHERE UPPER(name) = UPPER('{safe_name}')"
            )

            total_count = 0
            min_ms = None
            avg_ms = None
            max_ms = None
            earliest_ms = None
            latest_ms = None
            span_ms = None

            try:
                for row in tp.query(summary_query):
                    total_count = int(getattr(row, "total_count", 0) or 0)
                    min_ms = _to_ms(getattr(row, "min_dur_ns", None))
                    avg_ms = _to_ms(getattr(row, "avg_dur_ns", None))
                    max_ms = _to_ms(getattr(row, "max_dur_ns", None))
                    earliest_ms = _to_ms(getattr(row, "earliest_ts_ns", None))
                    latest_ms = _to_ms(getattr(row, "latest_ts_ns", None))
                    if earliest_ms is not None and latest_ms is not None:
                        span_ms = float(latest_ms) - float(earliest_ms)
                    break
            except Exception as e:
                logger.warning(f"Summary query failed: {e}")

            # 2) 示例：前N个最长的切片及其上下文
            max_examples = 50
            examples_query = (
                "WITH candidates AS (\n"
                "  SELECT s.id, s.ts, s.dur, s.depth, s.category, s.track_id\n"
                "  FROM slice s\n"
                f"  WHERE UPPER(s.name) = UPPER('{safe_name}')\n"
                ")\n"
                "SELECT\n"
                "  c.id AS slice_id,\n"
                "  CAST(c.ts / 1e6 AS INT) AS ts_ms,\n"
                "  CAST((c.ts + c.dur) / 1e6 AS INT) AS end_ts_ms,\n"
                "  CAST(c.dur / 1e6 AS REAL) AS dur_ms,\n"
                "  c.depth,\n"
                "  c.category,\n"
                "  tr.name AS track_name,\n"
                "  th.name AS thread_name,\n"
                "  th.tid,\n"
                "  th.is_main_thread,\n"
                "  p.name AS process_name,\n"
                "  p.pid\n"
                "FROM candidates c\n"
                "JOIN track tr ON c.track_id = tr.id\n"
                "LEFT JOIN thread_track ttr ON c.track_id = ttr.id\n"
                "LEFT JOIN thread th ON ttr.utid = th.utid\n"
                "LEFT JOIN process_track pt ON c.track_id = pt.id\n"
                "LEFT JOIN process p ON COALESCE(th.upid, pt.upid) = p.upid\n"
            )

            examples_query += (
                "ORDER BY c.dur DESC\n"
                f"LIMIT {max_examples};"
            )

            # 3) 相似名称(通配符包含)，不区分大小写
            other_slices: List[str] = []
            other_slices_query = (
                "SELECT name, COUNT(*) AS cnt\n"
                "FROM slice\n"
                f"WHERE UPPER(name) LIKE UPPER('%{safe_name}%')\n"
                "GROUP BY name\n"
                "ORDER BY cnt DESC\n"
                "LIMIT 20;"
            )


            examples: List[Dict[str, Any]] = []
            try:
                for row in tp.query(examples_query):
                    examples.append(
                        {
                            "sliceId": getattr(row, "slice_id", None),
                            "tsMs": getattr(row, "ts_ms", None),
                            "endTsMs": getattr(row, "end_ts_ms", None),
                            "durMs": float(getattr(row, "dur_ms", 0.0) or 0.0),
                            "depth": getattr(row, "depth", None),
                            "category": getattr(row, "category", None),
                            "trackName": getattr(row, "track_name", None),
                            "thread_name": getattr(row, "thread_name", None),
                            "tid": getattr(row, "tid", None),
                            "is_main_thread": getattr(row, "is_main_thread", None),
                            "process_name": getattr(row, "process_name", None),
                            "pid": getattr(row, "pid", None),
                        }
                    )
            except Exception as e:
                logger.warning(f"Examples query failed: {e}")

            # 收集具有相似名称的其他切片
            try:
                for row in tp.query(other_slices_query):
                    name_val = getattr(row, "name", None)
                    if isinstance(name_val, str):
                        other_slices.append(name_val)
            except Exception as e:
                logger.info(f"Similar names query failed (non-fatal): {e}")

            return {
                "sliceName": slice_name,
                "totalCount": total_count,
                "durationSummary": {
                    "minMs": min_ms,
                    "avgMs": avg_ms,
                    "maxMs": max_ms,
                },
                "timeBounds": {
                    "earliestTsMs": int(earliest_ms) if isinstance(earliest_ms, (int, float)) else None,
                    "latestTsMs": int(latest_ms) if isinstance(latest_ms, (int, float)) else None,
                    "spanMs": span_ms,
                },
                "examples": examples,
                "otherSlices": other_slices,
            }

        return self.run_formatted(trace_path, process_name, _get_slice_info_operation)
