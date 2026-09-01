import json
import traceback
from pathlib import Path

import requests

from mcp.server import MCPServer

from worker_tools import (
    DEFAULT_WORKSPACE,
    WorkspaceTools,
)


# ============================================================
# 基础配置
# ============================================================

MODEL = "qwen3-8b"

LM_STUDIO_REQUEST_TIMEOUT = 900

LM_STUDIO_CHAT_URL = (
    "http://127.0.0.1:1234/api/v1/chat"
)

LM_STUDIO_OPENAI_URL = (
    "http://127.0.0.1:1234/v1/chat/completions"
)

MAX_AGENT_ROUNDS = 15


# ============================================================
# Workspace 安全白名单
# ============================================================

# Qwen 只允许操作这些根目录本身，
# 或者这些根目录下面的子目录。
#
# 以后你有新的项目根目录，可以继续在这里添加。
#
# 例如：
# Path(r"D:\MyCode"),
# Path(r"E:\ResearchProjects"),

ALLOWED_WORKSPACE_ROOTS = [
    DEFAULT_WORKSPACE,
    Path(r"D:\Projects").resolve(),
    Path(r"D:\Research").resolve(),
]


# ============================================================
# MCP Server
# ============================================================

mcp = MCPServer(
    "Local Qwen Worker"
)


# ============================================================
# Workspace Security
# ============================================================

def is_path_inside(
    path: Path,
    root: Path,
) -> bool:
    """
    判断 path 是否等于 root，
    或者是否位于 root 内部。
    """

    try:
        path.relative_to(root)
        return True

    except ValueError:
        return False


def resolve_workspace(
    workspace_path: str = "",
) -> Path:
    """
    解析并验证用户 / Sol 提供的 workspace。

    空字符串：
        使用默认测试 workspace。

    非空：
        必须位于 ALLOWED_WORKSPACE_ROOTS
        中某个允许根目录内部。
    """

    if not workspace_path.strip():

        candidate = (
            DEFAULT_WORKSPACE
        ).resolve()

    else:

        candidate = (
            Path(
                workspace_path
            )
            .expanduser()
            .resolve()
        )

    # --------------------------------------------------------
    # 必须存在
    # --------------------------------------------------------

    if not candidate.exists():

        raise FileNotFoundError(
            "Workspace 不存在："
            f"{candidate}"
        )

    # --------------------------------------------------------
    # 必须是目录
    # --------------------------------------------------------

    if not candidate.is_dir():

        raise NotADirectoryError(
            "Workspace 不是目录："
            f"{candidate}"
        )

    # --------------------------------------------------------
    # 白名单检查
    # --------------------------------------------------------

    allowed = False

    for root in ALLOWED_WORKSPACE_ROOTS:

        root = root.resolve()

        if is_path_inside(
            candidate,
            root,
        ):

            allowed = True
            break

    if not allowed:

        allowed_text = "\n".join(
            f"- {root}"
            for root
            in ALLOWED_WORKSPACE_ROOTS
        )

        raise PermissionError(
            "Workspace 未在允许目录白名单中。\n\n"
            f"请求目录：\n{candidate}\n\n"
            "当前允许的根目录：\n"
            f"{allowed_text}"
        )

    return candidate


# ============================================================
# 普通 Qwen 调用
# ============================================================

def call_qwen(
    task: str,
    mode: str = "worker",
) -> str:

    mode = (
        mode
        .lower()
        .strip()
    )

    if mode == "fast":

        reasoning = "off"
        max_output_tokens = 1200
        temperature = 0.7
        top_p = 0.8

    elif mode == "reasoning":

        reasoning = "on"
        max_output_tokens = 4096
        temperature = 0.6
        top_p = 0.95

    else:

        reasoning = "on"
        max_output_tokens = 2200
        temperature = 0.6
        top_p = 0.95

    body = {

        "model": MODEL,

        "input": (
            "你是一个执行型本地 Worker。\n"
            "严格执行交给你的任务。\n"
            "信息不足时明确指出，"
            "不要猜测不存在的信息。\n\n"
            f"任务：\n{task}"
        ),

        "reasoning": reasoning,

        "temperature":
            temperature,

        "top_p":
            top_p,

        "top_k":
            20,

        "min_p":
            0,

        "max_output_tokens":
            max_output_tokens,

        "store":
            False,
    }

    response = requests.post(
        LM_STUDIO_CHAT_URL,
        json=body,
        timeout=LM_STUDIO_REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    data = response.json()

    texts = []

    for item in data.get(
        "output",
        [],
    ):

        if (
            item.get("type")
            == "message"
        ):

            content = item.get(
                "content",
                "",
            )

            if content:

                texts.append(
                    content
                )

    if not texts:

        return (
            "Qwen 没有返回可用回答。"
        )

    return "\n".join(
        texts
    )


# ============================================================
# Qwen 内部 Coding Tools Schema
# ============================================================

CODING_TOOLS = [

    # --------------------------------------------------------
    # list_files
    # --------------------------------------------------------

    {
        "type": "function",

        "function": {

            "name":
                "list_files",

            "description": (
                "递归列出当前 coding workspace "
                "中的项目文件。"
                "调查项目结构时使用。"
            ),

            "parameters": {

                "type": "object",

                "properties": {},

                "additionalProperties":
                    False,
            },
        },
    },

    # --------------------------------------------------------
    # read_file
    # --------------------------------------------------------

    {
        "type": "function",

        "function": {

            "name":
                "read_file",

            "description": (
                "读取当前 workspace 中"
                "指定文本文件的真实完整内容。"
                "修改已有文件前必须先读取。"
            ),

            "parameters": {

                "type": "object",

                "properties": {

                    "relative_path": {

                        "type":
                            "string",

                        "description": (
                            "相对于当前 workspace "
                            "的文件路径"
                        ),
                    },
                },

                "required": [
                    "relative_path",
                ],

                "additionalProperties":
                    False,
            },
        },
    },

    # --------------------------------------------------------
    # search_code
    # --------------------------------------------------------

    {
        "type": "function",

        "function": {

            "name":
                "search_code",

            "description": (
                "在整个 workspace 中搜索文本。"
                "适合寻找函数、变量、错误字符串、"
                "调用关系和配置。"
            ),

            "parameters": {

                "type": "object",

                "properties": {

                    "query": {

                        "type":
                            "string",

                        "description":
                            "需要搜索的文本",
                    },

                    "file_pattern": {

                        "type":
                            "string",

                        "description": (
                            "文件模式，例如 "
                            "*.py、*.json 或 *"
                        ),
                    },

                    "max_results": {

                        "type":
                            "integer",

                        "description":
                            "最大结果数量",
                    },
                },

                "required": [
                    "query",
                ],

                "additionalProperties":
                    False,
            },
        },
    },

    # --------------------------------------------------------
    # replace_in_file
    # --------------------------------------------------------

    {
        "type": "function",

        "function": {

            "name":
                "replace_in_file",

            "description": (
                "对已有文本文件进行精确字符串替换。"
                "小范围修改必须优先使用。"
                "用于保证最小 Git diff。"
            ),

            "parameters": {

                "type": "object",

                "properties": {

                    "relative_path": {

                        "type":
                            "string",
                    },

                    "old_text": {

                        "type":
                            "string",

                        "description":
                            "原始精确文本",
                    },

                    "new_text": {

                        "type":
                            "string",

                        "description":
                            "替换后的文本",
                    },

                    "expected_count": {

                        "type":
                            "integer",

                        "description": (
                            "预期出现次数，"
                            "通常为 1"
                        ),
                    },
                },

                "required": [
                    "relative_path",
                    "old_text",
                    "new_text",
                ],

                "additionalProperties":
                    False,
            },
        },
    },

    # --------------------------------------------------------
    # write_file
    # --------------------------------------------------------

    {
        "type": "function",

        "function": {

            "name":
                "write_file",

            "description": (
                "创建新文件或完整覆盖文本文件。"
                "只有新建文件、大范围重构，"
                "或无法使用 replace_in_file 时"
                "才使用。"
            ),

            "parameters": {

                "type": "object",

                "properties": {

                    "relative_path": {

                        "type":
                            "string",
                    },

                    "content": {

                        "type":
                            "string",

                        "description":
                            "完整文件内容",
                    },
                },

                "required": [
                    "relative_path",
                    "content",
                ],

                "additionalProperties":
                    False,
            },
        },
    },

    # --------------------------------------------------------
    # run_python
    # --------------------------------------------------------

    {
        "type": "function",

        "function": {

            "name":
                "run_python",

            "description": (
                "运行当前 workspace "
                "中的指定 Python 文件。"
                "返回 STDOUT、STDERR "
                "和 EXIT CODE。"
            ),

            "parameters": {

                "type": "object",

                "properties": {

                    "relative_path": {

                        "type":
                            "string",
                    },
                },

                "required": [
                    "relative_path",
                ],

                "additionalProperties":
                    False,
            },
        },
    },

    # --------------------------------------------------------
    # run_tests
    # --------------------------------------------------------

    {
        "type": "function",

        "function": {

            "name":
                "run_tests",

            "description": (
                "使用 pytest 运行当前 workspace "
                "中的测试。"
                "修改代码后如果存在测试，"
                "优先使用此工具验收。"
            ),

            "parameters": {

                "type": "object",

                "properties": {

                    "target": {

                        "type":
                            "string",

                        "description": (
                            "pytest 测试目标。"
                            "留空表示运行全部测试。"
                        ),
                    },
                },

                "additionalProperties":
                    False,
            },
        },
    },

    # --------------------------------------------------------
    # git_diff
    # --------------------------------------------------------

    {
        "type": "function",

        "function": {

            "name":
                "git_diff",

            "description": (
                "查看当前 workspace 的"
                "未提交 Git diff。"
                "代码修改后必须检查。"
            ),

            "parameters": {

                "type": "object",

                "properties": {},

                "additionalProperties":
                    False,
            },
        },
    },

    # --------------------------------------------------------
    # git_status
    # --------------------------------------------------------

    {
        "type": "function",

        "function": {

            "name":
                "git_status",

            "description": (
                "查看当前 workspace 的 "
                "Git status --short。"
                "用于检查是否产生额外文件。"
            ),

            "parameters": {

                "type": "object",

                "properties": {},

                "additionalProperties":
                    False,
            },
        },
    },
]


# ============================================================
# Worker Report
# ============================================================

def build_worker_report(
    workspace: Path,
    success: bool,
    final_text: str,
    modified_files: list,
    last_validation_result: str,
    validation_tool: str,
    tool_history: list,
    workspace_tools: WorkspaceTools,
    note: str = "",
) -> str:

    lines = []

    lines.append(
        "=== Local Qwen Coding Worker Report ==="
    )

    lines.append(
        "Status: "
        + (
            "SUCCESS"
            if success
            else "INCOMPLETE"
        )
    )

    lines.append(
        f"Workspace root: {workspace}"
    )

    # --------------------------------------------------------
    # Modified files
    # --------------------------------------------------------

    if modified_files:

        absolute_files = []

        for relative_path in (
            modified_files
        ):

            absolute_path = (
                workspace
                / relative_path
            ).resolve()

            absolute_files.append(
                str(
                    absolute_path
                )
            )

        lines.append(
            "Modified files:\n- "
            + "\n- ".join(
                absolute_files
            )
        )

    else:

        lines.append(
            "Modified files: none"
        )

    # --------------------------------------------------------
    # Tool Trace
    # --------------------------------------------------------

    lines.append(
        f"Tool calls: "
        f"{len(tool_history)}"
    )

    if tool_history:

        lines.append(
            "\n=== Tool trace ==="
        )

        for entry in (
            tool_history
        ):

            lines.append(
                f"Round "
                f"{entry['round']}: "
                f"{entry['tool']}"
            )

    # --------------------------------------------------------
    # Validation
    # --------------------------------------------------------

    if last_validation_result:

        lines.append(
            "\n=== Last validation ==="
        )

        lines.append(
            "Validation tool: "
            f"{validation_tool}"
        )

        lines.append(
            last_validation_result
        )

    # --------------------------------------------------------
    # Git Status
    # --------------------------------------------------------

    lines.append(
        "\n=== Git status ==="
    )

    lines.append(
        workspace_tools.git_status()
    )

    # --------------------------------------------------------
    # Git Diff
    # --------------------------------------------------------

    lines.append(
        "\n=== Current git diff ==="
    )

    lines.append(
        workspace_tools.git_diff()
    )

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    if final_text.strip():

        lines.append(
            "\n=== Qwen final summary ==="
        )

        lines.append(
            final_text.strip()
        )

    if note:

        lines.append(
            "\n=== Note ==="
        )

        lines.append(
            note
        )

    return "\n".join(
        lines
    )


# ============================================================
# Coding Worker Agent
# ============================================================

def run_coding_worker(
    task: str,
    workspace: Path,
    allow_write: bool,
    allow_run: bool,
    max_rounds: int,
    mode: str = "worker",
) -> str:

    # ========================================================
    # Workspace-specific tool instance
    # ========================================================

    tools = WorkspaceTools(
        workspace
    )
        # ========================================================
    # Worker Mode
    # ========================================================

    mode = mode.lower().strip()

    if mode == "fast":

        thinking_suffix = "\n\n/no_think"

        generation_temperature = 0.2

        generation_max_tokens = 1200

    elif mode == "reasoning":

        thinking_suffix = "\n\n/think"

        generation_temperature = 0.2

        generation_max_tokens = 3200

    else:

        mode = "worker"

        thinking_suffix = "\n\n/think"

        generation_temperature = 0.2

        generation_max_tokens = 1800
    # ========================================================
    # System Prompt
    # ========================================================

    messages = [

        {
            "role": "system",

            "content": (

                "你是本地 Qwen Coding Worker。"

                "\nGPT-5.6 Sol 是你的上层 "
                "Planner 和最终 Reviewer。"

                "\n你的任务是执行边界清晰的"
                "项目级代码工作。"

                "\n\n当前唯一允许操作的 Workspace："

                f"\n{workspace}"

                "\n\n所有相对文件路径都以"
                "这个 workspace 为根目录。"

                "\n\n【调查】"

                "\n1. 不得猜测文件。"

                "\n2. 不知道项目结构时使用 "
                "list_files。"

                "\n3. 不知道代码位置时使用 "
                "search_code。"

                "\n4. 修改已有文件前必须 "
                "read_file。"

                "\n\n【修改】"

                "\n5. 必须坚持最小必要修改。"

                "\n6. 局部修改优先使用 "
                "replace_in_file。"

                "\n7. 只有新建文件、大范围重构"
                "或精确替换无法完成时"
                "才使用 write_file。"

                "\n8. 不要无关格式化。"

                "\n9. 不要随意增删空行。"

                "\n10. 不要修改无关代码。"

                "\n\n【验证】"

                "\n11. Python 脚本可使用 "
                "run_python。"

                "\n12. 项目存在 pytest 时"
                "优先使用 run_tests。"

                "\n13. EXIT CODE: 0 "
                "才算功能验证成功。"

                "\n14. 验证失败必须继续调试。"

                "\n15. 修改后必须调用 git_diff。"

                "\n16. 修改后应调用 git_status "
                "检查是否出现无关文件。"

                "\n17. 只有功能验证、diff 自检"
                "均完成后才能结束。"

                "\n18. 最终报告只需说明："
                "根因、实际修改、验证结果。"
            ),
        },

        {
            "role": "user",

            "content": (
                task
                + thinking_suffix
            ),
        },
    ]

    # ========================================================
    # State
    # ========================================================

    modified_files = []

    read_files = set()

    tool_history = []

    has_modified = False

    modification_verified = False

    diff_reviewed = False

    status_reviewed = False

    last_validation_result = ""

    validation_tool = ""

    final_text = ""

    # ========================================================
    # Agent Loop
    # ========================================================

    for round_number in range(
        1,
        max_rounds + 1,
    ):

        body = {

            "model":
                MODEL,

            "messages":
                messages,

            "tools":
                CODING_TOOLS,

            "tool_choice":
                "auto",

            "temperature":
                 generation_temperature,

            "max_tokens":
                generation_max_tokens,
        }

        response = requests.post(
            LM_STUDIO_OPENAI_URL,
            json=body,
            timeout=LM_STUDIO_REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        data = response.json()

        message = (
            data[
                "choices"
            ][0][
                "message"
            ]
        )

        tool_calls = (
            message.get(
                "tool_calls"
            )
        )

        # ====================================================
        # Qwen 想结束
        # ====================================================

        if not tool_calls:

            final_text = (
                message.get(
                    "content",
                    "",
                )
                or ""
            )

            messages.append(
                message
            )

            # ------------------------------------------------
            # 修改未验证
            # ------------------------------------------------

            if (
                has_modified
                and not modification_verified
            ):

                if not allow_run:

                    return (
                        build_worker_report(

                            workspace=
                                workspace,

                            success=
                                False,

                            final_text=
                                final_text,

                            modified_files=
                                modified_files,

                            last_validation_result=
                                last_validation_result,

                            validation_tool=
                                validation_tool,

                            tool_history=
                                tool_history,

                            workspace_tools=
                                tools,

                            note=(
                                "已经修改代码，"
                                "但 allow_run=false，"
                                "无法完成运行验收。"
                            ),
                        )
                    )

                messages.append(
                    {
                        "role":
                            "user",

                        "content": (
                            "你已经修改代码，"
                            "但还没有成功验证。"
                            "必须调用 run_python "
                            "或 run_tests，"
                            "直到 EXIT CODE: 0。"
                        ),
                    }
                )

                continue

            # ------------------------------------------------
            # 尚未检查 diff
            # ------------------------------------------------

            if (
                has_modified
                and not diff_reviewed
            ):

                messages.append(
                    {
                        "role":
                            "user",

                        "content": (
                            "修改已经通过功能验证，"
                            "但是还没有检查最终 git diff。"
                            "必须调用 git_diff。"
                        ),
                    }
                )

                continue

            # ------------------------------------------------
            # 尚未检查 status
            # ------------------------------------------------

            if (
                has_modified
                and not status_reviewed
            ):

                messages.append(
                    {
                        "role":
                            "user",

                        "content": (
                            "还必须调用 git_status，"
                            "确认没有生成无关文件"
                            "或意外修改。"
                        ),
                    }
                )

                continue

            # ------------------------------------------------
            # 正常结束
            # ------------------------------------------------

            success = (
                modification_verified
                if has_modified
                else True
            )

            return (
                build_worker_report(

                    workspace=
                        workspace,

                    success=
                        success,

                    final_text=
                        final_text,

                    modified_files=
                        modified_files,

                    last_validation_result=
                        last_validation_result,

                    validation_tool=
                        validation_tool,

                    tool_history=
                        tool_history,

                    workspace_tools=
                        tools,
                )
            )

        # ====================================================
        # Tool calls
        # ====================================================

        messages.append(
            message
        )

        for tool_call in (
            tool_calls
        ):

            function = (
                tool_call[
                    "function"
                ]
            )

            name = (
                function[
                    "name"
                ]
            )

            raw_arguments = (
                function.get(
                    "arguments",
                    "{}",
                )
            )

            # ------------------------------------------------
            # Parse Arguments
            # ------------------------------------------------

            if isinstance(
                raw_arguments,
                str,
            ):

                try:

                    arguments = (
                        json.loads(
                            raw_arguments
                        )
                    )

                except (
                    json.JSONDecodeError
                ) as exc:

                    result = (
                        "工具参数 JSON "
                        "解析失败："
                        f"{exc}"
                    )

                    messages.append(
                        {
                            "role":
                                "tool",

                            "tool_call_id":
                                tool_call[
                                    "id"
                                ],

                            "content":
                                result,
                        }
                    )

                    continue

            else:

                arguments = (
                    raw_arguments
                )

            # =================================================
            # list_files
            # =================================================

            if name == "list_files":

                result = (
                    tools.list_files()
                )

            # =================================================
            # read_file
            # =================================================

            elif name == "read_file":

                relative_path = (
                    arguments[
                        "relative_path"
                    ]
                )

                result = (
                    tools.read_file(
                        relative_path
                    )
                )

                if not result.startswith(
                    (
                        "文件不存在：",
                        "不是文件：",
                        "读取失败：",
                        "读取异常：",
                    )
                ):

                    read_files.add(
                        relative_path
                    )

            # =================================================
            # search_code
            # =================================================

            elif name == "search_code":

                result = (
                    tools.search_code(

                        query=
                            arguments[
                                "query"
                            ],

                        file_pattern=
                            arguments.get(
                                "file_pattern",
                                "*",
                            ),

                        max_results=
                            arguments.get(
                                "max_results",
                                50,
                            ),
                    )
                )

            # =================================================
            # replace_in_file
            # =================================================

            elif name == "replace_in_file":

                relative_path = (
                    arguments[
                        "relative_path"
                    ]
                )

                if not allow_write:

                    result = (
                        "replace_in_file "
                        "被拒绝："
                        "allow_write=false。"
                    )

                elif (
                    relative_path
                    not in read_files
                ):

                    result = (
                        "replace_in_file "
                        "被拒绝："
                        "必须先 read_file("
                        f"'{relative_path}')。"
                    )

                else:

                    result = (
                        tools.replace_in_file(

                            relative_path=
                                relative_path,

                            old_text=
                                arguments[
                                    "old_text"
                                ],

                            new_text=
                                arguments[
                                    "new_text"
                                ],

                            expected_count=
                                arguments.get(
                                    "expected_count",
                                    1,
                                ),
                        )
                    )

                    if (
                        result.startswith(
                            "已精确替换："
                        )
                    ):

                        has_modified = True

                        modification_verified = False
                        diff_reviewed = False
                        status_reviewed = False

                        if (
                            relative_path
                            not in modified_files
                        ):

                            modified_files.append(
                                relative_path
                            )

            # =================================================
            # write_file
            # =================================================

            elif name == "write_file":

                relative_path = (
                    arguments[
                        "relative_path"
                    ]
                )

                target = (
                    tools.safe_path(
                        relative_path
                    )
                )

                exists = (
                    target.exists()
                )

                if not allow_write:

                    result = (
                        "write_file 被拒绝："
                        "allow_write=false。"
                    )

                elif (
                    exists
                    and relative_path
                    not in read_files
                ):

                    result = (
                        "write_file 被拒绝："
                        "覆盖已有文件前必须先 "
                        "read_file("
                        f"'{relative_path}')。"
                    )

                else:

                    result = (
                        tools.write_file(

                            relative_path,

                            arguments[
                                "content"
                            ],
                        )
                    )

                    if (
                        result.startswith(
                            "已写入："
                        )
                    ):

                        has_modified = True

                        modification_verified = False
                        diff_reviewed = False
                        status_reviewed = False

                        if (
                            relative_path
                            not in modified_files
                        ):

                            modified_files.append(
                                relative_path
                            )

            # =================================================
            # run_python
            # =================================================

            elif name == "run_python":

                if not allow_run:

                    result = (
                        "run_python 被拒绝："
                        "allow_run=false。"
                    )

                else:

                    result = (
                        tools.run_python(
                            arguments[
                                "relative_path"
                            ]
                        )
                    )

                    last_validation_result = (
                        result
                    )

                    validation_tool = (
                        "run_python"
                    )

                    if has_modified:

                        modification_verified = (
                            "EXIT CODE: 0"
                            in result
                        )

            # =================================================
            # run_tests
            # =================================================

            elif name == "run_tests":

                if not allow_run:

                    result = (
                        "run_tests 被拒绝："
                        "allow_run=false。"
                    )

                else:

                    result = (
                        tools.run_tests(
                            target=
                                arguments.get(
                                    "target",
                                    "",
                                )
                        )
                    )

                    last_validation_result = (
                        result
                    )

                    validation_tool = (
                        "run_tests"
                    )

                    if has_modified:

                        modification_verified = (
                            "EXIT CODE: 0"
                            in result
                        )

            # =================================================
            # git_diff
            # =================================================

            elif name == "git_diff":

                result = (
                    tools.git_diff()
                )

                if has_modified:

                    diff_reviewed = True

            # =================================================
            # git_status
            # =================================================

            elif name == "git_status":

                result = (
                    tools.git_status()
                )

                if has_modified:

                    status_reviewed = True

            # =================================================
            # Unknown tool
            # =================================================

            else:

                result = (
                    f"未知工具：{name}"
                )

            # =================================================
            # History
            # =================================================

            tool_history.append(
                {
                    "round":
                        round_number,

                    "tool":
                        name,

                    "arguments":
                        arguments,

                    "result":
                        result,
                }
            )

            # =================================================
            # Return result to Qwen
            # =================================================

            messages.append(
                {
                    "role":
                        "tool",

                    "tool_call_id":
                        tool_call[
                            "id"
                        ],

                    "content":
                        result,
                }
            )

    # ========================================================
    # Maximum rounds
    # ========================================================

    return (
        build_worker_report(

            workspace=
                workspace,

            success=
                False,

            final_text=
                final_text,

            modified_files=
                modified_files,

            last_validation_result=
                last_validation_result,

            validation_tool=
                validation_tool,

            tool_history=
                tool_history,

            workspace_tools=
                tools,

            note=(
                "达到最大 Agent 轮数："
                f"{max_rounds}"
            ),
        )
    )


# ============================================================
# MCP Tool:
# 普通任务
# ============================================================

@mcp.tool()
def delegate_to_qwen(
    task: str,
    mode: str = "worker",
) -> str:
    """
    将普通任务委派给本机 Qwen3-8B。

    mode:
    fast / worker / reasoning
    """

    return call_qwen(
        task=task,
        mode=mode,
    )


# ============================================================
# MCP Tool:
# Coding Worker v3
# ============================================================

@mcp.tool()
def delegate_coding_task(
    task: str,
    workspace_path: str = "",
    allow_write: bool = False,
    allow_run: bool = False,
    mode: str = "worker",
    max_rounds: int = 0,
) -> str:
    """
    Delegate a coding task to the local Qwen coding worker.

    mode: fast, worker, or reasoning.
    max_rounds=0 uses the mode default.
    """

    try:

        # ----------------------------------------------------
        # Workspace
        # ----------------------------------------------------

        workspace = resolve_workspace(
            workspace_path
        )

        # ----------------------------------------------------
        # Mode
        # ----------------------------------------------------

        mode = mode.lower().strip()

        if mode not in {
            "fast",
            "worker",
            "reasoning",
        }:
            mode = "worker"

        # ----------------------------------------------------
        # Rounds
        # ----------------------------------------------------

        if int(max_rounds) <= 0:

            if mode == "fast":
                max_rounds = 8

            elif mode == "reasoning":
                max_rounds = 20

            else:
                max_rounds = 12

        else:

            max_rounds = max(
                1,
                min(
                    int(max_rounds),
                    25,
                ),
            )

        # ----------------------------------------------------
        # Worker
        # ----------------------------------------------------

        return run_coding_worker(
            task=task,
            workspace=workspace,
            allow_write=allow_write,
            allow_run=allow_run,
            max_rounds=max_rounds,
            mode=mode,
        )

    except Exception as exc:

        return (
            "=== delegate_coding_task INTERNAL ERROR ===\n"
            f"Exception: {type(exc).__name__}\n"
            f"Message: {exc}\n\n"
            "=== Traceback ===\n"
            f"{traceback.format_exc()}"
        )


@mcp.tool()
def delegate_coding_task_auto(
    task: str,
    workspace_path: str = "",
    allow_write: bool = False,
    allow_run: bool = False,
    worker_rounds: int = 12,
    reasoning_rounds: int = 20,
) -> str:
    """
    Automatically run a coding task with worker mode first.

    If worker is incomplete, continue with reasoning mode.
    """

    try:

        # ----------------------------------------------------
        # Workspace
        # ----------------------------------------------------

        workspace = resolve_workspace(
            workspace_path
        )

        # ----------------------------------------------------
        # Round limits
        # ----------------------------------------------------

        worker_rounds = max(
            1,
            min(
                int(worker_rounds),
                25,
            ),
        )

        reasoning_rounds = max(
            1,
            min(
                int(reasoning_rounds),
                25,
            ),
        )

        # ====================================================
        # Stage 1: Worker
        # ====================================================

        worker_result = run_coding_worker(
            task=task,
            workspace=workspace,
            allow_write=allow_write,
            allow_run=allow_run,
            max_rounds=worker_rounds,
            mode="worker",
        )

        # ----------------------------------------------------
        # Worker already succeeded
        # ----------------------------------------------------

        if "Final status: SUCCESS" in worker_result:

            return (
                "=== AUTO ROUTER RESULT ===\n"
                "Route: worker -> SUCCESS\n\n"
                "=== Worker stage ===\n"
                f"{worker_result}"
            )

        # ====================================================
        # Stage 2: Reasoning continuation
        # ====================================================

        continuation_task = (
            task
            + "\n\n"
            "The previous worker stage did not complete "
            "the task. Continue from the current workspace "
            "state. Preserve correct existing changes and "
            "do not restart from scratch. Investigate and "
            "resolve all remaining issues, then complete "
            "the required validation."
        )

        reasoning_result = run_coding_worker(
            task=continuation_task,
            workspace=workspace,
            allow_write=allow_write,
            allow_run=allow_run,
            max_rounds=reasoning_rounds,
            mode="reasoning",
        )

        # ----------------------------------------------------
        # Final route
        # ----------------------------------------------------

        if "Final status: SUCCESS" in reasoning_result:

            route = (
                "worker -> INCOMPLETE -> "
                "reasoning -> SUCCESS"
            )

        else:

            route = (
                "worker -> INCOMPLETE -> "
                "reasoning -> INCOMPLETE"
            )

        return (
            "=== AUTO ROUTER RESULT ===\n"
            f"Route: {route}\n\n"
            "=== Worker stage ===\n"
            f"{worker_result}\n\n"
            "=== Reasoning stage ===\n"
            f"{reasoning_result}"
        )

    except Exception as exc:

        return (
            "=== delegate_coding_task_auto "
            "INTERNAL ERROR ===\n"
            f"Exception: {type(exc).__name__}\n"
            f"Message: {exc}\n\n"
            "=== Traceback ===\n"
            f"{traceback.format_exc()}"
        )


# ============================================================
# Start MCP stdio Server
# ============================================================

if __name__ == "__main__":
    mcp.run()
