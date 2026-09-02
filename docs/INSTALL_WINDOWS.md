# Windows 安装与部署指南

## 1. 前置软件

需要安装：

- Windows 10 / 11
- Python
- Git
- LM Studio
- Codex CLI

当前已经实际验证的环境：

- Python 3.14.7
- requests 2.34.2
- mcp 2.1.1
- pytest 9.1.1
- Qwen3-8B

其他版本可能也能够运行，但尚未经过本项目验证。

## 2. 获取项目

从 GitHub 克隆：

```powershell
git clone <YOUR_REPOSITORY_URL>
cd local-qwen-coding-worker
也可以直接复制整个项目目录到本机。

## 3. ?? Python ????

### 3.1 ??????? Python

Windows ????? python ???????? Python?

???

    Get-Command python -ErrorAction SilentlyContinue
    where.exe python
    python --version

?? python --version ????????????????

?????????

    C:\Users\<USER>\AppData\Local\Microsoft\WindowsApps\python.exe

????? Windows App Execution Alias??????? Python ????

### 3.2 py Launcher ??????

???

    py --version

????? py??????????? Python?

### 3.3 ?? uv ??? Python

????? uv?

    uv --version
    uv python list

uv ??? Python ?????

    C:\Users\<USER>\AppData\Roaming\uv\python\...

????????????

### 3.4 ?????????

???????????? Python?

    & "<REAL_PYTHON_PATH>" -m venv .venv

Cold Restore ???????? .venv??????????????????????

????????

    .\.venv\Scripts\python.exe --version

?????

    .\.venv\Scripts\python.exe -m pip install -r requirements.txt

?????

    requests==2.34.2
    mcp==2.1.1
    pytest==9.1.1

???????????

    .\.venv\Scripts\python.exe

?????? PATH?WindowsApps Alias ?? Python ???????

## 4. 配置 LM Studio

安装并启动 LM Studio。
加载兼容的 Qwen 模型。
当前经过验证的模型：qwen3-8b

启动 LM Studio Local Server。
JSON Coding Worker 默认使用 OpenAI Compatible API：http://127.0.0.1:1234/v1/chat/completions

可以通过下面地址测试 LM Studio API：http://127.0.0.1:1234/v1/models

## 5. Workspace 配置

默认 Workspace 由 worker_tools.py 根据项目自身位置自动生成：<项目目录>\workspace

因此项目移动到其他目录后，默认 Workspace 会自动跟随，不需要修改绝对路径。
qwen_mcp_server.py 中还包含：ALLOWED_WORKSPACE_ROOTS

用于限制 Worker 可以操作的目录。
当前示例包括：DEFAULT_WORKSPACE
D:\Projects
D:\Research

部署到其他电脑时，应根据实际目录修改。
不要轻易允许整个系统盘。

## 6. 编译检查

运行：python -m py_compile `
    .\json_coding_worker.py `
    .\qwen_mcp_gateway.py `
    .\qwen_mcp_server.py `
    .\worker_tools.py

没有任何输出代表：PASS

## 7. Import Smoke Test
运行：@'
import requests
import mcp
import worker_tools
import json_coding_worker
import qwen_mcp_server
import qwen_mcp_gateway

print("IMPORT SMOKE TEST: PASS")
print("Default workspace:", worker_tools.DEFAULT_WORKSPACE)
'@ | python -

预期：IMPORT SMOKE TEST: PASS

## 8. 测试 LM Studio API

保持 LM Studio Server 开启。
运行：@'
import requests

response = requests.get(
    "http://127.0.0.1:1234/v1/models",
    timeout=10,
)

response.raise_for_status()

print("LM STUDIO API: PASS")

for model in response.json().get("data", []):
    print("-", model.get("id"))
'@ | python -

成功时：LM STUDIO API: PASS

并显示当前可用模型。

## 9. 配置 Codex MCP

Codex 需要使用本项目虚拟环境中的 Python 启动：qwen_mcp_gateway.py

具体绝对路径取决于仓库所在位置。
因此在新电脑部署时，不要照抄旧电脑上的绝对路径。
配置完成后，在 Codex 中检查 MCP 状态。
当前 Gateway 应暴露三个主要工具：delegate_to_qwen
delegate_coding_task
delegate_coding_task_auto

## 10. 第一次 Coding Worker 测试

不要直接使用重要项目。
建议新建一个临时 Git 项目，然后人为制造一个很简单的 Bug。
确认 Worker 可以完成：
1. 运行完整 pytest
2. 找到失败测试
3. 读取相关源码
4. 执行最小修改
5. 修改后重新运行完整 pytest
6. 执行 git diff --check
7. 检查 git diff
8. 检查 git status
9. 执行 finish
10. 返回 controller_validation: PASS

## 11. 写权限安全
正式项目在允许 Worker 修改之前，应先使用 Git。
至少确认：git status

可以正常工作。
这样即使 Worker 修改错误，也可以通过 Git 恢复。

## 12. GPU 和 VRAM

本地模型加载后：
- VRAM 长期占用通常是正常现象
- 请求过程中 GPU 利用率升高属于正常现象
- 请求结束后 GPU 计算利用率通常应该下降
模型仍然占用 VRAM，并不代表 GPU 仍在持续计算。
如果请求已经结束，但 GPU 长时间保持高计算负载，可能意味着本地推理 Backend 仍然在处理任务或处于异常状态。

## 13. Worker 故障排查

出现错误时优先检查：status
reason
action_trace
request_telemetry

特别关注：time_to_headers_seconds
time_to_first_chunk_seconds
total_inference_time_seconds

这些指标可以帮助区分：
- Qwen 推理问题
- Controller 状态机问题
- LM Studio Transport 问题
- pytest 问题
- Git 问题

## 14. 安全注意事项
不要提交：
- API Key
- Token
- Password
- .env
- 私人源码
- workspace
- 日志
- 模型权重
- .venv
详细安全规则见：docs/SECURITY.md

## 15. Cold Restore / ???????

?????????????????????????

### 15.1 ????

    git clone <YOUR_REPOSITORY_URL>
    cd local-qwen-coding-worker
    git status

??????? clean?

### 15.2 ???? Python

?????

    Get-Command python -ErrorAction SilentlyContinue
    where.exe python
    python --version
    py --version
    uv --version
    uv python list

????????????

????????????? Python ?????

### 15.3 ????????

    & "<REAL_PYTHON_PATH>" -m venv .venv

???

    .\.venv\Scripts\python.exe -m pip install -r requirements.txt

?????????? .venv?

### 15.4 ??? Import ??

???

    .\.venv\Scripts\python.exe -m py_compile .\json_coding_worker.py .\qwen_mcp_gateway.py .\qwen_mcp_server.py .\worker_tools.py

????? PASS?

Import Smoke Test ??????

    requests
    mcp
    worker_tools
    json_coding_worker
    qwen_mcp_server
    qwen_mcp_gateway

???? DEFAULT_WORKSPACE ?????????????????????

### 15.5 Git Runtime Hygiene

?? Python ? pytest ????

    git status --short

??????????? Git?

    .venv
    __pycache__
    .pytest_cache
    workspace
    logs
    model files

### 15.6 ?? LM Studio

???

    http://127.0.0.1:1234/v1/models

???????????

?? /v1/models ?????????????? Qwen ???

### 15.7 ?? MCP Gateway

Codex MCP ????????????

    <RESTORED_REPOSITORY>\.venv\Scripts\python.exe
    <RESTORED_REPOSITORY>\qwen_mcp_gateway.py

????????????????????

Gateway ??????

    delegate_to_qwen
    delegate_coding_task
    delegate_coding_task_auto

### 15.8 Cold Restore ????

??????

    Git clone                         PASS
    Fresh Python venv                 PASS
    requirements install             PASS
    py_compile                       PASS
    core imports                     PASS
    relative DEFAULT_WORKSPACE       PASS
    Git runtime hygiene              PASS
    LM Studio /v1/models             PASS
    real Qwen inference              PASS
    MCP stdio / Gateway              PASS
    JSON Coding Worker               PASS
    WorkspaceTools                   PASS
    controller permissions           PASS

???????????????????????????

