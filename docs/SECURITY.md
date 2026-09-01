\# 安全说明



\## 1. 仓库原则



本仓库只应包含：



\- 源代码

\- 文档

\- 安全的测试文件

\- 非敏感配置模板



不要提交任何真实凭据或私人项目内容。



\## 2. 严禁提交的内容



包括但不限于：



\- API Key

\- Access Token

\- Password

\- Cookie

\- Authorization Header

\- Private Key

\- `.env`

\- 真实账号配置

\- 私人项目源码

\- 未公开研究数据

\- Runtime Workspace

\- 日志

\- Prompt 记录

\- 模型权重

\- Python 虚拟环境



\## 3. 当前 .gitignore 应屏蔽



至少包括：



```text

.venv/

venv/

\_\_pycache\_\_/

.pytest\_cache/



workspace/

workspaces/



.env

.env.\*



logs/

\*.log



\*.gguf

\*.safetensors


## 4. Workspace 隔离
Coding Worker 的运行时 workspace 不应进入本仓库。
目标代码项目应该与 Worker 仓库分离。
例如：LocalQwenWorker/
Projects/
Research/

要把正式项目直接放入 Worker 仓库内部。

## 5. Secret 管理

未来如果需要 API Key 或其他凭据，推荐使用：
- 环境变量
- .env
- 系统凭据管理工具
Git 仓库中只允许提交安全模板，例如：.env.example
禁止提交真实：.env

## 6. API Key 泄露后的处理

如果凭据曾经进入 GitHub，应默认已经泄露。
正确处理顺序：
1. 立即 revoke / rotate 旧凭据
2. 修改当前源码或配置
3. 清理 Git 历史中的敏感内容
4. 重新进行 Secret Scan
5. 确认旧凭据已经无法认证
只删除 GitHub 当前文件是不够的。
因为旧凭据可能仍存在于：
- Commit History
- Git Object
- Fork
- Cache
- 自动化扫描记录

## 7. Git 历史风险

如果一个 Secret 曾经被 commit：删除当前文件
≠
删除 Git 历史

因此最安全的策略是：
第一次 commit 之前就确保没有 Secret。

## 8. 日志风险

运行日志可能包含：
- Prompt
- 模型输出
- 文件路径
- 源码片段
- 错误堆栈
- 请求内容
- Workspace 信息
- Telemetry
因此日志默认不进入 Git。
如果确实需要提交日志，应先进行人工脱敏。

## 9. 本机路径

类似：D:\Projects
D:\Research

一般不属于认证秘密。
但绝对路径可能：
- 暴露本机目录结构
- 降低项目可移植性
未来建议改为配置文件或环境变量。

## 10. 模型文件

不要提交：*.gguf
*.safetensors

模型文件通常体积巨大，也不适合普通 Git 管理。
应从原始模型来源重新下载。

## 11. 发布前检查流程

第一次 commit 前：
1. Secret Scan
2. 检查文件列表
3. git status
4. git diff --cached
5. 人工检查 staged files
第一次 push 前再次执行相同流程。

## 12. Public 仓库注意事项

一旦仓库公开，应假设新 commit 很快会被自动化工具扫描。
不要依赖：
“我上传之后马上删掉就行。”

正确原则是：

不要让 Secret 进入 Git 历史。

## 13. 当前仓库安全策略

本项目当前采用：
- 白名单发布目录
- Runtime workspace 隔离
- .venv 排除
- 日志排除
- 模型文件排除
- 首次 commit 前 Staged Secret Audit
- 首次 push 前再次检查
这些措施用于降低误提交敏感信息的风险。
