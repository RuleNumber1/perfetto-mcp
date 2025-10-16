"""SQL处理的查询辅助工具。"""

import os
import logging

logger = logging.getLogger(__name__)


# 防护栏默认值（可通过环境变量覆盖）
DEFAULT_MAX_SCRIPT_BYTES = int(os.getenv("PERFETTO_MCP_MAX_SCRIPT_BYTES", "1000000"))
DEFAULT_MAX_STATEMENTS = int(os.getenv("PERFETTO_MCP_MAX_STATEMENTS", "200"))


def add_limit_to_query(sql_query: str, limit: int = 50) -> str:
    """如果SQL查询没有LIMIT子句，则添加一个。
    
    Args:
        sql_query: SQL查询字符串
        limit: 返回的最大行数（默认：50）
        
    Returns:
        str: 添加了LIMIT子句的查询
    """
    query_upper = sql_query.upper()
    if 'LIMIT' not in query_upper:
        # 如果存在尾部分号，则移除
        if sql_query.rstrip().endswith(';'):
            sql_query = sql_query.rstrip()[:-1]
        sql_query = f"{sql_query} LIMIT {limit}"
    
    return sql_query


def _split_statements(sql_script: str) -> list[str]:
    """尽力将SQL脚本按分号分割成语句。

    处理单引号、双引号、行注释（--）和块注释（/* */），
    避免在这些区域内分割分号。
    """
    statements: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    in_line_comment = False
    in_block_comment = False
    i = 0
    length = len(sql_script)
    while i < length:
        ch = sql_script[i]
        nxt = sql_script[i + 1] if i + 1 < length else ""

        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
            current.append(ch)
            i += 1
            continue

        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                current.append(ch)
                current.append(nxt)
                i += 2
                continue
            current.append(ch)
            i += 1
            continue

        # Enter comments
        if ch == "-" and nxt == "-" and not in_single and not in_double:
            in_line_comment = True
            current.append(ch)
            current.append(nxt)
            i += 2
            continue
        if ch == "/" and nxt == "*" and not in_single and not in_double:
            in_block_comment = True
            current.append(ch)
            current.append(nxt)
            i += 2
            continue

        # Toggle quotes
        if ch == "'" and not in_double:
            in_single = not in_single
            current.append(ch)
            i += 1
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            current.append(ch)
            i += 1
            continue

        if ch == ";" and not in_single and not in_double:
            # End of statement
            stmt = "".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
            i += 1
            continue

        current.append(ch)
        i += 1

    # Trailing statement without semicolon
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)

    return statements


def approximate_statement_count(sql_script: str) -> int:
    """返回脚本中语句的最佳估计数量。"""
    if not sql_script:
        return 0
    return len(_split_statements(sql_script))


def detect_last_statement_type(sql_script: str) -> str | None:
    """检测最后一个非空语句的第一个关键字（大写）。

    如果没有找到语句，返回None。
    """
    statements = _split_statements(sql_script)
    if not statements:
        return None
    last = statements[-1].lstrip()
    # Extract first token (letters, underscore, dot allowed for PERFETTO keywords)
    token = []
    for ch in last:
        if ch.isalpha() or ch == "_" or ch == ".":
            token.append(ch)
        else:
            break
    if not token:
        return None
    return "".join(token).upper()


def is_valid_perfetto_sql(sql_script: str, *, max_bytes: int = DEFAULT_MAX_SCRIPT_BYTES, max_statements: int | None = DEFAULT_MAX_STATEMENTS) -> tuple[bool, str | None]:
    """对PerfettoSQL脚本进行宽松验证。

    返回 (ok, reason)。当ok为True时，"reason"为None。
    """
    if not sql_script or not sql_script.strip():
        return False, "SQL script is empty"

    try:
        size_bytes = len(sql_script.encode("utf-8", errors="ignore"))
    except Exception:
        size_bytes = len(sql_script)

    if max_bytes is not None and size_bytes > max_bytes:
        return False, f"SQL script exceeds max size of {max_bytes} bytes"

    if max_statements is not None:
        try:
            count = approximate_statement_count(sql_script)
        except Exception:
            # 如果分割失败，为了安全起见接受（如果需要，TraceProcessor会报错）
            count = 1
        if count > max_statements:
            return False, f"SQL script has {count} statements which exceeds max of {max_statements}"

    return True, None


def validate_sql_query(sql_query: str) -> bool:
    """已弃用。为向后兼容而保留。现在使用宽松的脚本检查。"""
    ok, _ = is_valid_perfetto_sql(sql_query)
    if not ok:
        logger.warning("SQL script rejected by guardrails")
    return ok


def format_query_result_row(row, columns: list) -> dict:
    """将查询结果行格式化为字典。
    
    Args:
        row: 查询结果行对象
        columns: 列名列表
        
    Returns:
        dict: 行数据作为字典
    """
    row_dict = {}
    for col in columns:
        value = getattr(row, col)
        # 将任何非JSON可序列化的类型转换为字符串
        if value is not None and not isinstance(value, (str, int, float, bool)):
            value = str(value)
        row_dict[col] = value
    
    return row_dict
