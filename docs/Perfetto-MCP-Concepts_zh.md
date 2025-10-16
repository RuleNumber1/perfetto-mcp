# Perfetto 跟踪分析参考指南（MCP版）

## 架构与数据模型

**核心数据类型**：
- **切片（Slices）**：具有开始/结束时间戳的时间边界事件，可以嵌套显示调用栈。深度参数表示嵌套级别。
- **计数器（Counters）**：在时间戳处采样的时间序列数值（CPU频率、内存、电池）
- **线程状态（Thread States）**：带有状态转换的调度信息
- **流/链接（Flows/Links）**：连接跨线程/进程的因果关系箭头

**轨道层次结构**：
- CPU核心轨道：每个核心的可运行线程切片
- 进程轨道：影响整个进程的高级事件
- 线程轨道：包括函数调用的详细执行信息
- 计数器轨道：时间序列图表
- 帧时间线：预期与实际帧切片

## 基本SQL表与查询

**核心表**：
- `slice`：持续时间事件（ts, dur, name, depth, track_id）
- `thread_state`：线程调度状态和持续时间
- `sched_slice`：纳秒精度的CPU调度
- `android_anrs`：检测到的ANR事件及其时间戳
- `counter/counter_track`：时间序列指标
- `process/thread`：元数据（名称、PID、TID）
- `android_startup`：应用启动指标

## 心智模型

Perfetto提供了一个统一的系统级时间线，连接内核、框架/服务和应用事件，以便回答：什么运行了、在哪里运行、运行了多长时间。LLM应使用的核心原语：轨道（CPU核心、进程、线程、计数器）、切片（有界工作如Choreographer#doFrame或Binder事务）、计数器（CPU频率/利用率、堆大小、电池/GPU）和流（跨线程/进程的因果关系）。

**关键分析查询**：
```sql
-- 主线程状态分布
SELECT state, SUM(dur)/1e9 AS total_sec, 
       100.0 * SUM(dur)/(SELECT MAX(ts)-MIN(ts) FROM thread_state) AS percentage
FROM thread_state JOIN thread USING(utid)
WHERE thread.name = 'main' GROUP BY state ORDER BY total_sec DESC;

-- 查找指示卡顿的调度间隙
SELECT ts, dur, cpu, end_state, thread.name, process.name
FROM sched_slice LEFT JOIN thread USING(utid) LEFT JOIN process USING(upid)
WHERE thread.is_main_thread = 1 AND dur > 16000000 -- 16ms+间隙
ORDER BY dur DESC;

-- Binder事务延迟分析
SELECT slice.name, dur/1e6 as latency_ms, ts/1e9 as timestamp_sec
FROM slice WHERE name LIKE 'binder%' AND dur > 10e6
ORDER BY dur DESC LIMIT 20;

-- GC频率和影响
SELECT ts/1e9 AS time_sec, dur/1e6 AS gc_duration_ms,
       LEAD(ts) OVER (ORDER BY ts) - ts AS time_to_next_gc
FROM slice WHERE name LIKE 'GC_%';
```

## ANR深度分析

**检测与分析工作流**：
1. **定位ANR**：在system_server中查找`am_anr`事件以获取准确时间戳
2. **识别窗口**：检查ANR检测前100ms-1s（关键时期）
3. **主线程分析**：
   - 最后响应时刻
   - 状态转换：运行（R）-> 睡眠（S）或不可中断睡眠（D）
   - 检查：I/O操作、binder事务、锁等待、GC暂停
4. **跟踪依赖关系**：
   - 线程间的唤醒链
   - Binder事务流（客户端 -> 服务器跨度）
   - 共享资源访问模式
5. **系统因素**：
   - CPU饱和（许多可运行线程）
   - System_server锁争用
   - Binder线程池耗尽

**线程状态解释**：
- **运行（R）**：当前在CPU上执行
- **可运行（R）**：准备就绪但等待CPU（CPU争用指标）
- **睡眠（S）**：自愿等待（锁、条件）
- **不可中断睡眠（D）**：I/O等待，无法中断（磁盘/网络）
- **停止（T）**：被信号挂起
- **僵尸（Z）**：已终止但未回收

**常见ANR模式**：
- **死锁**：循环等待模式，多个线程处于睡眠状态且有锁等待原因
- **主线程I/O**：操作>100ms（文件系统、数据库查询、SharedPreferences提交）
- **Binder瓶颈**：事务>1-2秒，ANR时未完成，线程池饱和
- **GC压力**：多个连续GC事件阻塞执行

## 性能分析模式

**帧生产流水线**：
1. 输入处理
2. 动画更新
3. 测量/布局传递（检查每帧多次）
4. 绘制操作（注意过度绘制）
5. RenderThread GPU命令
6. SurfaceFlinger合成

**卡顿检测指标**：
- 单帧>25ms = 可见卡顿
- 帧>32ms = 多帧丢失
- 不规则帧间隔 = 流水线问题
- 检查：Choreographer#doFrame持续时间，VSYNC-app与VSYNC-sf对齐
- 应用卡顿类型：关键应用相关卡顿包括"AppDeadlineMissed"（应用耗时超过预期）和"BufferStuffing"（应用发送帧速度快于呈现速度，创建队列积压增加输入延迟）。
- SurfaceFlinger卡顿类别：系统级卡顿包括"SurfaceFlingerCpuDeadlineMissed"（主线程超时）、"SurfaceFlingerGpuDeadlineMissed"（GPU合成延迟）、"DisplayHAL"（硬件抽象层延迟）和"PredictionError"（调度器时序漂移）。
- SQL数据访问：帧数据可通过两个主要表（`expected_frame_timeline_slice`和`actual_frame_timeline_slice`）通过SQL查询访问，提供详细的时序、令牌信息、卡顿类型和进程详细信息以进行全面性能分析。

**内存压力指标**：
- **应用级别**：GC频率>1/秒，GC持续时间>10ms，堆增长接近限制
- **系统级别**：kswapd激活，直接回收事件，lmkd进程终止，PSI指标
- **分配模式**：动画期间峰值>10MB/秒，单调增长（泄漏），快速分配/释放循环（抖动）

**级联故障识别**：
- 内存压力 -> GC风暴 -> 帧丢失
- CPU饱和 -> 调度延迟 -> ANR
- 温度升高 -> 频率限制 -> 系统范围减速
- 锁争用 -> 优先级反转 -> RenderThread延迟

## 系统组件分析

**system_server关键服务**：
- **ActivityManagerService**：进程生命周期，ANR检测（`am_anr`事件），内存修剪
- **WindowManagerService**：焦点变化，可见性更新，输入路由，动画协调
- **InputDispatcher**：5秒超时执行，输入队列管理，触摸/按键事件处理
- **PowerManagerService**：唤醒锁，电源状态转换，电池优化影响

**SurfaceFlinger**：
- Vsync信号生成（60/90/120Hz目标）
- 缓冲区队列管理（深度指示压力）
- 图层合成（数量和复杂度）
- GPU合成回退（性能影响）

**Binder IPC分析**：
- 事务组件：`binder_transaction`（传出），`binder_transaction_received`（传入）
- 线程池：Binder_1、Binder_2等（耗尽导致排队）
- 性能红色标志：延迟>10ms，队列深度增长，事务>1MB，失败事务

## 系统化分析方法论

### 初步评估检查清单：
- 跟踪有效性：足够持续时间，启用必要类别
- 基线建立：正常帧持续时间，内存模式，线程利用率
- 症状窗口识别：问题的确切时间范围
- 关键线程识别：主线程、RenderThread、相关Binder线程

### 调查工作流：

### ANR调查工作流：
1. **定位ANR事件**：在system_server中查找`am_anr`
2. **识别冻结线程**：通常是主/UI线程
3. **分析ANR前窗口**：事件前5-10秒
4. **检查线程状态**：
   - 最后响应时刻
   - 转换到阻塞状态
   - 阻塞原因/等待通道
5. **跟踪依赖关系**：
   - 进行中的Binder调用
   - 锁持有者
   - I/O操作
6. **识别根本原因**：
   - 直接阻塞者
   - 级联效应
   - 系统条件

### 常见ANR根本原因：
- **死锁**：
  - 锁获取中的循环等待模式
  - 多个线程相互等待
  - 可见为处于睡眠状态且有锁等待原因的线程
- **主线程I/O**：
  - 文件系统操作超过100ms
  - 没有异步处理的数据库查询
  - 主线程上的SharedPreferences提交
- **无限循环**：
  - CPU消耗而不让步
  - 缺少中断条件
  - 连续运行状态而无进展
- **GC压力**：
  - 过多的垃圾收集阻塞执行
  - 多个连续GC事件
  - 内存分配失败

### 卡顿调查工作流：
1. **量化问题**：SQL查询帧>16.67ms
2. **识别最差帧**：按持续时间排序
3. **逐帧分析**：
   - 主线程操作
   - RenderThread完成
   - SurfaceFlinger合成
4. **模式识别**：
   - 一致性问题与峰值
   - 与用户操作的相关性
   - 系统事件对齐
5. **根本原因分析**：
   - 应用代码问题
   - 系统资源争用
   - 环境因素

### 内存调查工作流：
1. **特征化使用情况**：增长率、峰值、基线
2. **GC模式分析**：
   - 频率和持续时间
   - 收集类型
   - 每次GC释放的内存
3. **分配跟踪**：
   - 热点分配站点
   - 大分配
   - 分配风暴
4. **系统内存相关性**：
   - 可用内存趋势
   - 内存压力事件
   - 进程终止
5. **泄漏检测**：
   - 单调增长
   - 未释放的引用
   - 本地堆与Java堆

### 实际分类问题：
- 哪个线程在关键路径上？运行中还是等待中？为什么？
- 设备是CPU受限、I/O受限、锁受限还是远程服务受限？
- 是否有GC、频率限制或热事件？
- 单一根本原因还是多个复合因素？

## 可重复的分析工作流

1. 隔离症状窗口（用户操作、卡顿集群、停滞、功耗峰值）。
2. 检查帧健康度（如果是UI）：查找超预算帧；记录时间戳。
3. 在该窗口中检查关键线程：
   - UI/主线程（测量/布局/绘制），检查GC暂停和锁等待。
   - RenderThread/GPU用于繁重的栅格化或驱动程序等待。
   - Binder池用于长时间远程调用或队列积压。
4. 与CPU关联：核心是否繁忙？线程是否可运行但未调度？设置了什么频率？
5. 识别等待：I/O（磁盘/网络）、互斥锁、调度器等待、Binder回复延迟。
6. 扫描计数器：RAM峰值 -> GC；固定低CPU频率 -> 延迟/热；电流峰值 -> 功耗回归。
7. 验证因果关系：跟踪流或Binder链请求->工作->响应。
8. 用证据总结根本原因。

## 常见反模式与解决方案

**主线程违规**：
- 同步文件I/O -> 使用后台线程
- 网络调用 -> AsyncTask/协程
- 数据库查询 -> Room与LiveData
- 位图解码 -> 后台+缓存
- SharedPreferences commit() -> 使用apply()

**布局低效**：
- 带权重的嵌套LinearLayout -> ConstraintLayout
- 复杂RelativeLayout链 -> 扁平化层次结构
- 每帧多次onLayout -> 优化失效
- 深层视图层次结构 -> Merge/ViewStub

**对象分配热点路径**：
- onDraw()中的分配 -> 预分配
- 循环中的字符串连接 -> StringBuilder
- 紧密循环中创建对象 -> 对象池
- 性能代码中的自动装箱 -> 原始数组

## 环境与跨进程因素

**热管理**：
- 跟踪中可见的CPU/GPU频率缩放
- 热区温度读数
- 限制模式：周期性下降，持续限制

**跨进程依赖关系**：
- 启动期间的PackageManager查询
- 系统服务争用（ActivityManager、WindowManager）
- 共享资源：文件锁、数据库、硬件（摄像头、传感器）
- 多跳binder链：累积延迟、超时传播

## 构建分析直觉

**模式识别特征**：
- **锁争用**：多个线程等待，相同等待通道，优先级反转
- **内存压力级联**：GC风暴 -> 分配失败 -> lmkd终止
- **热限制**：与频率变化对齐的周期性性能下降
- **Binder耗尽**：所有Binder_N线程繁忙，增长的事务队列

**关键原则**：
- 永远不要孤立分析单个轨道 - 跨层关联
- 区分症状与根本原因 - 可见问题有隐藏起源
- 使用SQL进行定量分析
- 考虑设备状态和环境因素
- 关注关键路径 - 实际阻塞用户体验的内容

**渐进式分析**：
1. 广泛指标：整体帧率、内存趋势、CPU使用率
2. 异常识别：统计异常值、模式中断
3. 聚焦分析：特定时间范围、受影响组件
4. 根本原因跟踪：依赖关系、因果链
5. 验证：跨多个出现的一致性

## ANR超时参考

| ANR类型 | 超时时间 | 关注领域 |
|---------|----------|-----------|
| 输入分发 | 5秒 | 触摸/按键事件，UI线程阻塞 |
| 广播（前台） | 10秒 | onReceive()执行，同步操作 |
| 服务（前台） | 20秒 | onCreate/onStartCommand，初始化 |
| 广播（后台） | 60秒 | 后台处理，资源争用 |

## 帧时序目标

| 显示刷新率 | 帧预算 | 卡顿阈值 |
|------------|--------|-----------|
| 60Hz | 16.67ms | >25ms |
| 90Hz | 11.11ms | >17ms |
| 120Hz | 8.33ms | >13ms |

## 诊断模式与快速线索
- UI卡顿（错过vsync）：UI上过长的doFrame，RenderThread上的繁重栅格化，或由于争用导致的延迟CPU服务。
- Binder瓶颈：客户端主线程等待回复；确认服务器线程池饱和和长处理程序。
- I/O停滞：主线程接触磁盘/网络；长系统调用；等待时CPU核心空闲。
- GC暂停：可识别的运行时切片；与堆计数器和分配爆发关联。
- 热/CPU缩放：需求下的低频率 -> 延迟；工作很少的持续高频率 -> 功耗浪费。
- 锁争用：许多短运行切片等待互斥锁；所有者线程上的长临界区。

## 线程状态健全性
当线程缓慢时，将其分类为运行（消耗CPU）、可运行（准备就绪但未调度 -> CPU争用或优先级问题）或等待/阻塞（I/O、锁、Binder回复）。跨sched/thread_state的细分加上每核心活动快速揭示限制因素。

## 快速分类检查清单（供LLM使用）
- 我们是否找到了症状窗口？
- 哪个线程在关键路径上，它是运行中、可运行还是等待中 - 为什么？
- 我们是CPU受限、I/O受限、锁受限还是远程服务受限？
- 是否有GC、频率限制或热事件？
- 单一根本原因还是多个复合因素？
- 我们是否有排名的、可操作的修复方案？