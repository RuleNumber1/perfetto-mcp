![showcase](./static/perfetto-mcp-logo.jpg)

# Perfetto MCP

> 将自然语言转换为强大的 Perfetto 跟踪分析

一个模型上下文协议（MCP）服务器，将自然语言提示转换为聚焦的 Perfetto 分析。快速解释卡顿、诊断 ANR、发现 CPU 热点线程、发现锁争用和内存泄漏 - 所有这些都无需编写 SQL。

## ✨ 功能特性

- **自然语言 → SQL**：用简单英语提问，获得精确的 Perfetto 查询
- **ANR 检测**：自动识别和分析应用程序无响应事件
- **性能分析**：CPU 分析、帧卡顿检测、内存泄漏检测
- **线程争用**：发现同步瓶颈和锁争用
- **Binder 分析**：分析 IPC 性能和慢速系统交互

![showcase](./static/Perfetto-mcp-showcase.gif)

## 📋 前提条件

- **Python 3.13+** (macOS/Homebrew):
  ```bash
  brew install python@3.13
  ```
- **uv** (推荐):
  ```bash
  brew install uv
  ```

## 🚀 快速开始

<details>
<summary><strong>Cursor</strong></summary>

[![安装 MCP 服务器](https://cursor.com/deeplink/mcp-install-dark.svg)](https://cursor.com/install-mcp?name=perfetto-mcp&config=eyJjb21tYW5kIjoidXZ4IHBlcmZldHRvLW1jcCJ9)

或添加到 `~/.cursor/mcp.json` (全局) 或 `.cursor/mcp.json` (项目):

```json
{
  "mcpServers": {
    "perfetto-mcp": {
      "command": "uvx",
      "args": ["perfetto-mcp"]
    }
  }
}
```

</details>

<details>
<summary><strong>Claude Code</strong></summary>

运行此命令。查看 [Claude Code MCP 文档](https://docs.anthropic.com/en/docs/claude-code/mcp) 获取更多信息。

```bash
# 添加到用户范围
claude mcp add perfetto-mcp --scope user -- uvx perfetto-mcp
```

或编辑 `~/claude.json` (macOS) 或 `%APPDATA%\Claude\claude.json` (Windows):

```json
{
  "mcpServers": {
    "perfetto-mcp": {
      "command": "uvx",
      "args": ["perfetto-mcp"]
    }
  }
}
```

</details>

<details>
<summary><strong>VS Code</strong></summary>

[<img alt="在 VS Code 中安装" src="https://img.shields.io/badge/VS_Code-VS_Code?style=flat-square&label=安装%20Perfetto%20MCP&color=0098FF">](https://insiders.vscode.dev/redirect?url=vscode%3Amcp%2Finstall%3F%7B%22name%22%3A%22perfetto-mcp%22%2C%22type%22%3A%22stdio%22%2C%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22perfetto-mcp%22%5D%7D)

或添加到 `.vscode/mcp.json` (项目) 或运行 "MCP: 添加服务器" 命令:

```json
{
  "mcpServers": {
    "perfetto-mcp": {
      "command": "uvx",
      "args": ["perfetto-mcp"]
    }
  }
}
```

在 GitHub Copilot Chat 的 Agent 模式中启用。

</details>

<details>
<summary><strong>Codex</strong></summary>

编辑 `~/.codex/config.toml`:

```toml
[mcp_servers.perfetto-mcp]
command = "uvx"
args = ["perfetto-mcp"]
```

</details>

### 本地安装（开发服务器）

```bash
cd perfetto-mcp-server
uv sync
uv run mcp dev src/perfetto_mcp/dev.py
```
<details>
<summary><strong>本地 MCP</strong></summary>

```json
{
  "mcpServers": {
    "perfetto-mcp-local": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/git/repo/perfetto-mcp",
        "run",
        "-m",
        "perfetto_mcp"
      ],
      "env": { "PYTHONPATH": "src" }
    }
  }
}
```
</details>

<details>
<summary><strong>使用 pip</strong></summary>

```bash
pip3 install perfetto-mcp
python3 -m perfetto_mcp
```

</details>

## 📖 使用方法

示例起始提示：
> 在 perfetto 跟踪中，我看到 FragmentManager 执行需要 438ms。你能找出为什么需要这么长时间吗？

### 必需参数

每个工具都需要这两个输入：

| 参数 | 描述 | 示例 |
|------|------|------|
| **trace_path** | Perfetto 跟踪的绝对路径 | `/path/to/trace.perfetto-trace` |
| **process_name** | 目标进程/应用名称 | `com.example.app` |

### 在您的提示中

明确指定跟踪和进程，在提示前加上：

*"使用 perfetto 跟踪 `/absolute/path/to/trace.perfetto-trace` 处理进程 `com.example.app`"*  

### 可选过滤器

许多工具支持额外的过滤（但让您的 LLM 处理这些）：

- **time_range**: `{start_ms: 10000, end_ms: 25000}`
- **工具特定阈值**: `min_block_ms`, `jank_threshold_ms`, `limit`

## 🛠️ 可用工具

### 🔎 探索与发现

| 工具 | 用途 | 示例提示 |
|------|------|----------|
| **`find_slices`** | 调查切片名称并定位热点路径 | *"查找包含 'Choreographer' 的切片名称并显示顶部示例"* |
| **`execute_sql_query`** | 运行自定义 PerfettoSQL 进行高级分析 | *"运行自定义 SQL 在前 30 秒内关联线程和帧"* |

### 🚨 ANR 分析
注意：如果记录的跟踪包含 ANR，此工具很有帮助

| 工具 | 用途 | 示例提示 |
|------|------|----------|
| **`detect_anrs`** | 查找 ANR 事件并进行严重性分类 | *"在前 10 秒内检测 ANR 并总结严重性"* |
| **`anr_root_cause_analyzer`** | 深入分析 ANR 原因并按可能性排序 | *"分析 20,000 ms 附近的 ANR 根本原因并排序可能原因"* |

### 🎯 性能分析

| 工具 | 用途 | 示例提示 |
|------|------|----------|
| **`cpu_utilization_profiler`** | 线程级 CPU 使用率和调度 | *"按线程分析 CPU 使用率并标记最热线程"* |
| **`main_thread_hotspot_slices`** | 查找运行时间最长的主线程操作 | *"列出 10s–25s 期间 >50 ms 的主线程热点"* |

### 📱 UI 性能

| 工具 | 用途 | 示例提示 |
|------|------|----------|
| **`detect_jank_frames`** | 识别错过截止时间的帧 | *"查找超过 16.67 ms 的卡顿帧并列出最差的 20 个"* |
| **`frame_performance_summary`** | 整体帧健康指标 | *"总结帧性能并报告卡顿率和 P99 CPU 时间"* |

### 🔒 并发与 IPC

| 工具 | 用途 | 示例提示 |
|------|------|----------|
| **`thread_contention_analyzer`** | 查找同步瓶颈 | *"查找 15s–30s 之间的锁争用并显示最差等待"* |
| **`binder_transaction_profiler`** | 分析 Binder IPC 性能 | *"分析慢速 Binder 事务并按服务器进程分组"* |

### 💾 内存分析

| 工具 | 用途 | 示例提示 |
|------|------|----------|
| **`memory_leak_detector`** | 查找持续的内存增长模式 | *"检测过去 60 秒内的内存泄漏信号"* |
| **`heap_dominator_tree_analyzer`** | 识别占用内存的类 | *"分析堆主导类并列出主要违规者"* |

### 输出格式

所有工具返回结构化 JSON，包含：
- **摘要**：高级发现
- **详细信息**：工具特定结果
- **元数据**：执行上下文和使用的任何回退

## 📚 资源

- **[Trace Processor Python API](https://perfetto.dev/docs/analysis/trace-processor-python)** - Perfetto 的 Python 接口
- **[Perfetto SQL 语法](https://perfetto.dev/docs/analysis/perfetto-sql-syntax)** - 自定义查询的 SQL 参考

## 📄 许可证

Apache 2.0 许可证。查看 [LICENSE](https://github.com/antarikshc/perfetto-mcp/blob/main/LICENSE) 获取详细信息。

---

<p align="center">
  <a href="https://github.com/antarikshc/perfetto-mcp">GitHub</a> •
  <a href="https://github.com/antarikshc/perfetto-mcp/issues">问题</a> •
  <a href="https://github.com/antarikshc/perfetto-mcp/blob/main/README-internal.md">文档</a>
</p>