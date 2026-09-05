\# 架构说明



\## 1. 总体设计



Local Qwen Coding Worker 的核心思路是：



\*\*让本地模型负责判断和生成操作意图，让 Python Controller 负责真正执行操作。\*\*



基本结构：



Codex

→ MCP Gateway

→ JSON Coding Worker

→ Python Controller

→ WorkspaceTools

→ 目标 Git 项目



LM Studio 负责提供本地 Qwen 模型推理。



\## 2. 为什么不用 unrestricted shell



本项目没有直接把任意 Shell 权限交给 Qwen。



Qwen 只能输出受限制的 JSON Action，例如：



\- list\_files

\- read\_file

\- search\_code

\- run\_tests

\- replace\_in\_file

\- git\_diff\_check

\- git\_diff

\- git\_status

\- finish



Python Controller 会验证 Action 是否符合当前任务状态和权限要求。



因此：



模型提出操作 ≠ 操作一定会执行。



\## 3. 权限模型



Coding Worker 使用两个主要权限：



\- allow\_write

\- allow\_run



\### allow\_write



决定 Worker 是否允许修改源码。



\### allow\_run



决定 Worker 是否允许执行 pytest 或项目代码。



如果没有相应权限，Controller 会拒绝相关 Action。



\## 4. Read-Before-Edit



在修改某个源码文件之前，Worker 必须先读取当前文件内容。



这样可以避免模型根据过期信息或猜测直接修改源码。



如果修改失败，Controller 会要求重新读取当前源码，再构造新的精确修改。



\## 5. Exact Replacement



源码修改采用精确文本替换，而不是 unrestricted rewrite。



修改请求包含：



\- file

\- old\_text

\- new\_text

\- expected\_count



只有当 old\_text 出现次数与 expected\_count 完全一致时，修改才允许执行。



这可以降低错误批量替换的风险。



\## 6. 修改后的强制测试



任何成功源码修改都会使之前的测试结果失效。



随后必须运行完整 pytest。



流程：



源码修改

→ complete pytest

→ 根据结果继续



在完整 pytest 运行之前，不允许继续进行第二次源码修改。



\## 7. Git 最终验收



代码修改成功并不等于任务完成。



要返回：



`changes\_complete`



还必须完成：



1\. complete pytest PASS

2\. git diff --check PASS

3\. 检查 git diff

4\. 检查 git status

5\. finish



否则 Controller 不允许报告成功。



\## 8. Edit Failure Recovery



如果精确替换失败，例如：



\- expected\_count 错误

\- old\_text 已过期

\- 文件内容发生变化



Controller 会清除旧的编辑证据，并要求重新读取文件。



这样可以避免模型反复使用同一个错误 replacement。



\## 9. Investigation Budget



为了防止模型一直搜索而不修改代码，Controller 对 Debug 类任务设置调查预算。



重复搜索会被限制。



达到搜索或无进展阈值后：



`search\_code`



会被关闭。



模型必须根据已有：



\- pytest failure

\- 当前源码

\- 已读取证据



决定执行修改或返回非成功结果。



\## 10. Streaming Transport



当前 Worker 使用 OpenAI Compatible SSE Streaming 与 LM Studio 通信。



每一轮都会记录：



\- time\_to\_headers\_seconds

\- time\_to\_first\_chunk\_seconds

\- total\_inference\_time\_seconds

\- context chars

\- estimated tokens

\- request nonce



这些数据用于区分：



\- 模型推理问题

\- Controller 问题

\- LM Studio / transport 问题



\## 11. Tail Nonce



当前测试的本地运行环境曾观察到一种特殊情况：



完全相同的 Prompt 在 Exact LCP/KV reuse 边界附近可能出现异常延迟甚至 timeout。



因此每次请求末尾都会加入唯一 nonce。



这样：



\- 大部分 Prompt Prefix 仍然可以复用

\- 每次请求又不会成为 100% 完全相同的 Prompt



这是针对当前测试运行栈的工程性 workaround。



不代表所有 LM Studio、llama.cpp、Qwen 或商业 API 都存在相同问题。



\## 12. Validation Tail



v0.3.10 增加 Validation Tail。



如果正常 coding round 的最后一轮刚好成功修改源码，Controller 不会因为轮次耗尽立即结束任务。



它会额外提供一个只用于验收的有限阶段：



complete pytest

→ git diff --check

→ git diff

→ git status

→ finish



Validation Tail 不是额外 Debug 时间。



进入这个阶段后：



\- 不允许 search

\- 不允许额外 read

\- 不允许继续修改源码



如果修改后的 pytest 失败，Validation Tail 立即停止并返回 INCOMPLETE。



\## 13. Legacy Compatibility Layer



`qwen\_mcp\_server.py` 属于旧版 MCP Server 架构。



当前 Gateway 仍然依赖其中的：



\- call\_qwen()

\- resolve\_workspace()



因此暂时保留。



未来可以将这些功能拆到独立 runtime/config 模块中。

\## 14. Controller-Driven Post-Change Validation

Starting with v0.3.13, a successful source replacement can trigger deterministic post-change validation directly inside the Python Controller.

The sequence is:

```text
successful replace_in_file
-> complete pytest
-> git diff --check
-> git diff
-> git status
-> return validation evidence to Qwen
-> Qwen submits finish
```

The Controller reuses the existing action execution and state machine rather than implementing a second validation path.

Fail-stop rules:

- If post-change pytest fails, automatic validation stops immediately and the evidence is returned to Qwen.
- If `git diff --check` fails, automatic validation stops before `git diff` and `git status`.
- The Controller never auto-submits `finish`.
- Final `finish` remains Qwen-owned and continues to pass through the existing finish hard gate.
- Read Refresh Recovery remains active after post-change complete pytest.

Validation Tail remains available when the base coding budget is exhausted. If Controller-driven mechanical validation has already succeeded, the tail requires only the final Qwen `finish` action.

## 15. Search-Driven Evidence Acquisition

Starting with v0.3.14, a qualifying failing-test search may be followed by bounded authoritative evidence acquisition inside the Python Controller.

The responsibility boundary is:

    Qwen chooses search_code
    -> WorkspaceTools performs deterministic search
    -> Controller parses a bounded set of candidate paths
    -> Controller performs real read_file actions
    -> normal read state is registered
    -> search results and CURRENT file evidence return in the same LLM round
    -> Qwen reasons about the evidence and chooses the edit

The Bridge activates only when all required conditions are satisfied, including writable/runnable mode, a test-related task, complete pytest already run and failing, and no pending post-change validation.

Safety bounds:

- At most 3 distinct candidate files are automatically acquired.
- At most 8000 aggregate CURRENT file-content characters are acquired for Bridge injection.
- The 8000-character limit applies to acquired file content, not the entire final prompt payload.
- Windows backslashes are canonicalized to / only at the Bridge boundary.
- Search snippets themselves are not promoted to authoritative source evidence.
- Real read_file actions continue to satisfy the existing read-before-edit invariant.

Fallback behavior is fail-closed:

    malformed search result
    OR too many candidate files
    OR acquired content exceeds the bound
    OR read_file fails
    -> rollback Bridge read state
    -> preserve the original search result
    -> continue with v0.3.13 search behavior

The first v0.3.14 implementation does not preflight file size before read_file; an oversized file can be read into Controller memory before the aggregate content limit causes rollback. Oversized Bridge evidence is not injected into the LLM.

Search-Driven Evidence Acquisition does not change Controller-driven post-change validation, Validation Tail, the finish hard gate, or Qwen ownership of final completion.
