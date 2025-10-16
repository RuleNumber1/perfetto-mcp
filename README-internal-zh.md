# Perfetto MCP 服务器

一个模型上下文协议（MCP）服务器，提供用于分析 Perfetto 跟踪文件的工具，具有持久连接管理和自动重连支持。

## 功能特性

- **持久连接**：在多个工具调用之间保持跟踪连接以提高性能
- **自动重连**：优雅处理连接失败，具有自动重试逻辑
- **模块化架构**：清晰的责任分离，为不同功能提供专用模块
- **线程安全**：通过适当的锁定机制确保并发工具调用的安全性

## 架构

服务器采用模块化结构组织：

```
src/perfetto_mcp/
├── __init__.py              # 包初始化和 stdio 入口点
├── __main__.py              # 启用 `python -m perfetto_mcp`
├── server.py                # MCP 服务器设置与生命周期管理
├── dev.py                   # 开发入口 `mcp dev`（导出 `mcp`）
├── connection_manager.py    # 持久 TraceProcessor 连接管理
├── resource/                # MCP 资源注册
│   ├── __init__.py          # register_resources(mcp)
│   ├── concepts.py          # 概念文档作为 FileResource
│   └── trace_analysis.py    # 跟踪分析 URL 资源
├── tools/
│   ├── __init__.py
│   ├── base.py                             # 基础工具类，包含连接管理和格式化
│   ├── find_slices.py                      # find_slices 工具
│   ├── sql_query.py                        # execute_sql_query 工具
│   ├── anr_detection.py                    # detect_anrs 工具
│   ├── anr_root_cause.py                   # anr_root_cause_analyzer 工具
│   ├── cpu_utilization.py                  # cpu_utilization_profiler 工具
│   ├── jank_frames.py                      # detect_jank_frames 工具
│   ├── frame_performance_summary.py        # frame_performance_summary 工具
│   ├── memory_leak_detector.py             # memory_leak_detector 工具
│   ├── heap_dominator_tree_analyzer.py     # heap_dominator_tree_analyzer 工具
│   ├── thread_contention_analyzer.py       # thread_contention_analyzer 工具
│   ├── binder_transaction_profiler.py      # binder_transaction_profiler 工具
│   └── main_thread_hotspots.py             # main_thread_hotspot_slices 工具
└── utils/
    ├── __init__.py
    └── query_helpers.py     # SQL 脚本防护和格式化辅助工具
```

### 核心组件

- **ConnectionManager**：管理持久 TraceProcessor 连接，具有自动切换和重连功能
- **BaseTool**：为所有工具提供连接管理和错误处理的基础类
- **Server Lifecycle**：优雅关闭和连接管理的适当清理处理程序

## 可用工具

### 1. `find_slices(trace_path, pattern, process_name=None, match_mode='contains', limit=100, main_thread_only=False, time_range=None)`
通过灵活的名称匹配发现切片，无需编写 SQL 即可返回聚合结果和示例。
返回 JSON 封装：`result = { matchMode, filters, timeRangeMs, aggregates: [...], examples: [...], notes }`。

### 2. `execute_sql_query(trace_path, sql_query, process_name=None)`
对跟踪数据库执行 PerfettoSQL 脚本（多语句）。脚本由 TraceProcessor 逐字执行。
如果最终语句是 `SELECT`，则返回行；否则结果具有 `rowCount = 0` 和 `columns = []`。

注意事项：
- 支持完整的 PerfettoSQL，包括 `INCLUDE PERFETTO MODULE`（在支持的地方允许通配符）、
  `CREATE PERFETTO TABLE/VIEW/INDEX/MACRO/FUNCTION` 和标准 SQLite 语句（例如 `PRAGMA`）。
- 不自动应用 `LIMIT`。考虑添加 `LIMIT` 以避免大型结果集。
- 防护措施：服务器强制执行基本限制（脚本大小和可选语句计数）。脚本在其他情况下会通过而不进行关键字阻止。

### 3. `detect_anrs(trace_path, process_name=None, min_duration_ms=5000, time_range=None)`
在 Android 跟踪中检测应用程序无响应（ANR）事件，提供上下文详细信息和严重性分析。

### 4. `anr_root_cause_analyzer(trace_path, process_name=None, anr_timestamp_ms=None, analysis_window_ms=10000, time_range=None, deep_analysis=False)`
通过关联时间窗口内的多个信号（主线程阻塞、慢速 Binder 事务、内存压力、Java 监视器争用）分析可能的 ANR 根本原因。
返回结构化封装，包含见解部分和每个信号的详细信息。

### 5. `cpu_utilization_profiler(trace_path, process_name, group_by='thread', include_frequency_analysis=True)`
分析进程的 CPU 利用率，提供按线程的细分（运行时间、调度统计、CPU 百分比）。
可选包含 CPU 频率（DVFS）摘要（当可用时）。

### 6. `detect_jank_frames(trace_path, process_name, jank_threshold_ms=16.67, severity_filter=None)`
识别进程的卡顿帧，包括严重性和来源分类（应用程序 vs SurfaceFlinger），包含超时、CPU/UI 时间和图层名称。
返回 JSON 封装：`result = { totalCount, frames: [...], filters }`，其中每个帧行包含：
`{ frame_id, timestamp_ms, duration_ms, overrun_ms, jank_type, jank_severity_type, jank_source, cpu_time_ms, ui_time_ms, layer_name, jank_classification }`。

`detect_jank_frames` 注意事项：
- 需要 Android 帧时间线数据。工具首先使用标准库模块（`android.frames.timeline`、`android.frames.per_frame_metrics`）。
- 如果这些模块缺失，则回退到原始 `actual_frame_timeline_slice`/`expected_frame_timeline_slice` 表，并计算 `overrun_ms` 而不包含 CPU/UI 时间（返回 null），以在较精简的跟踪中保持有用。

### 7. `frame_performance_summary(trace_path, process_name)`
进程的聚合帧性能指标和卡顿统计。
返回 JSON 封装：`result = { total_frames, jank_frames, jank_rate_percent, slow_frames, big_jank_frames, huge_jank_frames, avg_cpu_time_ms, max_cpu_time_ms, p95_cpu_time_ms, p99_cpu_time_ms, performance_rating }`。
需要每帧指标；如果不可用，返回 `FRAME_METRICS_UNAVAILABLE` 错误及详细信息。

### 8. `memory_leak_detector(trace_path, process_name, growth_threshold_mb_per_min=5.0, analysis_duration_ms=60000)`
使用进程 RSS 增长模式和堆图聚合检测内存泄漏。
返回 JSON 封装：`result = { growth: { avgGrowthRateMbPerMin, maxGrowthRateMbPerMin, sampleCount, leakIndicatorCount }, suspiciousClasses: [{ type_name, obj_count, size_mb, dominated_obj_count, dominated_size_mb }], filters, notes }`。
如果 RSS 或堆图数据不可用，返回部分结果，`notes` 解释缺失内容。

### 9. `heap_dominator_tree_analyzer(trace_path, process_name, max_classes=20)`
分析进程的最新堆图快照，找出主导堆使用的类。
返回 JSON 封装：`result = { totalCount, classes: [{ display_name, instance_count, self_size_mb, native_size_mb, total_size_mb, avg_reachability, min_root_distance, memory_impact }], filters, notes }`。
如果扩展堆图列或模块缺失，回退到简化查询（不包含 `native_size_mb`、`avg_reachability`、`min_root_distance`）并添加 `notes` 条目。如果跟踪中没有堆图，返回 `HEAP_GRAPH_UNAVAILABLE`。

### 10. `thread_contention_analyzer(trace_path, process_name, time_range=None, min_block_ms=50.0, include_per_thread_breakdown=False, include_examples=False, limit=80)`
识别线程争用和同步瓶颈，具有时间范围、最小持续时间过滤、可选的按线程阻塞状态细分、示例等待，以及监视器争用数据不可用时的自动回退。

返回 JSON 封装：`result = { totalCount, contentions: [...], filters, analysisSource, usesWakerLinkage?, usedSchedBlockedReason?, primaryDataUnavailable?, fallbackNotice?, timeRangeMs?, thresholds, dataDependencies, notes?, blocked_state_breakdown?, top_dstate_functions?, examples? }`，其中每个争用行包含：
`{ blocked_thread_name, blocking_thread_name, short_blocking_method_name, contention_count, total_blocked_ms, avg_blocked_ms, max_blocked_ms, total_waiters, max_concurrent_waiters, blocked_is_main_thread?, severity }`。

注意事项：
- **主要分析**：当可用时使用 `android.monitor_contention`；`min_block_ms` 在聚合前过滤短等待；`time_range` 限制到窗口。
- **自动回退**：从 `thread_state` 推断，具有唤醒器链接和可选的 `sched_blocked_reason` 归因；相同的 `time_range` 和阈值。
- **细分**：如果 `include_per_thread_breakdown=True`，返回相同窗口内每个线程的 S/D 总计和百分比。
- **示例**：如果 `include_examples=True`，返回最长等待（来自主要或回退路径），上限为 `limit`。

### 11. `binder_transaction_profiler(trace_path, process_filter, min_latency_ms=10.0, include_thread_states=True, time_range=None, correlate_with_main_thread=False, group_by=None)`
使用 `android.binder` 模块分析 Binder 事务性能并识别瓶颈。

返回 JSON 封装，结果取决于 `group_by`：
- 当 `group_by=None`：`result = { totalCount, timeRangeMs?, transactions: [...], filters }`，其中每行包含：
  `{ client_process, server_process, aidl_name, method_name, client_latency_ms, server_latency_ms, overhead_ms, overhead_ratio, is_main_thread, is_sync, top_thread_states, main_thread_top_states?, latency_severity }`。
- 当分组时（`'aidl'` 或 `'server_process'`）：`result = { totalCount, timeRangeMs?, aggregates: [...], filters }`，包含聚合计数和平均延迟/开销。

参数：
- `time_range`：可选 `{'start_ms': X, 'end_ms': Y}`，按客户端时间戳限定分析窗口。
- `correlate_with_main_thread`：如果为 true，为主线程事务添加客户端主线程状态的最佳效果摘要。
- `group_by`：`None`、`'aidl'`、`'server_process'` 之一，切换到聚合视图。

注意事项：
- 需要 `android.binder` 视图（`android_binder_txns`、`android_sync_binder_thread_state_by_txn`）。如果不可用，返回 `BINDER_DATA_UNAVAILABLE`。
- 过滤客户端或服务器匹配 `process_filter` 且客户端延迟 >= `min_latency_ms` 的事务。
- 当 `include_thread_states` 为 true 时，包含每个事务按时间排序的顶部线程状态。

### 12. `main_thread_hotspot_slices(trace_path, process_name, limit=80, time_range=None, min_duration_ms=None)`
识别目标进程主线程上运行时间最长的切片。适用于 ANR 和卡顿排查。

返回 JSON 封装：`result = { filters, timeRangeMs, dataDependencies, hotspots: [...], summary, notes }`，其中每个热点行包含：
`{ sliceId, name, category, depth, trackId, trackName, tsMs, endTsMs, durMs, threadName, tid, isMainThread, processName, pid }`。

注意事项：
- 当可用时按 `thread.is_main_thread = 1` 过滤；否则回退到启发式 `tid == pid` 并在 `notes` 中报告。
- 支持 `process_name` GLOB（例如 `com.example.*`），可选 `time_range = {start_ms, end_ms}` 和 `min_duration_ms` 阈值。

## MCP 资源

- `resource://perfetto-mcp/concepts`
  - Perfetto 分析概念和工作流程的文本/Markdown 参考
  - 后端：`docs/Perfetto-MCP-Concepts.md`
  - MIME：`text/markdown`
  - 通过 `list_resources` 发现，通过 `read_resource` 读取

- `resource://perfetto-docs/trace-analysis-getting-started`
  - 指向官方 Perfetto 跟踪分析文档的 URL 资源
  - 引用：`https://perfetto.dev/docs/analysis/getting-started`
  - MIME：`text/markdown`
  - 为使用 MCP 工具提供官方 Perfetto 工作流程指导的上下文

## 开发命令

**设置和依赖：**
- `uv sync` - 安装依赖项并创建虚拟环境
- `uv add <package>` - 添加新依赖项

**运行服务器：**
- `uv run mcp dev src/perfetto_mcp/dev.py` - 使用开发工具运行 MCP 服务器
- `uv run -m perfetto_mcp` - 直接运行 MCP 服务器（stdio）
- `uvx perfetto-mcp` - 运行已安装的控制台脚本

**测试：**
- `uv run pytest -q` - 运行测试套件（当添加测试时）

## 连接管理

服务器实现智能连接管理：

- **持久连接**：相同跟踪文件的连接在多个工具调用之间保持打开
- **自动切换**：当提供不同的跟踪路径时无缝切换连接
- **重连**：在连接失败时自动重连而不丢失上下文
- **清理**：通过多种机制在服务器关闭时进行适当的连接清理

## 错误处理

服务器在添加新功能的同时保持与原始错误处理的向后兼容性：

- **FileNotFoundError**：无效的跟踪文件路径
- **ConnectionError**：TraceProcessor 连接问题
- **自动恢复**：在连接失败时尝试重连
- **优雅降级**：当重连失败时回退到错误消息

## 安全特性

- **宽松的 PerfettoSQL 执行**：脚本由 TraceProcessor 逐字执行
- **防护措施**：脚本大小和语句计数限制（环境可配置）；无关键字阻止
- **无自动 LIMIT**：考虑添加 `LIMIT` 以避免大型结果集

## 工具输出模式

所有工具调用返回一致的 JSON 封装：

```
{
  "processName": "not-specified" | "<provided by caller>",
  "tracePath": "./trace.pftrace",
  "success": true,
  "error": null | { "code": "...", "message": "...", "details": "..." },
  "result": { ... 工具特定负载 ... }
}
```

示例：
- find_slices → `result = { matchMode, filters, timeRangeMs, aggregates, examples, notes }`
- execute_sql_query → `result = { query, columns, rows, rowCount, scriptStatementCount?, lastStatementType?, returnsRows }`
- detect_anrs → `result = { totalCount, anrs: [...], filters: { ... } }`
- anr_root_cause_analyzer → `result = { window, filters, mainThreadBlocks, binderDelays, memoryPressure, lockContention, insights, notes }`
- cpu_utilization_profiler → `result = { processName, groupBy, summary, threads, frequency }`
- detect_jank_frames → `result = { totalCount, frames: [...], filters }`
- thread_contention_analyzer → `result = { totalCount, contentions: [...], filters, analysisSource, usesWakerLinkage?, usedSchedBlockedReason?, primaryDataUnavailable?, fallbackNotice? }`

## 依赖项

- 需要 Python >=3.10（推荐 3.13+）
- 关键包：`mcp[cli]`、`perfetto`、`protobuf<5`

## 关闭处理

服务器实现多种清理策略：
- 主要：`atexit` 处理程序用于正常关闭
- 次要：SIGTERM/SIGINT 的信号处理程序
- 优雅：在所有场景中进行适当的连接清理

## 使用示例

运行服务器（stdio）：
- `uv run mcp dev src/perfetto_mcp/dev.py`（使用开发工具），或
- `uv run -m perfetto_mcp`，或
- `uvx perfetto-mcp`

工具调用示例（高级）：
- `find_slices`：提供 `trace_path` 和 `pattern`（默认包含）以调查匹配切片及其统计信息和顶部示例。
- `execute_sql_query`：提供 SELECT 查询。危险语句会被拒绝。
- `detect_anrs`：可选按 `process_name`、`min_duration_ms` 和 `{start_ms, end_ms}` 窗口过滤。
- `anr_root_cause_analyzer`：提供进程和 `anr_timestamp_ms` 与 `analysis_window_ms` 或显式 `time_range`。
- `cpu_utilization_profiler`：提供 `process_name`；返回按线程的 CPU 使用情况细分。可选包含频率分析。
- `detect_jank_frames`：提供 `process_name`；可选调整 `jank_threshold_ms` 和 `severity_filter`。

## 数据前提条件和故障排除

- `detect_anrs`/`anr_root_cause_analyzer`：需要包含 ANR 数据的 Android 系统跟踪。如果 ANR 模块/表缺失，工具返回信息性错误。
- `detect_jank_frames`：需要 Android 帧时间线数据（Android S+）。如果标准库视图缺失，工具回退到原始帧表。如果这些也缺失，您可能未在跟踪配置中启用帧时间线。
- `cpu_utilization_profiler`：需要调度程序数据（ftrace sched 事件）来计算每个线程的运行时间和调度统计。
- `thread_contention_analyzer`：优先使用 `android.monitor_contention` 数据进行精确的 Java 锁详细信息，但如果不可用，自动回退到基于调度程序的推断。

如果您看到 `*_DATA_UNAVAILABLE` 错误，请重新捕获启用相关数据源或提供不同的跟踪。

## 参考文档

- MCP 服务器：https://modelcontextprotocol.io/quickstart/server
- Python MCP SDK：https://github.com/modelcontextprotocol/python-sdk
- Perfetto 跟踪分析：https://perfetto.dev/docs/analysis/getting-started
- Perfetto TraceProcessor：https://perfetto.dev/docs/analysis/trace-processor-python