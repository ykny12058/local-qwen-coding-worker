\# 版本历史



\## 当前稳定版本

### v0.3.12 — Lean Healthy Convergence

本版本优化健康、未修改仓库的 Agent 收敛行为，并将 JSON Coding Worker 核心版本提升至 v0.3.12。

主要变化：

- 新增 Lean Healthy Convergence guidance
- 完整 pytest 已通过、源码未修改且当前源码/测试证据充分时，Worker 优先以 healthy 状态结束
- 避免仅用于 reassurance 的额外 search 与 Git 检查
- 保留最小必要调查能力，不强制固定 Action Trace
- 新增 Healthy efficiency regression guard
- Guard 要求最终 Action 为 finish
- Healthy 未修改路径不得执行 git diff --check、git diff、git status
- Healthy 路径最多允许 6 个 LLM rounds
- 实测 Healthy baseline 从 8 rounds 收敛至 5 rounds，减少 37.5%

Modified-source 安全验证流程保持不变：

- source modification -> complete pytest -> git diff --check -> git diff -> git status -> finish

完整 Regression Harness：6 passed, 0 failed



\### v0.3.11 — Maintenance / Regression Baseline

本版本是围绕 v0.3.10 稳定核心进行的维护与回归基础设施更新。

JSON Coding Worker 的核心实现仍为：

    json_coding_worker v0.3.10

本版本没有修改 Worker 核心状态机或 Coding 行为，因此 Gateway 中显示的 Backend 版本仍应为 v0.3.10。

主要新增：

- 正式 Regression Harness
- 6 个核心回归场景
- Deterministic Hard Search Budget 测试
- Deterministic Validation Tail 测试
- Edit Failure Recovery 故障注入测试
- Live Qwen 健康仓库与 Bug Repair 集成测试

最终回归结果：

    6 passed, 0 failed

维护调整：

- healthy_baseline 的 Live Qwen round budget 从 6 调整到 8
- 修正 Gateway Backend 显示：v0.3.2 -> v0.3.10
- 强化 Windows 安装和 Cold Restore 文档
- 增加 WindowsApps Python Alias 排查
- 增加 py Launcher 缺失场景说明
- 增加 uv-managed Python 恢复流程
- 明确灾难恢复时必须重新创建 fresh .venv
- 增加 Cold Restore 完整验收检查表

灾难恢复验证：

    Cold Disaster Recovery    PASS

已验证恢复链路包括：

- Git clone
- Fresh Python venv
- requirements install
- py_compile
- core imports
- relative DEFAULT_WORKSPACE
- Git runtime hygiene
- LM Studio /v1/models
- real Qwen inference
- MCP stdio / Gateway
- JSON Coding Worker
- WorkspaceTools
- controller permissions

### v0.3.10 — Validation Tail Reserve



加入 Validation Tail。



当正常 Coding Round 的最后一轮刚好完成源码修改时，不再因为轮次耗尽直接返回 INCOMPLETE。



Controller 可以进入有限的验证阶段：



```text

complete pytest

→ git diff --check

→ git diff

→ git status

→ finish
Validation Tail 只允许验收，不允许继续 Debug。
如果修改后的完整 pytest 失败：
Validation Tail
→ STOP
→ INCOMPLETE
不会额外提供新的修复轮次。
实战验证结果：status: changes_complete
controller_validation: PASS
v0.3.9 — Hard Investigation Budget
加入强制调查预算。
当模型持续搜索但没有实质进展时，Controller 会关闭 Search Phase。
解决的问题：
search
→ search
→ search
→ search
→ max_rounds exhausted
Search Phase 关闭后，模型必须根据已有：
- pytest failure
- 当前源码
- 已读文件证据
执行修改或返回非成功状态。
v0.3.8 — Tail Nonce / Exact-Match Guard
每次模型请求末尾加入唯一 nonce。
目的：
- 避免完全相同 Prompt
- 绕过测试环境中观察到的 Exact LCP/KV reuse 异常
- 保留绝大多数 Prefix Cache
测试中，Tail Nonce 可以在保留大部分缓存复用的同时，避免 exact-match 边界导致的异常延迟和 timeout。
v0.3.7 — Streaming Transport
LM Studio 请求改为 SSE Streaming。
新增 Transport Telemetry：
time_to_headers_seconds
time_to_first_chunk_seconds
total_inference_time_seconds
用于判断：
- 请求是否到达服务器
- Prompt Processing 是否启动
- 首 Token 是否正常返回
- Backend 是否可能卡住
v0.3.6 — Infrastructure Telemetry
加入每轮基础设施遥测。
记录：
- message_count
- request_context_chars
- estimated_context_tokens
- inference_time_seconds
- failed_round
- structured INFRA_ERROR
帮助区分：
- Agent Logic 问题
- LM Studio 问题
- Transport 问题
v0.3.5 — Search Convergence
加入：
- Duplicate Search Guard
- No-Progress Guidance
减少模型反复执行相同或近似搜索。
v0.3.4 — Edit Failure Recovery
当精确源码替换失败时：
- 释放旧 Read Evidence
- 要求重新读取当前文件
- 根据最新源码重新生成 replacement
避免模型不断重试已经失效的 old_text。
v0.3.3 — Read Refresh / Finish Hardening
加入：
- 修改后的 Read Refresh
- 更严格 Finish Gate
- 防止测试未通过时错误返回 changes_complete
v0.3.2
早期 JSON Coding Worker 稳定化阶段。
主要完成：
- JSON Action Controller
- 基础 WorkspaceTools
- pytest / Git 操作
- MCP Gateway 集成
v0.2.x
更早期实验版本。
此阶段仍在探索：
- Native Tool Calling
- JSON Worker
- MCP 调用架构
- 本地 Qwen 推理方式
Pre-Git Development
v0.2.x 和早期 v0.3.x 是在正式 Git 仓库建立之前开发的。
部分稳定快照保存在：
archive/
当前保留的主要 Pre-Git 稳定快照：
json_coding_worker_v0.3.10_validation_tail_stable.py
从 v0.3.10 之后，建议正式使用：
- Git Commit
- Git Tag
- Git Release
管理版本。
以后尽量不再依赖：
backup
final
stable
final_final
这种人工文件名备份方式。
