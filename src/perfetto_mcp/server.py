"""Main MCP server setup with lifecycle management."""

import atexit
import logging
from mcp.server.fastmcp import FastMCP
from .connection_manager import ConnectionManager
from .tools.find_slices import SliceFinderTool
from .tools.sql_query import SqlQueryTool
from .tools.anr_detection import AnrDetectionTool
from .resource import register_resources
from .tools.anr_root_cause import AnrRootCauseTool
from .tools.cpu_utilization import CpuUtilizationProfilerTool
from .tools.jank_frames import JankFramesTool
from .tools.frame_performance_summary import FramePerformanceSummaryTool
from .tools.memory_leak_detector import MemoryLeakDetectorTool
from .tools.heap_dominator_tree_analyzer import HeapDominatorTreeAnalyzerTool
from .tools.thread_contention_analyzer import ThreadContentionAnalyzerTool
from .tools.binder_transaction_profiler import BinderTransactionProfilerTool
from .tools.main_thread_hotspots import MainThreadHotspotTool

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_server() -> FastMCP:
    """Create and configure the Perfetto MCP server.
    
    Returns:
        FastMCP: Configured MCP server instance
    """
    # Create MCP server
    mcp = FastMCP("Perfetto MCP")
    
    # Initialize connection manager
    connection_manager = ConnectionManager()
    
    # Create tool instances
    slice_finder_tool = SliceFinderTool(connection_manager)
    sql_query_tool = SqlQueryTool(connection_manager)
    anr_detection_tool = AnrDetectionTool(connection_manager)
    anr_root_cause_tool = AnrRootCauseTool(connection_manager)
    cpu_util_tool = CpuUtilizationProfilerTool(connection_manager)
    jank_frames_tool = JankFramesTool(connection_manager)
    frame_summary_tool = FramePerformanceSummaryTool(connection_manager)
    memory_leak_tool = MemoryLeakDetectorTool(connection_manager)
    heap_dom_tool = HeapDominatorTreeAnalyzerTool(connection_manager)
    thread_contention_tool = ThreadContentionAnalyzerTool(connection_manager)
    binder_txn_tool = BinderTransactionProfilerTool(connection_manager)
    main_thread_hotspot_tool = MainThreadHotspotTool(connection_manager)


    @mcp.tool()
    def find_slices(
        trace_path: str,
        pattern: str,
        process_name: str | None = None,
        match_mode: str = "contains",
        limit: int = 100,
        main_thread_only: bool = False,
        time_range: dict | None = None,
    ) -> str:
        """
        通过灵活的名称匹配发现切片，快速查看跟踪文件中的内容，
        无需编写SQL即可获取聚合数据和可链接的示例。

        使用场景：
        - 快速探索未知切片名称和热点路径（无需手动编写SQL）
        - 查看每个切片名称的频率和持续时间统计（最小值/平均值/最大值，以及可用的p50/p90/p99分位数）
        - 获取可链接的示例（id、ts、dur、track_id）以便在UI中跳转或与其他工具关联
        - 通过进程、主线程和时间范围筛选来缩小调查范围

        参数：
        - pattern: 用于匹配切片名称的字符串
        - match_mode: 'contains'（默认）、'exact' 或 'glob'
        - process_name: 可选筛选器；支持 '*' 通配符
        - main_thread_only: 限制为进程主线程
        - time_range: {'start_ms': X, 'end_ms': Y}
        - limit: 返回的最大示例切片数（默认100）

        输出：
        - aggregates: 每个切片名称的计数和持续时间统计（最小值/平均值/最大值，以及可用的p50/p90/p99分位数）
        - examples: 按持续时间排序的顶级切片，包含线程/进程上下文和用于链接的track_id
        - notes: 功能或回退通知
        """
        return slice_finder_tool.find_slices(
            trace_path,
            pattern,
            process_name,
            match_mode,
            limit,
            main_thread_only,
            time_range,
        )


    @mcp.tool()
    def execute_sql_query(trace_path: str, sql_query: str, process_name: str | None = None) -> str:
        """
        在跟踪数据上执行PerfettoSQL脚本（多语句）进行高级分析。

        使用场景：当其他工具无法满足需求时，需要复杂过滤/连接，或想要跨多个表关联数据时使用。
        这是用于自定义分析的强大工具 - 当预构建工具过于局限时使用。

        功能：完全SQL访问所有跟踪表，包括：
        - slice：包含时序的所有跟踪片段
        - thread/process：线程和进程元数据
        - counter：随时间变化的性能计数器
        - android_anrs：ANR事件
        - actual_frame_timeline_slice：帧卡顿数据
        - sched_slice：CPU调度信息
        - android_binder_txns：跨进程调用
        - heap_graph_*：内存堆分析

        安全性：接受完整的PerfettoSQL/SQLite脚本。不自动应用LIMIT；大型查询可能返回多行。
        脚本由TraceProcessor逐字执行。

        常见模式：
        - 持续时间分析："SELECT name, dur/1e6 as ms FROM slice WHERE dur > 10e6"
        - 聚合："SELECT name, COUNT(*), AVG(dur)/1e6 FROM slice GROUP BY name"
        - 时间过滤："SELECT * FROM slice WHERE ts BETWEEN 1e9 AND 2e9"
        - 进程过滤："SELECT * FROM thread WHERE upid IN (SELECT upid FROM process WHERE name LIKE '%chrome%')"

        高级用户提示：使用`INCLUDE PERFETTO MODULE ...`语句加载标准库模块（支持通配符如`android.*`）。
        也可以在TraceProcessor支持的情况下使用`CREATE PERFETTO TABLE`/`VIEW`/`FUNCTION`/`MACRO`/`INDEX`。

        参考：
        - PerfettoSQL语法：https://perfetto.dev/docs/analysis/perfetto-sql-syntax
        - 标准库（预置）：https://perfetto.dev/docs/analysis/stdlib-docs#package-prelude
        """
        return sql_query_tool.execute_sql_query(trace_path, sql_query, process_name)


    @mcp.tool()
    def detect_anrs(trace_path: str, process_name: str | None = None, min_duration_ms: int = 5000, time_range: dict | None = None) -> str:
        """
        使用场景：调查应用冻结、无响应、"未响应"对话框或用户关于应用卡顿的投诉。
        ANR是严重问题，主线程被阻塞超过5秒，导致Android考虑杀死应用。

        提供：完整的ANR列表，基于主线程状态和系统条件进行严重性评估。
        每个ANR包含垃圾回收压力分析，以识别内存相关原因。

        过滤器：
        - process_name: 目标应用（支持通配符："com.example.*", "*browser*"）
        - time_range: {'start_ms': X, 'end_ms': Y} 用于聚焦特定时间段

        ANR分析背景：ANR是直接影响用户体验的关键性能问题。通常由以下原因引起：
        - 主线程阻塞操作（I/O、网络、数据库）
        - 锁竞争和同步问题
        - 内存压力导致过度GC
        - Binder事务延迟
        - 主线程上的CPU密集型操作

        输出：
        - 每个ANR的时间戳和进程信息
        - 主线程状态（ANR时间点的最后已知状态）
        - ANR附近的GC事件计数（>10个事件表示内存压力）
        - 严重性启发式：GC>10为CRITICAL；主线程处于sleep/IO等待或中等GC为HIGH；
          其他情况为MEDIUM（系统关键进程会提升严重性）

        后续步骤：
        1. 使用anr_root_cause_analyzer和ANR时间戳进行深度分析
        2. 检查thread_contention_analyzer以查找锁相关原因
        3. 如果ANR涉及系统服务，运行binder_transaction_profiler

        解释：短时间内多个ANR = 系统性问题。单个ANR = 调查特定时间戳。
        没有ANR不保证良好性能 - 还需检查卡顿指标。

        - 需要跟踪中包含'android.anrs'数据源；否则返回ANR_DATA_UNAVAILABLE
        - 确保跟踪包含Android性能数据
        - 高ANR计数表示需要调查的系统性性能问题
        - 将ANR时间戳与其他性能指标（掉帧、内存压力）关联
        - 详细根因分析请使用execute_sql_query()和ANR时间戳
        - 零ANR不意味着良好性能 - 检查跟踪覆盖率和数据源
        """
        return anr_detection_tool.detect_anrs(trace_path, process_name, min_duration_ms, time_range)


    @mcp.tool()
    def anr_root_cause_analyzer(
        trace_path: str,
        process_name: str | None = None,
        anr_timestamp_ms: int | None = None,
        analysis_window_ms: int = 10_000,
        time_range: dict | None = None,
        deep_analysis: bool = False,
    ) -> str:
        """
        使用多信号关联对 ANR 事件进行全面的根本原因分析。

        使用场景：在 detect_anrs 发现 ANR 后，调查已知的冻结时间戳，
        或当用户报告应用变得无响应的特定时间时使用。此工具查看
        问题周围的 ±10 秒窗口以识别根本原因。

        分析四个关键信号：
        1. 主线程阻塞：长时间的非运行状态（I/O 等待、睡眠）阻止 UI 更新
        2. Binder 延迟：到系统服务的慢速 IPC 调用（>100ms 事务）
        3. 内存压力：低可用内存强制进行过多 GC
        4. 锁竞争：Java 同步块导致线程等待

        参数：
        - process_name: 目标进程（某些分析需要）
        - anr_timestamp_ms 或 time_range: 要调查的时刻
        - analysis_window_ms: 上下文窗口大小（默认 ±10 秒）
        - deep_analysis: true 表示增强的关联洞察
        - 验证：如果同时提供了 anr_timestamp_ms 和 time_range，时间戳必须
          位于 time_range 内，否则工具返回 INVALID_PARAMETERS

        输出洞察：
        - "likelyCauses": 可能根本原因的排名列表
        - "rationale": 解释每个原因被识别的原因
        - 每种信号类型的详细数据
        - 多个原因交互时的关联说明

        优势：与单信号工具不同，此工具关联多个数据源以识别
        真正的根本原因。例如，它可以区分"GC 期间的锁竞争导致的 ANR"
        与"慢速 binder 调用导致的 ANR"与"CPU 饥饿导致的 ANR"。

        典型发现：大多数 ANR 是由主线程锁竞争或同步
        binder 调用引起的，而不是 CPU 过载。需要跟踪中的 binder 和 monitor_contention
        模块来获取这些信号；缺失的模块在 'notes' 中报告。
        """
        return anr_root_cause_tool.anr_root_cause_analyzer(
            trace_path,
            process_name,
            anr_timestamp_ms,
            analysis_window_ms,
            time_range,
            deep_analysis,
        )
    
    @mcp.tool()
    def cpu_utilization_profiler(
        trace_path: str,
        process_name: str,
        group_by: str = "thread",
        include_frequency_analysis: bool = True,
    ) -> str:
        """
        按线程分析 CPU 使用情况以识别性能瓶颈。

        使用场景：调查高电池消耗、热节流、性能缓慢，
        或确定应用是否受 CPU 限制时使用。对于理解性能问题
        是由于过度 CPU 使用还是其他因素（I/O、锁竞争等）至关重要。

        按线程显示：
        - 跟踪持续时间的 CPU 百分比
        - 总运行时间和调度计数
        - 平均/最大时间片（长时间片 = 好，许多短时间片 = 抖动）
        - 使用的 CPU（指示线程迁移）
        - 可选：如果跟踪有 DVFS 数据，添加 CPU 频率分析

        关键指标：
        - 主线程 >80% CPU：需要卸载 UI 工作
        - 后台线程 >90%：考虑分块处理工作
        - 许多低百分比线程：可能过度线程化
        - 高调度计数但低 CPU%：可能锁竞争

        进程模式：
        - process_name: 支持通配符（"com.example.*"）
        - group_by: 目前仅支持 "thread"
        - include_frequency_analysis: 添加 CPU 频率关联

        解释：高 CPU 并不总是意味着低效代码 - 可能表示热节流
        使 CPU 保持在低频率。与 cpu_frequency 数据比较。如果 CPU 使用率
        低但性能差，改为调查锁竞争或 I/O 阻塞。

        输出：按 CPU 使用率排序的线程列表，主线程标记。使用此功能识别
        哪些特定线程需要优化。
        """
        return cpu_util_tool.cpu_utilization_profiler(
            trace_path,
            process_name,
            group_by,
            include_frequency_analysis,
        )

    @mcp.tool()
    def detect_jank_frames(
        trace_path: str,
        process_name: str,
        jank_threshold_ms: float = 16.67,
        severity_filter: list[str] | None = None,
    ) -> str:
        """
        查找掉帧/卡顿帧，提供详细的性能分类。

        使用场景：UI 感觉迟缓、滚动卡顿、动画不流畅，或
        需要量化 UI 性能问题时使用。卡顿直接影响用户体验 -
        即使只有几帧卡顿也会让应用感觉不专业。

        检测：
        - 超过截止时间的帧（60fps 为 16.67ms，120fps 为 8.33ms）
        - 卡顿来源：应用 vs SurfaceFlinger（系统合成器）
        - 严重性：基于截止时间超出的轻度、中度、重度
        - 每帧的 CPU/UI 线程时间

        参数：
        - process_name: 跟踪中的确切应用名称
        - jank_threshold_ms: 16.67（60fps）或 8.33（120fps）
        - severity_filter: ["severe", "moderate"] 以专注于最坏情况

        输出包括：
        - frame_id、timestamp、duration 用于关联
        - overrun_ms: 帧错过截止时间的程度
        - jank_type 和 source（应用 vs 系统）
        - CPU/UI 时间分解
        - 分类：SMOOTH/JANK/BIG_JANK/HUGE_JANK

        解释：
        - 偶尔卡顿（<1% 帧）：正常
        - 持续卡顿（>5% 帧）：用户可见问题
        - 卡顿集群：检查这些时间点的 GC、I/O 或锁竞争
        - SurfaceFlinger 卡顿：系统问题，不是您的应用

        后续：使用帧时间戳与 execute_sql_query 关联，了解
        卡顿帧期间发生了什么（GC 事件、binder 调用、CPU 频率）。
        """
        return jank_frames_tool.detect_jank_frames(
            trace_path,
            process_name,
            jank_threshold_ms,
            severity_filter,
        )

    @mcp.tool()
    def frame_performance_summary(trace_path: str, process_name: str) -> str:
        """
        高级帧性能指标和整体 UI 流畅度评估。

        使用场景：需要快速性能评级、建立基线指标、比较
        优化前后，或在深入特定帧之前获取概览时使用。
        这为您提供"森林视图"，而 detect_jank_frames 显示单个"树木"。

        提供：
        - 总帧数和卡顿统计
        - 卡顿率百分比（UI 流畅度的关键指标）
        - 帧类别：慢速、卡顿、大卡顿、巨大卡顿
        - CPU 时间分布：平均、最大、P95、P99
        - 性能评级：EXCELLENT/GOOD/ACCEPTABLE/POOR

        性能标准：
        - EXCELLENT: <1% 卡顿率（控制台级流畅度）
        - GOOD: 1-5% 卡顿率（大多数用户不会注意到）
        - ACCEPTABLE: 5-10% 卡顿率（高级用户会抱怨）
        - POOR: >10% 卡顿率（所有用户受影响）

        关键洞察：
        - P99 CPU 时间：最坏情况帧成本
        - 最大 CPU 时间：峰值检测（GC、加载等）
        - 大/巨大卡顿计数：用户肯定注意到的关键帧

        典型工作流程：
        1. 首先运行此工具进行整体评估
        2. 如果 POOR/ACCEPTABLE，使用 detect_jank_frames 查找特定坏帧
        3. 将坏帧时间戳与其他事件关联

        注意：不同内容类型有不同的标准。游戏可能在动作场景中接受 5% 卡顿，
        而阅读应用应始终保持 <1%。
        """
        return frame_summary_tool.frame_performance_summary(trace_path, process_name)

    @mcp.tool()
    def memory_leak_detector(
        trace_path: str,
        process_name: str,
        growth_threshold_mb_per_min: float = 5.0,
        analysis_duration_ms: int = 60_000,
    ) -> str:
        """
        通过堆增长模式和可疑类分析检测内存泄漏。

        使用场景：调查 OOM 崩溃、随时间逐渐性能下降、
        用户报告应用长时间使用后变慢，或高内存警告时使用。
        内存泄漏通常很微妙 - 小泄漏可能需要数小时才会导致可见问题。

        分析两个维度：
        1. 增长模式：RSS 内存随时间趋势
        2. 堆可疑对象：保留内存过多的类

        检测标准：
        - 持续增长 >5MB/分钟（默认阈值）
        - 特定类的支配堆大小过大
        - 增长率和堆可疑对象之间的相关性

        参数：
        - process_name: 目标应用
        - growth_threshold_mb_per_min: 泄漏指示器（默认 5.0）
        - analysis_duration_ms: 时间窗口（默认 60 秒）

        输出：
        - 增长指标：平均/最大增长率、泄漏指示器计数
        - 可疑类：按支配大小排序，包含实例计数
        - 每个类的内存影响分类

        常见泄漏模式：
        - Bitmaps/图像未回收：大的 dominated_size_mb
        - 监听器注册但未注销：高 instance_count
        - 静态集合无限增长：随时间增加
        - 上下文泄漏：堆中的 Activity/View 类

        限制：需要跟踪中的堆图数据。没有它，只有 RSS 增长分析
        可用。对于详细的泄漏路径，后续使用 heap_dominator_tree_analyzer。

        误报：缓存和池可能显示稳定增长。检查增长是否
        无限继续或趋于平稳。
        """
        return memory_leak_tool.memory_leak_detector(
            trace_path,
            process_name,
            growth_threshold_mb_per_min,
            analysis_duration_ms,
        )

    @mcp.tool()
    def heap_dominator_tree_analyzer(
        trace_path: str,
        process_name: str,
        max_classes: int = 20,
    ) -> str:
        """
        深入堆内存以识别特定的内存占用类。

        使用场景：在 memory_leak_detector 发现问题后，调查高基线
        内存使用，或优化内存占用时使用。这准确显示哪些类
        保留最多内存并阻止垃圾收集。

        分析：
        - 跟踪中的最新堆图快照
        - 支配关系（什么保持对象存活）
        - 每个类的自身 vs 本地内存
        - 可达性和 GC 根距离

        每个类的输出：
        - instance_count: 对象数量
        - self_size_mb: Java 堆内存
        - native_size_mb: 本地分配
        - total_size_mb: 组合影响
        - memory_impact: CRITICAL (>50MB)、WARNING (>20MB)、NORMAL

        关键洞察：
        - 高实例计数 + 低个体大小 = 集合泄漏
        - 低实例计数 + 高大小 = 大对象问题
        - 高 native_size: Bitmaps、本地缓冲区
        - 低 root_distance: 直接从 GC 根引用

        常见发现：
        - Bitmap/Drawable: 图像缓存问题
        - Activity/Fragment: 上下文泄漏
        - ArrayList/HashMap: 无界集合
        - 自定义类：应用特定保留

        优化目标：首先关注 CRITICAL/WARNING 类。单个修复通常
        可以恢复数十 MB。

        要求：需要堆图数据（Debug.dumpHprofData 或类似）。如果扩展
        列/模块缺失，工具回退到简化查询（省略 native_size、
        reachability、root_distance）并添加说明。如果没有堆图存在，返回
        HEAP_GRAPH_UNAVAILABLE。
        """
        return heap_dom_tool.heap_dominator_tree_analyzer(trace_path, process_name, max_classes)

    @mcp.tool()
    def thread_contention_analyzer(
        trace_path: str,
        process_name: str,
        time_range: dict | None = None,
        min_block_ms: float = 50.0,
        include_per_thread_breakdown: bool = False,
        include_examples: bool = False,
        limit: int = 80,
    ) -> str:
        """
        查找线程同步瓶颈，支持自动回退分析 - 大多数 ANR 的隐藏原因。

        使用场景：原因不明的 ANR、CPU 使用率低但 UI 冻结、死锁
        怀疑，或性能问题与 CPU/内存指标不相关时使用。
        当其他指标看起来正常时，此工具通常揭示真正原因。

        关键洞察：线程竞争是 ANR 的首要原因，比 CPU
        过载或内存压力更常见。单个放置不当的同步块可以冻结
        整个应用。

        分析模式：
        - 主要：当可用时使用 android.monitor_contention 数据进行精确 Java 锁详情
        - 回退：当监控竞争数据缺失时，自动回退到基于调度器的推断，使用 thread_state、
          sched_waking 和可选的 sched_blocked_reason 表

        检测：
        - 哪些线程被阻塞以及什么阻塞它们
        - 持有锁的特定方法（当可用时）
        - 等待持续时间和频率
        - 并发等待者计数（死锁风险指示器）
        - 通过 sched_blocked_reason 的 D 状态阻塞归因（当可用时）

        严重性分类：
        - CRITICAL: 主线程阻塞 >100ms
        - HIGH: 任何线程阻塞 >500ms 或频繁竞争
        - MEDIUM: 工作线程上的中等阻塞
        - LOW: 轻微竞争，用户不可见

        输出元数据：
        - analysisSource: "monitor_contention"（主要）或 "scheduler_inferred"（回退）
        - usesWakerLinkage: 如果唤醒者-线程关系可用则为 true
        - usedSchedBlockedReason: 如果 D 状态函数归因可用则为 true
        - primaryDataUnavailable: 当使用回退时为 true
        - fallbackNotice: 回退触发时的人类可读解释

        发现的常见反模式：
        - 热路径上的同步单例访问
        - 网络 I/O 期间持有数据库锁
        - 主线程上的 SharedPreferences.commit()
        - 嵌套同步块（死锁风险）
        - UI 线程等待后台线程锁

        推荐的跟踪配置：
        - 主要：包含 android.monitor_contention 数据源
        - 回退：包含 linux.ftrace 与 sched/sched_switch、sched/sched_waking、
          和可选的 sched/sched_blocked_reason 事件

        参数：
        - process_name: 目标应用/进程（支持确切名称；使用 find_slices/process 元数据工具进行发现）
        - time_range: {'start_ms': X, 'end_ms': Y} 将分析集中在特定窗口（例如，应用启动、ANR）
        - min_block_ms: 忽略短于此阈值的等待（默认 50ms）
        - include_per_thread_breakdown: 包含每个线程的 S/D 总计和百分比
        - include_examples: 包含顶部示例等待用于说明
        - limit: 组/示例/分解行的上限（默认 80）

        修复优先级：通常是影响巨大的简单修复。将工作移出同步块
        或使用并发结构通常完全解决问题。
        """
        return thread_contention_tool.thread_contention_analyzer(
            trace_path,
            process_name,
            time_range,
            min_block_ms,
            include_per_thread_breakdown,
            include_examples,
            limit,
        )

    @mcp.tool()
    def binder_transaction_profiler(
        trace_path: str,
        process_filter: str,
        min_latency_ms: float = 10.0,
        include_thread_states: bool = True,
        time_range: dict | None = None,
        correlate_with_main_thread: bool = False,
        group_by: str | None = None,
    ) -> str:
        """
        分析跨进程（IPC）通信性能和瓶颈。

        使用场景：系统 UI 交互缓慢、输入延迟、内容提供者
        或系统服务延迟，或当 ANR 涉及系统进程通信时使用。Binder 是
        Android 的核心 IPC 机制 - 慢速 binder 调用直接导致 ANR。

        测量：
        - 客户端延迟（包括等待 + 服务器处理）
        - 服务器端处理时间
        - 开销（客户端延迟 - 服务器时间 = IPC 开销）
        - 主线程影响（对 ANR 关键）

        参数：
        - process_filter: 匹配为客户端或服务器
        - min_latency_ms: 专注于慢速调用（默认 10ms）
        - include_thread_states: 显示调用期间线程在做什么
        - time_range: 可选的 {'start_ms': X, 'end_ms': Y} 以限定分析窗口
        - correlate_with_main_thread: 如果为 true，添加尽力而为的主线程状态摘要
        - group_by: None、'aidl'、'server_process' 之一用于聚合视图

        关键指标：
        - is_main_thread=true + latency>100ms = ANR 风险
        - 高开销 = 系统调度问题
        - 主线程上的同步调用 = 架构问题

        常见问题模式：
        - 主线程上的 ContentResolver 查询
        - UI 绘制期间的系统服务调用
        - 同步 LocationManager/SensorManager 调用
        - 主线程上的 PackageManager 操作

        输出：当 group_by 为 None 时，返回具有延迟和 overhead_ratio 的事务行。
        分组时，按 AIDL 方法或服务器进程返回聚合。

        架构洞察：高 binder 延迟通常表明需要使调用
        异步或缓存结果。考虑使用 AsyncTask、协程或缓存层。

        注意：某些系统 binder 调用是不可避免的。专注于减少频率和
        在可能的情况下移出主线程。
        """
        return binder_txn_tool.binder_transaction_profiler(
            trace_path,
            process_filter,
            min_latency_ms,
            include_thread_states,
            time_range,
            correlate_with_main_thread,
            group_by,
        )

    @mcp.tool()
    def main_thread_hotspot_slices(
        trace_path: str,
        process_name: str,
        limit: int = 80,
        time_range: dict | None = None,
        min_duration_ms: float | int | None = None,
    ) -> str:
        """
        识别进程中最重的主线程切片，按持续时间排序。

        使用场景：需要最快了解 UI 线程在哪些地方花费时间时使用。
        这非常适合 ANR 和卡顿分类，突出显示长时间运行的回调和阶段。

        参数：
        - process_name: 目标应用/进程（支持 GLOB 如 "com.example.*"）。
        - time_range: {'start_ms': X, 'end_ms': Y} 以专注于特定时期。
        - limit: 返回的最大切片数（默认 80）。
        - min_duration_ms: 仅包含 >= 阈值的切片。

        输出：
        - hotspots: 顶部切片，包含 id、时间戳、持续时间和上下文（线程/进程/轨道）。
        - summary: 总计以及是否使用主线程标志或启发式方法。
        - notes: 数据可用性和回退信息。
        """
        return main_thread_hotspot_tool.main_thread_hotspot_slices(
            trace_path,
            process_name,
            limit,
            time_range,
            min_duration_ms,
        )

    # 使用 atexit 设置清理
    atexit.register(connection_manager.cleanup)

    # 在专用模块中注册 MCP 资源
    register_resources(mcp)

    logger.info("Perfetto MCP 服务器已创建，包含连接管理")

    return mcp
