"""主线程热点切片工具。

展示进程主线程上运行时间最长的切片，以加速
ANR和卡顿的排查。返回带有丰富上下文和元数据的可链接行。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from .base import BaseTool, ToolError

logger = logging.getLogger(__name__)


class MainThreadHotspotTool(BaseTool):
    """用于识别进程中最重的主线程工作的工具。"""

    def main_thread_hotspot_slices(
        self,
        trace_path: str,
        process_name: str,
        limit: int = 80,
        time_range: Optional[Dict[str, float | int]] = None,
        min_duration_ms: Optional[float | int] = None,
    ) -> str:
        """返回进程主线程上最长的前N个切片。

        参数
        ----------
        trace_path : str
            Perfetto跟踪文件路径。
        process_name : str
            目标进程名称(支持GLOB，例如"com.example.*")。
        limit : int, optional
            要返回的热点切片的最大数量(1..500)。默认为80。
        time_range : dict | None, optional
            可选时间边界：{ 'start_ms': X, 'end_ms': Y }。
        min_duration_ms : float | int | None, optional
            可选阈值，仅包含持续时间>=阈值的切片。

        返回
        -------
        str
            包含热点、过滤器和说明的结果负载的JSON信封。
        """

        def _validate_and_normalize() -> Tuple[str, int, Optional[Tuple[int, int]], Optional[int], List[str]]:
            notes: List[str] = []

            if not process_name or not isinstance(process_name, str):
                raise ToolError("INVALID_PARAMETERS", "process_name must be a non-empty string")

            # 转义SQL字符串字面量中的引号
            safe_proc = process_name.replace("'", "''").strip()
            # 如果调用者没有包含通配符，则用*包装以实现类似包含的易用性
            if "*" not in safe_proc:
                safe_proc = f"*{safe_proc}*"

            try:
                limit_int = int(limit)
            except Exception:
                raise ToolError("INVALID_PARAMETERS", "limit must be an integer")
            if limit_int < 1:
                limit_int = 1
            if limit_int > 500:
                limit_int = 500

            time_bounds_ns: Optional[Tuple[int, int]] = None
            if time_range is not None:
                if not isinstance(time_range, dict):
                    raise ToolError("INVALID_PARAMETERS", "time_range must be a dict with start_ms/end_ms")
                start_ms = time_range.get("start_ms")
                end_ms = time_range.get("end_ms")
                if start_ms is None or end_ms is None:
                    raise ToolError("INVALID_PARAMETERS", "time_range requires both start_ms and end_ms")
                try:
                    start_ns = int(float(start_ms) * 1e6)
                    end_ns = int(float(end_ms) * 1e6)
                except Exception:
                    raise ToolError("INVALID_PARAMETERS", "time_range values must be numeric")
                if end_ns < start_ns:
                    raise ToolError("INVALID_PARAMETERS", "time_range end_ms must be >= start_ms")
                time_bounds_ns = (start_ns, end_ns)
                notes.append("Scanning limited to provided time_range")
            else:
                notes.append("No time_range provided; scanning entire trace")

            min_dur_ns: Optional[int] = None
            if min_duration_ms is not None:
                try:
                    min_dur_ns = int(float(min_duration_ms) * 1e6)
                except Exception:
                    raise ToolError("INVALID_PARAMETERS", "min_duration_ms must be numeric")

            return safe_proc, limit_int, time_bounds_ns, min_dur_ns, notes

        safe_proc, limit_int, time_bounds_ns, min_dur_ns, initial_notes = _validate_and_normalize()

        def _to_ms(ns_value: Optional[int | float]) -> Optional[float]:
            if ns_value is None:
                return None
            try:
                return float(ns_value) / 1e6
            except Exception:
                return None

        def _build_hotspots_query(use_is_main_thread: bool) -> str:
            where_clauses: List[str] = [f"p.name GLOB '{safe_proc}'"]
            if use_is_main_thread:
                where_clauses.append("th.is_main_thread = 1")
            else:
                # 回退启发式：tid == pid的主线程
                where_clauses.append("th.tid = p.pid")

            if time_bounds_ns is not None:
                start_ns, end_ns = time_bounds_ns
                where_clauses.append(f"s.ts BETWEEN {start_ns} AND {end_ns}")
            if min_dur_ns is not None:
                where_clauses.append(f"s.dur >= {min_dur_ns}")

            where_sql = " AND ".join(where_clauses)

            # 仅考虑线程轨道以确保线程上下文存在
            # 连接轨道以获取显示名称和类别
            query = (
                "SELECT\n"
                "  s.id AS slice_id,\n"
                "  s.name AS slice_name,\n"
                "  s.category AS category,\n"
                "  s.depth AS depth,\n"
                "  s.track_id AS track_id,\n"
                "  tr.name AS track_name,\n"
                "  CAST(s.ts / 1e6 AS INT) AS ts_ms,\n"
                "  CAST((s.ts + s.dur) / 1e6 AS INT) AS end_ts_ms,\n"
                "  CAST(s.dur / 1e6 AS REAL) AS dur_ms,\n"
                "  th.name AS thread_name, th.tid AS tid,\n"
                "  th.is_main_thread AS is_main_thread,\n"
                "  p.name AS process_name, p.pid AS pid\n"
                "FROM slice s\n"
                "JOIN thread_track ttr ON s.track_id = ttr.id\n"
                "JOIN thread th ON ttr.utid = th.utid\n"
                "JOIN process p ON th.upid = p.upid\n"
                "LEFT JOIN track tr ON s.track_id = tr.id\n"
                f"WHERE {where_sql}\n"
                "ORDER BY s.dur DESC\n"
                f"LIMIT {limit_int};\n"
            )
            return query

        def _operation(tp) -> Dict[str, Any]:
            notes: List[str] = list(initial_notes)
            data_dependencies = ["slice", "thread_track", "thread", "process", "track"]

            use_is_main = True
            query = _build_hotspots_query(use_is_main)
            rows: List[Any] = []
            try:
                rows = list(tp.query(query))
            except Exception as e:
                msg = str(e).lower()
                if ("no such column" in msg and "is_main_thread" in msg) or ("is_main_thread" in msg and "unknown" in msg):
                    notes.append("'thread.is_main_thread' not available; falling back to tid==pid heuristic")
                    use_is_main = False
                    query = _build_hotspots_query(use_is_main)
                    rows = list(tp.query(query))
                else:
                    raise ToolError("QUERY_FAILED", f"Hotspot query failed: {e}")

            hotspots: List[Dict[str, Any]] = []
            max_dur_ms = 0.0
            dur_sum_ms = 0.0
            for r in rows:
                dur_ms = float(getattr(r, "dur_ms", 0.0) or 0.0)
                max_dur_ms = max(max_dur_ms, dur_ms)
                dur_sum_ms += dur_ms
                hotspots.append(
                    {
                        "sliceId": getattr(r, "slice_id", None),
                        "name": getattr(r, "slice_name", None),
                        "category": getattr(r, "category", None),
                        "depth": getattr(r, "depth", None),
                        "trackId": getattr(r, "track_id", None),
                        "trackName": getattr(r, "track_name", None),
                        "tsMs": getattr(r, "ts_ms", None),
                        "endTsMs": getattr(r, "end_ts_ms", None),
                        "durMs": dur_ms,
                        "threadName": getattr(r, "thread_name", None),
                        "tid": getattr(r, "tid", None),
                        "isMainThread": bool(getattr(r, "is_main_thread", 0) or 0),
                        "processName": getattr(r, "process_name", None),
                        "pid": getattr(r, "pid", None),
                    }
                )

            avg_dur_ms = (dur_sum_ms / len(rows)) if rows else 0.0
            if not rows:
                notes.append("No main-thread slices matched the filters")

            result: Dict[str, Any] = {
                "filters": {
                    "processName": process_name,
                    "limit": limit_int,
                    "minDurationMs": None if min_dur_ns is None else _to_ms(min_dur_ns),
                },
                "timeRangeMs": (
                    None
                    if time_bounds_ns is None
                    else {
                        "startMs": int(time_bounds_ns[0] / 1e6),
                        "endMs": int(time_bounds_ns[1] / 1e6),
                    }
                ),
                "dataDependencies": data_dependencies,
                "hotspots": hotspots,
                "summary": {
                    "totalCount": len(hotspots),
                    "maxDurMs": max_dur_ms,
                    "avgDurMs": avg_dur_ms,
                    "usedIsMainThreadFlag": use_is_main,
                },
                "notes": notes,
            }

            return result

        return self.run_formatted(trace_path, process_name, _operation)


