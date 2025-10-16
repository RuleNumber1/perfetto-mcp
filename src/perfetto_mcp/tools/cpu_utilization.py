"""CPU利用率分析器工具，具有每线程细分和可选的DVFS分析。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .base import BaseTool, ToolError

logger = logging.getLogger(__name__)


class CpuUtilizationProfilerTool(BaseTool):
    """用于分析进程CPU利用率的工具。

    提供每线程CPU运行时间、跟踪期间的利用率百分比和调度统计信息。
    在可用时可选地使用CPU频率(DVFS)摘要增强结果。
    """

    def cpu_utilization_profiler(
        self,
        trace_path: str,
        process_name: str,
        group_by: str = "thread",
        include_frequency_analysis: bool = True,
    ) -> str:
        """分析给定进程的CPU利用率。

        参数
        ----------
        trace_path : str
            Perfetto跟踪文件路径。
        process_name : str
            目标进程名称(支持GLOB模式，例如"com.example.*")。
        group_by : str, optional
            目前仅支持"thread"。默认为"thread"。
        include_frequency_analysis : bool, optional
            当为True时，如果DVFS计数器可用，则包含平均CPU频率摘要(kHz)和每CPU详细信息。
            默认为True。

        返回
        -------
        str
            包含字段的JSON信封：processName, tracePath, success, error, result
            结果形状：
              {
                processName: str,
                groupBy: "thread",
                summary: { runtimeSecondsTotal, cpuPercentOfTrace, threadsCount },
                threads: [
                  { threadName, isMainThread, runtimeSeconds, cpuPercent, cpusUsed,
                    scheduleCount, avgSliceMs, maxSliceMs }
                ],
                frequency: {
                  avgCpuFreqKHz: number | null,
                  perCpu: [{ cpu, avgKHz, minKHz, maxKHz }]
                } | null
              }
        """

        def _op(tp):
            if not process_name or not isinstance(process_name, str):
                raise ToolError("INVALID_PARAMETERS", "process_name must be a non-empty string")

            if group_by != "thread":
                raise ToolError(
                    "INVALID_PARAMETERS",
                    "Only group_by='thread' is supported currently",
                )

            safe_proc = process_name.replace("'", "''")

            # 核心每线程CPU利用率查询
            cpu_query = f"""
            INCLUDE PERFETTO MODULE linux.cpu.utilization.process;
            SELECT 
              t.name AS thread_name,
              t.is_main_thread AS is_main_thread,
              SUM(s.dur) AS total_runtime_ns,
              COUNT(DISTINCT s.cpu) AS cpus_used,
              COUNT(*) AS schedule_count,
              AVG(s.dur) AS avg_slice_duration_ns,
              MAX(s.dur) AS max_slice_duration_ns,
              CAST(SUM(s.dur) * 100.0 / trace_dur() AS REAL) AS cpu_percent
            FROM sched_slice s
            JOIN thread t USING(utid)
            JOIN process p USING(upid)
            WHERE p.name GLOB '{safe_proc}'
            GROUP BY t.utid
            ORDER BY total_runtime_ns DESC;
            """

            rows = list(tp.query(cpu_query))

            threads: List[Dict[str, Any]] = []
            total_runtime_ns = 0
            for r in rows:
                total_runtime_ns += int(getattr(r, "total_runtime_ns", 0) or 0)
                threads.append(
                    {
                        "threadName": getattr(r, "thread_name", None),
                        "isMainThread": bool(getattr(r, "is_main_thread", 0) or 0),
                        "runtimeSeconds": float((getattr(r, "total_runtime_ns", 0) or 0) / 1e9),
                        "cpuPercent": float(getattr(r, "cpu_percent", 0.0) or 0.0),
                        "cpusUsed": getattr(r, "cpus_used", None),
                        "scheduleCount": getattr(r, "schedule_count", None),
                        "avgSliceMs": float((getattr(r, "avg_slice_duration_ns", 0) or 0) / 1e6),
                        "maxSliceMs": float((getattr(r, "max_slice_duration_ns", 0) or 0) / 1e6),
                    }
                )

            # 摘要
            runtime_seconds_total = float(total_runtime_ns / 1e9)
            cpu_percent_total = float(sum(t.get("cpuPercent", 0.0) or 0.0 for t in threads))

            frequency: Optional[Dict[str, Any]] = None
            if include_frequency_analysis:
                frequency = self._query_frequency_summary(tp)

            return {
                "processName": process_name,
                "groupBy": group_by,
                "summary": {
                    "runtimeSecondsTotal": runtime_seconds_total,
                    "cpuPercentOfTrace": cpu_percent_total,
                    "threadsCount": len(threads),
                },
                "threads": threads,
                "frequency": frequency,
            }

        return self.run_formatted(trace_path, process_name, _op)

    # -------------------------------
    # 助手
    # -------------------------------
    def _query_frequency_summary(self, tp) -> Optional[Dict[str, Any]]:
        """如果存在，使用DVFS计数器查询CPU频率摘要。

        如果android.dvfs不可用，则回退到cpu_counter_track/counter。
        返回包含平均值和每CPU统计信息的字典，如果不可用则返回None。
        """
        # 首先尝试android.dvfs
        dvfs_sql = """
        INCLUDE PERFETTO MODULE android.dvfs;
        SELECT cpu,
               AVG(value) AS avg_khz,
               MIN(value) AS min_khz,
               MAX(value) AS max_khz
        FROM android_dvfs_counters
        WHERE name LIKE 'cpufreq%'
        GROUP BY cpu
        ORDER BY cpu;
        """
        per_cpu: List[Dict[str, Any]] = []
        try:
            rows = list(tp.query(dvfs_sql))
            for r in rows:
                per_cpu.append(
                    {
                        "cpu": getattr(r, "cpu", None),
                        "avgKHz": float(getattr(r, "avg_khz", 0.0) or 0.0),
                        "minKHz": float(getattr(r, "min_khz", 0.0) or 0.0),
                        "maxKHz": float(getattr(r, "max_khz", 0.0) or 0.0),
                    }
                )
        except Exception as e:
            msg = str(e).lower()
            if "android.dvfs" in msg or "android_dvfs_counters" in msg or "no such" in msg:
                per_cpu = []
            else:
                # Unexpected error; log and return None for frequency
                logger.warning(f"DVFS frequency query failed: {e}")
                return None

        # 如果dvfs数据不可用，则回退到cpu_counter_track
        if not per_cpu:
            fallback_sql = """
            SELECT ct.cpu AS cpu,
                   AVG(c.value) AS avg_khz,
                   MIN(c.value) AS min_khz,
                   MAX(c.value) AS max_khz
            FROM counter c
            JOIN cpu_counter_track ct ON c.track_id = ct.id
            WHERE ct.name = 'cpufreq'
            GROUP BY ct.cpu
            ORDER BY ct.cpu;
            """
            try:
                rows = list(tp.query(fallback_sql))
                for r in rows:
                    per_cpu.append(
                        {
                            "cpu": getattr(r, "cpu", None),
                            "avgKHz": float(getattr(r, "avg_khz", 0.0) or 0.0),
                            "minKHz": float(getattr(r, "min_khz", 0.0) or 0.0),
                            "maxKHz": float(getattr(r, "max_khz", 0.0) or 0.0),
                        }
                    )
            except Exception as e:
                logger.info(f"CPU freq fallback unavailable: {e}")
                return None

        if not per_cpu:
            return None

        # 跨CPU平均
        try:
            avg_all = sum(item["avgKHz"] for item in per_cpu) / max(1, len(per_cpu))
        except Exception:
            avg_all = None

        return {
            "avgCpuFreqKHz": avg_all,
            "perCpu": per_cpu,
        }
