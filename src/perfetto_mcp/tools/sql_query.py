"""用于在跟踪上执行任意查询的SQL查询工具。"""

import json
import logging
from typing import Optional
from .base import BaseTool, ToolError
from ..utils.query_helpers import (
    validate_sql_query,
    format_query_result_row,
    approximate_statement_count,
    detect_last_statement_type,
)

logger = logging.getLogger(__name__)


class SqlQueryTool(BaseTool):
    """用于在Perfetto跟踪上执行任意SQL查询的工具。"""

    def execute_sql_query(self, trace_path: str, sql_query: str, process_name: Optional[str] = None) -> str:
        """执行经过验证的PerfettoSQL脚本并返回统一的JSON信封。"""
        # 带有防护措施的宽松验证(大小/语句计数)
        if not validate_sql_query(sql_query):
            envelope = self._make_envelope(
                trace_path=trace_path,
                process_name=process_name,
                success=False,
                error=self._error(
                    "INVALID_QUERY",
                    "SQL script rejected by guardrails",
                    sql_query,
                ),
                result={"query": sql_query},
            )
            return json.dumps(envelope, indent=2)

        def _execute_sql_operation(tp):
            """内部操作，用于执行SQL查询并构建结果负载。"""
            # 按原样执行脚本(无自动LIMIT)
            qr_it = tp.query(sql_query)

            # 收集结果
            rows = []
            columns = None

            for row in qr_it:
                if columns is None:
                    columns = list(row.__dict__.keys())
                row_dict = format_query_result_row(row, columns)
                rows.append(row_dict)

            # 计算元数据
            try:
                stmt_count = approximate_statement_count(sql_query)
            except Exception:
                stmt_count = None
            try:
                last_stmt = detect_last_statement_type(sql_query)
            except Exception:
                last_stmt = None

            returns_rows = bool(columns)

            # 仅结果负载；信封由run_formatted添加
            payload = {
                "query": sql_query,
                "columns": columns if columns else [],
                "rows": rows,
                "rowCount": len(rows),
                "scriptStatementCount": stmt_count,
                "lastStatementType": last_stmt,
                "returnsRows": returns_rows,
            }
            return payload

        # Use the unified formatter with connection management
        return self.run_formatted(trace_path, process_name, _execute_sql_operation)
