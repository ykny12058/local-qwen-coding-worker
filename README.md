# Local Qwen Coding Worker

一个运行在本地 Qwen 模型上的 Coding Worker，通过 MCP 与 Codex 连接。

当前稳定版本：**v0.3.13**

## 项目目标

本项目采用 Controller 驱动架构，而不是直接给予本地模型不受限制的 Shell 或文件系统权限。

基本流程：

Codex
→ MCP Gateway
→ JSON Coding Worker
→ Python Controller
→ WorkspaceTools
→ 目标 Git 项目

Qwen 负责分析任务并输出结构化 JSON Action。

Python Controller 负责：

- 校验模型动作
- 控制文件读取与修改
- 运行 pytest
- 检查 Git diff
- 执行最终验收
- 阻止不符合规则的操作

## 主要文件

### json_coding_worker.py

核心 Coding Worker。

当前稳定基线：**v0.3.13**

主要功能：

- JSON Action 协议
- Streaming 模型请求
- Read-Before-Edit
- Exact Replacement
- Edit Failure Recovery
- Complete pytest 强制验证
- Git Finalization Gate
- Duplicate Search Guard
- Hard Investigation Budget
- Infrastructure Telemetry
- Tail Nonce
- Controller-Driven Post-Change Validation
- Validation Tail Reserve

### qwen_mcp_gateway.py

Codex 与本地 Worker 之间的 MCP Gateway。

目前主要提供：

- delegate_to_qwen
- delegate_coding_task
- delegate_coding_task_auto

### worker_tools.py

受限制的 Workspace 工具层，包括：

- 文件列表
- 文件读取
- 代码搜索
- pytest
- 精确源码替换
- git diff --check
- git diff
- git status

### qwen_mcp_server.py

旧版 MCP Server，目前仍作为兼容模块使用。

当前 Gateway 仍从其中调用：

- call_qwen()
- resolve_workspace()

因此暂时不能删除。

## 已验证环境

- Windows 10 / 11
- Python 3.14.7
- requests 2.34.2
- mcp 2.1.1
- pytest 9.1.1
- Git
- LM Studio
- Codex CLI
- Qwen3-8B

其他版本可能也可以运行，但当前仓库快照是在以上环境中完成验证的。

## 安全

不要向仓库提交：

- API Key
- Access Token
- Password
- `.env`
- 私人项目源码
- Worker runtime workspace
- 日志
- 模型权重
- Python 虚拟环境

详细说明见：

`docs/SECURITY.md`

## 安装

Windows 部署方法见：

`docs/INSTALL_WINDOWS.md`

## 架构说明

详细设计见：

`docs/ARCHITECTURE.md`

## 版本历史

见：

`docs/VERSION_HISTORY.md`

## 关于本地模型缓存

开发过程中，在当前测试使用的 LM Studio / llama.cpp / Qwen 本地运行环境中，观察到完全相同 Prompt 在 100% LCP/KV reuse 边界附近存在异常性能表现。

因此 Worker 会在每次请求末尾加入唯一 Tail Nonce。

这样可以：

- 保留绝大部分 Prefix Cache
- 避免请求成为完全相同的 Prompt
- 绕过当前本地运行栈中观察到的 Exact-Match 边界异常

这只是针对当前已测试本地环境的工程性 workaround，并不代表所有 LM Studio、llama.cpp、Qwen 或商业 API 缓存实现都存在相同问题。

## License

目前尚未选择开源许可证。

在加入 LICENSE 前，默认适用普通版权规则。
