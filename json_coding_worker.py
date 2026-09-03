import builtins
import json
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import requests

from worker_tools import WorkspaceTools

# ============================================================
# Safe Logging
# ============================================================

# MCP stdio reserves stdout for protocol traffic.
# All diagnostic output from this module goes to stderr.
#
# Windows child processes may otherwise use GBK / cp936,
# which can crash on characters such as U+FEFF.

try:
    sys.stderr.reconfigure(
        encoding="utf-8",
        errors="backslashreplace",
    )
except Exception:
    pass


def print(*args, **kwargs):
    """
    Module-local safe logger.

    Existing print(...) calls in this file are redirected
    to stderr so they cannot corrupt MCP stdout.
    """

    kwargs.setdefault(
        "file",
        sys.stderr,
    )

    return builtins.print(
        *args,
        **kwargs,
    )

# ============================================================
# Config
# ============================================================

MODEL = "qwen3-8b"

LM_STUDIO_URL = (
    "http://127.0.0.1:1234/v1/chat/completions"
)

REQUEST_TIMEOUT = 120
MAX_ROUNDS = 18
MAX_RESULT_CHARS = 16000
MIN_EVIDENCE_CHARS = 8

# Writable test/debug tasks get a bounded search phase per
# debugging generation. The budget resets only after a source
# modification followed by COMPLETE pytest.
MAX_UNIQUE_SEARCHES_PER_GENERATION = 4
MAX_NO_PROGRESS_BEFORE_SEARCH_LOCK = 4

# If the base round budget expires after a successful source edit,
# reserve a small validation-only tail. Five actions are normally
# required (pytest, diff-check, diff, status, finish); six rounds
# allow one recoverable model/JSON mistake without reopening coding.
VALIDATION_TAIL_MAX_ROUNDS = 6


# ============================================================
# Worker State
# ============================================================

@dataclass
class WorkerState:
    workspace: Path

    # Hard permission gates
    allow_write: bool = False
    allow_run: bool = False

    listed_files: bool = False

    read_files: set[str] = field(
        default_factory=set
    )

    file_contents: dict[str, str] = field(
        default_factory=dict
    )

    # --------------------------------------------------------
    # Investigation state
    # --------------------------------------------------------

    # Exact search keys already used in the current debugging
    # generation. The key is (query, file_pattern).
    search_keys: set[tuple[str, str]] = field(
        default_factory=set
    )

    # Counts consecutive investigation actions that do not
    # advance the executable repair state. This is guidance
    # only; hard safety gates remain separate.
    no_progress_steps: int = 0

    # --------------------------------------------------------
    # Test state
    # --------------------------------------------------------

    tests_run: bool = False
    full_tests_run: bool = False
    full_tests_passed: bool = False
    post_change_validation_passed: bool = False

    # --------------------------------------------------------
    # Modification state
    # --------------------------------------------------------

    modified: bool = False

    modified_files: set[str] = field(
        default_factory=set
    )

    modification_count: int = 0

    # After every successful modification,
    # complete pytest must run before another modification.
    change_needs_test: bool = False

    # --------------------------------------------------------
    # Git validation
    # --------------------------------------------------------

    git_diff_check_seen: bool = False
    git_diff_check_passed: bool = False
    git_diff_check_result: str = ""

    git_diff_seen: bool = False
    git_diff_result: str = ""

    git_status_seen: bool = False
    git_status_result: str = ""

    def invalidate_after_change(self) -> None:
        """
        Any successful source modification invalidates
        all previous test and Git validation.
        """

        self.modified = True
        self.modification_count += 1

        self.change_needs_test = True

        self.full_tests_run = False
        self.full_tests_passed = False
        self.post_change_validation_passed = False

        self.git_diff_check_seen = False
        self.git_diff_check_passed = False
        self.git_diff_check_result = ""

        self.git_diff_seen = False
        self.git_diff_result = ""

        self.git_status_seen = False
        self.git_status_result = ""


# ============================================================
# JSON Parser
# ============================================================

def extract_json(text: str) -> dict:

    text = (
        text
        .lstrip("\ufeff")
        .strip()
    )

    if text.startswith("```"):

        text = re.sub(
            r"^```(?:json)?\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )

        text = re.sub(
            r"\s*```$",
            "",
            text,
        )

        text = text.strip()

    try:

        data = json.loads(text)

        if not isinstance(
            data,
            dict,
        ):
            raise ValueError(
                "JSON 顶层必须是 object。"
            )

        return data

    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")

    if (
        start == -1
        or end == -1
        or end <= start
    ):

        raise ValueError(
            "没有找到有效 JSON object。"
        )

    data = json.loads(
        text[
            start:end + 1
        ]
    )

    if not isinstance(
        data,
        dict,
    ):

        raise ValueError(
            "JSON 顶层必须是 object。"
        )

    return data


# ============================================================
# Helpers
# ============================================================

def normalize_whitespace(
    text: str,
) -> str:

    return " ".join(
        text.split()
    )


def truncate_result(
    text: str,
) -> str:

    if len(text) <= MAX_RESULT_CHARS:

        return text

    omitted = (
        len(text)
        - MAX_RESULT_CHARS
    )

    return (
        text[:MAX_RESULT_CHARS]
        + "\n\n"
        + (
            "[CONTROLLER: output truncated; "
            f"{omitted} characters omitted]"
        )
    )


def request_context_chars(
    messages: list,
) -> int:
    """
    Approximate request-history size for diagnostics only.

    This function does not trim or otherwise change messages.
    """

    try:

        serialized = json.dumps(
            messages,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    except Exception:

        serialized = str(
            messages
        )

    return len(
        serialized
    )


def estimate_context_tokens(
    char_count: int,
) -> int:
    """
    Rough diagnostic estimate only.

    Actual token count depends on the Qwen tokenizer and the
    English/Chinese/code mix in the request.
    """

    if char_count <= 0:

        return 0

    return max(
        1,
        int(
            round(
                char_count / 4.0
            )
        ),
    )


def build_infra_error_payload(
    *,
    reason: str,
    failed_round: int,
    request_chars: int,
    estimated_tokens: int,
    message_count: int,
    state: WorkerState,
    trace: list,
    request_telemetry: list,
    total_elapsed: float,
    transport_meta: dict | None = None,
) -> str:
    """
    Preserve the accumulated black-box state when LM Studio
    or the HTTP transport fails.
    """

    transport_meta = (
        transport_meta
        or {}
    )

    payload = {
        "status":
            "INFRA_ERROR",

        "reason":
            reason,

        "failed_round":
            failed_round,

        "request_context_chars":
            request_chars,

        "estimated_context_tokens":
            estimated_tokens,

        "message_count":
            message_count,

        "transport":
            transport_meta.get(
                "transport",
                "stream",
            ),

        "time_to_headers_seconds":
            transport_meta.get(
                "time_to_headers_seconds"
            ),

        "time_to_first_chunk_seconds":
            transport_meta.get(
                "time_to_first_chunk_seconds"
            ),

        "total_inference_time_seconds":
            transport_meta.get(
                "total_inference_time_seconds"
            ),

        "allow_write":
            state.allow_write,

        "allow_run":
            state.allow_run,

        "modified_files":
            sorted(
                state.modified_files
            ),

        "modification_count":
            state.modification_count,

        "full_tests_run":
            state.full_tests_run,

        "full_tests_passed":
            state.full_tests_passed,

        "change_needs_test":
            state.change_needs_test,

        "git_diff_check_seen":
            state.git_diff_check_seen,

        "git_diff_check_passed":
            state.git_diff_check_passed,

        "git_diff_checked":
            state.git_diff_seen,

        "git_status_checked":
            state.git_status_seen,

        "total_worker_time_seconds":
            round(
                total_elapsed,
                3,
            ),

        "request_telemetry":
            request_telemetry,

        "action_trace":
            trace,
    }

    return json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
    )


def test_result_passed(
    result: str,
) -> bool:

    return (
        "EXIT CODE: 0"
        in result
    )


def task_looks_test_related(
    task: str,
) -> bool:

    text = task.lower()

    keywords = [
        "pytest",
        "test",
        "tests",
        "failure",
        "failed",
        "failing",
        "bug",
        "debug",
        "error",
        "fix",
        "repair",
        "测试",
        "失败",
        "错误",
        "报错",
        "修复",
        "调试",
    ]

    return any(
        word in text
        for word in keywords
    )


def investigation_search_locked(
    state: WorkerState,
    task: str,
) -> bool:
    """
    Hard convergence gate for writable test/debug tasks.

    Search remains flexible during early diagnosis. Once the
    worker has:
    - a failing COMPLETE pytest,
    - at least one current file read,
    - and has exhausted either the unique-search budget or the
      no-progress budget,

    further search_code actions are blocked for this debugging
    generation.

    A successful source edit followed by COMPLETE pytest starts
    a new generation and clears search_keys/no_progress_steps,
    automatically reopening search.
    """

    if not (
        state.allow_write
        and state.allow_run
        and task_looks_test_related(
            task
        )
        and state.full_tests_run
        and not state.full_tests_passed
        and bool(
            state.read_files
        )
    ):

        return False

    unique_budget_exhausted = (
        len(
            state.search_keys
        )
        >= MAX_UNIQUE_SEARCHES_PER_GENERATION
    )

    no_progress_budget_exhausted = (
        state.no_progress_steps
        >= MAX_NO_PROGRESS_BEFORE_SEARCH_LOCK
    )

    return (
        unique_budget_exhausted
        or no_progress_budget_exhausted
    )


def validation_tail_needed(
    state: WorkerState,
) -> bool:
    """
    Whether an exhausted base loop may enter/continue the
    validation-only tail.

    The tail exists ONLY to validate a source modification that
    already happened during the base round budget. It never grants
    extra investigation or repair rounds.

    Tail stops immediately when:
    - post-change COMPLETE pytest fails, or
    - git diff --check has run and failed.
    """

    if not (
        state.allow_write
        and state.allow_run
        and state.modified
        and state.modification_count > 0
    ):

        return False

    # Immediately after an edit, COMPLETE pytest is mandatory.
    if state.change_needs_test:

        return True

    # If post-change COMPLETE pytest has not happened for any
    # unexpected reason, validation is still pending.
    if not state.full_tests_run:

        return True

    # A failing post-change suite means a new repair generation
    # would be required. The validation tail must not become extra
    # debugging budget.
    if not state.full_tests_passed:

        return False

    # A failed whitespace/diff check also requires repair rather
    # than further validation-only actions.
    if (
        state.git_diff_check_seen
        and not state.git_diff_check_passed
    ):

        return False

    # Tests pass: git gates and final finish still remain.
    return True


def validation_tail_expected_action(
    state: WorkerState,
) -> str | None:
    """
    Return the single next action permitted in validation tail.
    """

    if not validation_tail_needed(
        state
    ):

        return None

    if (
        state.change_needs_test
        or not state.full_tests_run
    ):

        return "run_tests"

    if not state.full_tests_passed:

        return None

    if not state.git_diff_check_seen:

        return "git_diff_check"

    if not state.git_diff_check_passed:

        return None

    if not state.git_diff_seen:

        return "git_diff"

    if not state.git_status_seen:

        return "git_status"

    return "finish"


def validate_validation_tail_action(
    action_data: dict,
    state: WorkerState,
) -> tuple[bool, str]:
    """
    Hard phase gate for the validation-only tail.

    No search/read/edit/list action can consume the extra rounds.
    """

    expected = (
        validation_tail_expected_action(
            state
        )
    )

    action = action_data.get(
        "action"
    )

    if expected is None:

        return (
            False,
            (
                "CONTROLLER VALIDATION TAIL STOP: "
                "Validation-only continuation is no longer "
                "permitted. Return INCOMPLETE rather than "
                "reopening investigation or editing."
            ),
        )

    if action != expected:

        return (
            False,
            (
                "CONTROLLER VALIDATION TAIL REJECTED: "
                "The base coding round budget is exhausted. "
                "This phase is validation-only. "
                f"The ONLY permitted next action is "
                f"{expected!r}; received {action!r}. "
                "Do not search, read, list, or modify source "
                "during the validation tail."
            ),
        )

    if expected == "run_tests":

        args = (
            action_data.get(
                "args",
                {}
            )
            or {}
        )

        target = args.get(
            "target",
            "",
        )

        if not isinstance(
            target,
            str,
        ) or target.strip():

            return (
                False,
                (
                    "CONTROLLER VALIDATION TAIL REJECTED: "
                    "The required validation action is COMPLETE "
                    'pytest only: run_tests target="".'
                ),
            )

    return (
        True,
        (
            "CONTROLLER: validation-tail action accepted."
        ),
    )


def task_allows_cosmetic_changes(
    task: str,
) -> bool:

    text = task.lower()

    keywords = [
        "comment",
        "comments",
        "documentation",
        "docstring",
        "format",
        "formatting",
        "style",
        "注释",
        "文档",
        "格式",
        "排版",
    ]

    return any(
        word in text
        for word in keywords
    )


def non_comment_code_lines(
    text: str,
) -> list[str]:

    lines = []

    for line in text.splitlines():

        stripped = line.strip()

        if not stripped:
            continue

        if stripped.startswith("#"):
            continue

        lines.append(
            stripped
        )

    return lines


def looks_cosmetic_only(
    old_text: str,
    new_text: str,
) -> bool:
    """
    Detect replacements that only change:
    - whitespace
    - formatting
    - Python comments
    """

    if (
        normalize_whitespace(
            old_text
        )
        ==
        normalize_whitespace(
            new_text
        )
    ):

        return True

    return (
        non_comment_code_lines(
            old_text
        )
        ==
        non_comment_code_lines(
            new_text
        )
    )


# ============================================================
# Exact-Match / LCP Guard
# ============================================================

def new_controller_request_nonce() -> str:
    """
    Return a per-request tail nonce.

    LM Studio / llama.cpp can enter an abnormal exact-prefix
    reuse path when the new prompt is byte-for-byte/token-for-token
    identical to the slot's cached prompt.

    Appending a tiny changing suffix keeps almost all useful prefix
    cache reuse while avoiding the 100% exact-match boundary case.
    """

    return uuid.uuid4().hex


# ============================================================
# Qwen Chat API
# ============================================================

def ask_qwen(
    messages: list,
    max_tokens: int = 850,
    transport_meta: dict | None = None,
) -> str:
    """
    OpenAI-compatible Chat API over streaming SSE.

    Streaming avoids the LM Studio non-streaming response
    finalization failure mode observed during local testing.

    Deliberately does NOT use native:
    tools=
    tool_choice=
    """

    if transport_meta is None:

        transport_meta = {}

    transport_meta.clear()

    transport_meta[
        "transport"
    ] = "stream"

    body = {
        "model":
            MODEL,

        "messages":
            messages,

        "temperature":
            0.1,

        "max_tokens":
            max_tokens,

        "stream":
            True,
    }

    request_start = (
        time.perf_counter()
    )

    with requests.post(
        LM_STUDIO_URL,
        json=body,
        stream=True,
        timeout=(
            10,
            REQUEST_TIMEOUT,
        ),
    ) as response:

        transport_meta[
            "time_to_headers_seconds"
        ] = round(
            (
                time.perf_counter()
                - request_start
            ),
            3,
        )

        response.raise_for_status()

        response.encoding = "utf-8"

        content_parts = []
        saw_done = False
        first_chunk_seen = False

        for line in response.iter_lines(
            decode_unicode=True,
        ):

            if not line:

                continue

            if not line.startswith(
                "data:"
            ):

                continue

            payload_text = (
                line[5:]
                .strip()
            )

            if not payload_text:

                continue

            if not first_chunk_seen:

                first_chunk_seen = True

                transport_meta[
                    "time_to_first_chunk_seconds"
                ] = round(
                    (
                        time.perf_counter()
                        - request_start
                    ),
                    3,
                )

            if payload_text == "[DONE]":

                saw_done = True
                break

            try:

                chunk = json.loads(
                    payload_text
                )

            except json.JSONDecodeError as exc:

                raise requests.RequestException(
                    "Invalid SSE JSON from LM Studio: "
                    f"{exc}"
                ) from exc

            choices = chunk.get(
                "choices",
                [],
            )

            if not choices:

                continue

            delta = (
                choices[0]
                .get(
                    "delta",
                    {},
                )
                or {}
            )

            piece = (
                delta.get(
                    "content",
                    "",
                )
                or ""
            )

            if piece:

                content_parts.append(
                    piece
                )

        transport_meta[
            "total_inference_time_seconds"
        ] = round(
            (
                time.perf_counter()
                - request_start
            ),
            3,
        )

        transport_meta[
            "saw_done"
        ] = saw_done

        transport_meta.setdefault(
            "time_to_first_chunk_seconds",
            None,
        )

        if not saw_done:

            raise requests.RequestException(
                "LM Studio streaming response ended "
                "before data: [DONE]."
            )

    content = "".join(
        content_parts
    )

    return (
        content
        .lstrip("\ufeff")
    )

# ============================================================
# Git diff --check
# ============================================================

def run_git_diff_check(
    workspace: Path,
) -> tuple[bool, str]:

    try:

        completed = subprocess.run(
            [
                "git",
                "diff",
                "--check",
            ],
            cwd=str(
                workspace
            ),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )

    except Exception as exc:

        return (
            False,
            (
                "GIT DIFF CHECK ERROR\n"
                f"{type(exc).__name__}: "
                f"{exc}"
            ),
        )

    output = "\n".join(
        part
        for part in [
            completed.stdout.strip(),
            completed.stderr.strip(),
        ]
        if part
    )

    if completed.returncode == 0:

        return (
            True,
            (
                "GIT DIFF CHECK: PASS\n"
                "EXIT CODE: 0"
            ),
        )

    return (
        False,
        (
            "GIT DIFF CHECK: FAIL\n"
            f"{output}\n"
            f"EXIT CODE: "
            f"{completed.returncode}"
        ),
    )


# ============================================================
# Finish Validation
# ============================================================

def validate_finish_payload(
    action_data: dict,
    state: WorkerState,
    task: str,
) -> tuple[bool, str]:

    status = action_data.get(
        "status"
    )

    summary = action_data.get(
        "summary"
    )

    findings = action_data.get(
        "findings",
        [],
    )

    allowed_status = {
        "healthy",
        "issues_found",
        "tests_failed",
        "changes_complete",
        "analysis_complete",
        "unknown",
    }

    # ========================================================
    # Basic schema
    # ========================================================

    if status not in allowed_status:

        return (
            False,
            (
                "FINISH_REJECTED: "
                "无效 status。"
            ),
        )

    if not isinstance(
        summary,
        str,
    ) or not summary.strip():

        return (
            False,
            (
                "FINISH_REJECTED: "
                "summary 必须非空。"
            ),
        )

    if not isinstance(
        findings,
        list,
    ):

        return (
            False,
            (
                "FINISH_REJECTED: "
                "findings 必须是 array。"
            ),
        )

    # ========================================================
    # Modification semantics
    # ========================================================

    if state.modified:

        if not state.allow_write:

            return (
                False,
                (
                    "FINISH_REJECTED: "
                    "Controller detected modifications "
                    "during a read-only invocation."
                ),
            )

        if not state.allow_run:

            return (
                False,
                (
                    "FINISH_REJECTED: "
                    "Source was modified but code execution "
                    "was not authorized, so mandatory "
                    "post-change validation cannot complete."
                ),
            )

        if status != "changes_complete":

            return (
                False,
                (
                    "FINISH_REJECTED: "
                    "本轮已经修改源码，"
                    "最终 status 必须是 "
                    "changes_complete。"
                ),
            )

    # ========================================================
    # changes_complete: HARD success gate
    #
    # IMPORTANT:
    # state.modified only means that THIS invocation made a
    # successful replacement. It does not prove that the
    # CURRENT workspace is valid. Therefore this success gate
    # is intentionally independent of state.modified.
    # ========================================================

    if status == "changes_complete":

        if not state.allow_write:

            return (
                False,
                (
                    "FINISH_REJECTED: "
                    "changes_complete requires "
                    "allow_write=True."
                ),
            )

        if not state.allow_run:

            return (
                False,
                (
                    "FINISH_REJECTED: "
                    "changes_complete requires "
                    "allow_run=True."
                ),
            )

        if state.change_needs_test:

            return (
                False,
                (
                    "FINISH_REJECTED: "
                    "最后一次修改之后尚未重新运行 "
                    "完整 pytest。"
                ),
            )

        if not state.full_tests_run:

            return (
                False,
                (
                    "FINISH_REJECTED: "
                    "changes_complete 要求已经执行 "
                    "完整 pytest。"
                ),
            )

        if not state.full_tests_passed:

            return (
                False,
                (
                    "FINISH_REJECTED: "
                    "完整 pytest 当前未通过，"
                    "不能报告 changes_complete。"
                ),
            )

        if (
            state.modified
            and not state.post_change_validation_passed
        ):

            return (
                False,
                (
                    "FINISH_REJECTED: "
                    "本轮修改后的硬验证没有通过。"
                ),
            )

        if not state.git_diff_check_seen:

            return (
                False,
                (
                    "FINISH_REJECTED: "
                    "changes_complete 前尚未执行 "
                    "git diff --check。"
                ),
            )

        if not state.git_diff_check_passed:

            return (
                False,
                (
                    "FINISH_REJECTED: "
                    "git diff --check 当前失败。"
                ),
            )

        if not state.git_diff_seen:

            return (
                False,
                (
                    "FINISH_REJECTED: "
                    "changes_complete 前尚未检查 "
                    "最终 git diff。"
                ),
            )

        if not state.git_status_seen:

            return (
                False,
                (
                    "FINISH_REJECTED: "
                    "changes_complete 前尚未检查 "
                    "最终 git status。"
                ),
            )

    # ========================================================
    # Read-only investigation semantics
    # ========================================================

    if (
        status == "analysis_complete"
        and state.allow_write
    ):

        return (
            False,
            (
                "FINISH_REJECTED: "
                "analysis_complete 仅用于未启用写权限的 "
                "read-only investigation。"
            ),
        )

    # ========================================================
    # Test/debug minimum evidence
    # ========================================================

    if (
        state.allow_run
        and task_looks_test_related(
            task
        )
        and status in {
            "healthy",
            "tests_failed",
            "changes_complete",
        }
        and not state.full_tests_run
    ):

        return (
            False,
            (
                "FINISH_REJECTED: "
                "测试/Debug 类任务的最终结论需要 "
                "完整 pytest 证据。"
            ),
        )

    # ========================================================
    # Test consistency
    # ========================================================

    if status == "tests_failed":

        if not state.allow_run:

            return (
                False,
                (
                    "FINISH_REJECTED: "
                    "allow_run=False，"
                    "没有权限产生真实测试失败结论。"
                    "请使用 analysis_complete 或 unknown。"
                ),
            )

        if not state.full_tests_run:

            return (
                False,
                (
                    "FINISH_REJECTED: "
                    "没有完整 pytest 证据。"
                ),
            )

        if state.full_tests_passed:

            return (
                False,
                (
                    "FINISH_REJECTED: "
                    "完整 pytest 实际已通过。"
                ),
            )

    if (
        status == "healthy"
        and state.allow_run
        and task_looks_test_related(
            task
        )
    ):

        if not state.full_tests_run:

            return (
                False,
                (
                    "FINISH_REJECTED: "
                    "测试/Debug 类任务没有完整 pytest，"
                    "不能报告 healthy。"
                ),
            )

        if not state.full_tests_passed:

            return (
                False,
                (
                    "FINISH_REJECTED: "
                    "pytest 当前失败，"
                    "不能报告 healthy。"
                ),
            )

    # ========================================================
    # Evidence Lock
    # ========================================================

    if (
        status == "issues_found"
        and not findings
    ):

        return (
            False,
            (
                "FINISH_REJECTED: "
                "issues_found 必须包含 findings。"
            ),
        )

    for index, finding in enumerate(
        findings,
        start=1,
    ):

        if not isinstance(
            finding,
            dict,
        ):

            return (
                False,
                (
                    "FINISH_REJECTED: "
                    f"finding #{index} 格式错误。"
                ),
            )

        file_path = finding.get(
            "file"
        )

        finding_text = finding.get(
            "finding"
        )

        evidence = finding.get(
            "evidence"
        )

        if not isinstance(
            file_path,
            str,
        ) or not file_path:

            return (
                False,
                (
                    "FINISH_REJECTED: "
                    f"finding #{index} 缺少 file。"
                ),
            )

        if file_path not in state.read_files:

            return (
                False,
                (
                    "FINISH_REJECTED: "
                    f"{file_path} 当前 debugging generation "
                    "没有读取。"
                ),
            )

        if not isinstance(
            finding_text,
            str,
        ) or not finding_text.strip():

            return (
                False,
                (
                    "FINISH_REJECTED: "
                    f"finding #{index} 缺少 finding。"
                ),
            )

        if not isinstance(
            evidence,
            str,
        ):

            return (
                False,
                (
                    "FINISH_REJECTED: "
                    f"finding #{index} 缺少 evidence。"
                ),
            )

        evidence = (
            evidence.strip()
        )

        if len(evidence) < (
            MIN_EVIDENCE_CHARS
        ):

            return (
                False,
                (
                    "FINISH_REJECTED: "
                    f"finding #{index} "
                    "evidence 过短。"
                ),
            )

        current_content = (
            state.file_contents.get(
                file_path,
                "",
            )
        )

        if (
            normalize_whitespace(
                evidence
            )
            not in
            normalize_whitespace(
                current_content
            )
        ):

            return (
                False,
                (
                    "FINISH_REJECTED: "
                    f"finding #{index} evidence "
                    f"无法在当前 {file_path} 中找到。"
                ),
            )

    return (
        True,
        "FINISH_ACCEPTED",
    )


# ============================================================
# Action Executor
# ============================================================

def execute_action(
    tools: WorkspaceTools,
    action_data: dict,
    state: WorkerState,
    task: str,
) -> tuple[bool, str]:

    action = action_data.get(
        "action"
    )

    args = action_data.get(
        "args",
        {},
    )

    if not isinstance(
        args,
        dict,
    ):

        return (
            False,
            (
                "ERROR: args 必须是 object。"
            ),
        )

    # ========================================================
    # list_files
    # ========================================================

    if action == "list_files":

        if state.listed_files:

            return (
                False,
                (
                    "CONTROLLER_REJECTED: "
                    "list_files 已执行过。"
                ),
            )

        result = (
            tools.list_files()
        )

        state.listed_files = True

        return (
            False,
            result,
        )

    # ========================================================
    # read_file
    # ========================================================

    if action == "read_file":

        relative_path = args.get(
            "relative_path"
        )

        if not isinstance(
            relative_path,
            str,
        ) or not relative_path:

            return (
                False,
                (
                    "ERROR: read_file "
                    "需要 relative_path。"
                ),
            )

        if relative_path in state.read_files:

            state.no_progress_steps += 1

            return (
                False,
                (
                    "CONTROLLER_REJECTED: "
                    f"{relative_path} 已读取过。"
                ),
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

            state.read_files.add(
                relative_path
            )

            state.file_contents[
                relative_path
            ] = result

            # A fresh file read is new authoritative evidence.
            state.no_progress_steps = 0

        return (
            False,
            result,
        )

    # ========================================================
    # search_code
    # ========================================================

    if action == "search_code":

        query = args.get(
            "query"
        )

        file_pattern = args.get(
            "file_pattern",
            "*",
        )

        max_results = args.get(
            "max_results",
            50,
        )

        if not isinstance(
            query,
            str,
        ) or not query:

            return (
                False,
                (
                    "ERROR: search_code "
                    "需要 query。"
                ),
            )

        if not isinstance(
            file_pattern,
            str,
        ):

            return (
                False,
                (
                    "ERROR: file_pattern "
                    "必须是字符串。"
                ),
            )

        try:

            max_results = int(
                max_results
            )

        except (
            TypeError,
            ValueError,
        ):

            return (
                False,
                (
                    "ERROR: max_results "
                    "必须是整数。"
                ),
            )

        # ----------------------------------------------------
        # HARD INVESTIGATION BUDGET / SEARCH PHASE GATE
        # ----------------------------------------------------

        if investigation_search_locked(
            state,
            task,
        ):

            state.no_progress_steps += 1

            return (
                False,
                (
                    "CONTROLLER SEARCH PHASE CLOSED: "
                    "The investigation budget for this debugging "
                    "generation is exhausted. Further search_code "
                    "actions are disabled.\n"
                    f"Unique searches used: "
                    f"{len(state.search_keys)}/"
                    f"{MAX_UNIQUE_SEARCHES_PER_GENERATION}. "
                    f"No-progress score: "
                    f"{state.no_progress_steps}/"
                    f"{MAX_NO_PROGRESS_BEFORE_SEARCH_LOCK}.\n"
                    "Do NOT try another search phrase. "
                    "Use the CURRENT failing-test evidence and "
                    "CURRENT file evidence. If they justify a "
                    "minimal source fix, perform replace_in_file "
                    "now. Otherwise finish with a non-success "
                    "status rather than continuing investigation. "
                    "Search will reopen only in a new debugging "
                    "generation after a successful edit followed "
                    "by COMPLETE pytest."
                ),
            )

        # ----------------------------------------------------
        # DUPLICATE SEARCH GUARD
        # ----------------------------------------------------

        search_key = (
            query.strip(),
            file_pattern.strip(),
        )

        if search_key in state.search_keys:

            state.no_progress_steps += 1

            return (
                False,
                (
                    "CONTROLLER_REJECTED: "
                    "当前 debugging generation 已执行过相同 "
                    "search_code。不要重复同一 query + "
                    "file_pattern。\n"
                    "使用现有证据采取下一步；只有确有新的定位需求时，"
                    "才使用不同且更具体的搜索。"
                ),
            )

        result = (
            tools.search_code(
                query=query,
                file_pattern=file_pattern,
                max_results=max_results,
            )
        )

        state.search_keys.add(
            search_key
        )

        # Unique searches remain allowed. However, once COMPLETE
        # pytest is known to fail, repeated investigation can
        # become search paralysis, so count it for soft guidance.
        if (
            state.full_tests_run
            and not state.full_tests_passed
        ):

            state.no_progress_steps += 1

        return (
            False,
            result,
        )

    # ========================================================
    # run_tests
    # ========================================================

    if action == "run_tests":

        # ----------------------------------------------------
        # HARD PERMISSION GATE
        # ----------------------------------------------------

        if not state.allow_run:

            return (
                False,
                (
                    "CONTROLLER_REJECTED: "
                    "This invocation has allow_run=False. "
                    "Running pytest or project code is "
                    "not permitted."
                ),
            )

        target = args.get(
            "target",
            "",
        )

        if not isinstance(
            target,
            str,
        ):

            return (
                False,
                (
                    "ERROR: target "
                    "必须是字符串。"
                ),
            )

        # ----------------------------------------------------
        # Capture pre-test state.
        #
        # Only a COMPLETE pytest run that is required after
        # a successful source modification may start a new
        # debugging generation and reset the read lock.
        # ----------------------------------------------------

        is_full_test = (
            not target.strip()
        )

        is_post_change_test = (
            state.modified
            and state.change_needs_test
            and is_full_test
        )

        result = (
            tools.run_tests(
                target=target
            )
        )

        state.tests_run = True

        if is_full_test:

            state.full_tests_run = True
            state.no_progress_steps = 0

            passed = (
                test_result_passed(
                    result
                )
            )

            state.full_tests_passed = (
                passed
            )

            if state.modified:

                state.change_needs_test = False

                state.post_change_validation_passed = (
                    passed
                )

            # ------------------------------------------------
            # READ REFRESH RECOVERY
            #
            # A successful source modification followed by
            # COMPLETE pytest starts a new debugging
            # generation. Files read in the previous
            # generation may then be read again.
            #
            # Duplicate reads within the SAME generation are
            # still rejected by the normal read_file guard.
            # ------------------------------------------------

            if is_post_change_test:

                state.read_files.clear()
                state.search_keys.clear()
                state.no_progress_steps = 0

                result += (
                    "\n\n"
                    "CONTROLLER: Complete pytest finished "
                    "after a source modification.\n"
                    "A new debugging generation has started. "
                    "Previously read source files may now be "
                    "read again if needed.\n"
                    "Repeated reads within this same "
                    "generation are still rejected."
                )

        return (
            False,
            result,
        )

# ========================================================
# replace_in_file
# ========================================================

    if action == "replace_in_file":

        # ----------------------------------------------------
        # HARD WRITE PERMISSION GATE
        # ----------------------------------------------------

        if not state.allow_write:

            return (
                False,
                (
                    "CONTROLLER_REJECTED: "
                    "This invocation has allow_write=False. "
                    "Source modification is not permitted."
                ),
            )

        # ----------------------------------------------------
        # Writing requires validation permission
        # ----------------------------------------------------

        if not state.allow_run:

            return (
                False,
                (
                    "CONTROLLER_REJECTED: "
                    "Source modification requires "
                    "allow_run=True because complete pytest "
                    "validation is mandatory after every change."
                ),
            )

        relative_path = args.get(
            "relative_path"
        )

        old_text = args.get(
            "old_text"
        )

        new_text = args.get(
            "new_text"
        )

        expected_count = args.get(
            "expected_count",
            1,
        )

        if not isinstance(
            relative_path,
            str,
        ) or not relative_path:

            return (
                False,
                (
                    "ERROR: replace_in_file "
                    "需要 relative_path。"
                ),
            )

        if not isinstance(
            old_text,
            str,
        ) or not old_text:

            return (
                False,
                (
                    "ERROR: old_text "
                    "必须非空。"
                ),
            )

        if not isinstance(
            new_text,
            str,
        ):

            return (
                False,
                (
                    "ERROR: new_text "
                    "必须是字符串。"
                ),
            )

        try:

            expected_count = int(
                expected_count
            )

        except (
            TypeError,
            ValueError,
        ):

            return (
                False,
                (
                    "ERROR: expected_count "
                    "必须是整数。"
                ),
            )

        # ----------------------------------------------------
        # Modify -> complete pytest -> next modify
        # ----------------------------------------------------

        if state.change_needs_test:

            return (
                False,
                (
                    "CONTROLLER_REJECTED: "
                    "上一次成功修改之后尚未运行 "
                    "完整 pytest。"
                    "在进行下一次修改之前，"
                    "必须先 run_tests target=\"\"。"
                ),
            )

        # ----------------------------------------------------
        # Must read first
        # ----------------------------------------------------

        if relative_path not in state.read_files:

            return (
                False,
                (
                    "CONTROLLER_REJECTED: "
                    f"修改 {relative_path} "
                    "前必须先 read_file。"
                ),
            )

        # ----------------------------------------------------
        # No-op protection
        # ----------------------------------------------------

        if old_text == new_text:

            return (
                False,
                (
                    "CONTROLLER_REJECTED: "
                    "old_text 与 new_text 相同。"
                ),
            )

        # ----------------------------------------------------
        # Reject cosmetic-only changes
        # ----------------------------------------------------

        if (
            looks_cosmetic_only(
                old_text,
                new_text,
            )
            and not
            task_allows_cosmetic_changes(
                task
            )
        ):

            return (
                False,
                (
                    "CONTROLLER_REJECTED: "
                    "此次 replacement "
                    "只改变注释、空白或格式，"
                    "与当前 Bugfix 任务无关。"
                    "保持最小必要修改。"
                ),
            )

        current_content = (
            state.file_contents.get(
                relative_path,
                "",
            )
        )

        actual_count = (
            current_content.count(
                old_text
            )
        )

        if (
            actual_count
            != expected_count
        ):

            # ------------------------------------------------
            # EDIT FAILURE RECOVERY
            #
            # The exact edit was built from stale, malformed,
            # or otherwise non-matching text. Revoke the
            # current read lock/cache for this file so the
            # model MUST perform a fresh read before trying
            # another replacement.
            # ------------------------------------------------

            state.read_files.discard(
                relative_path
            )

            state.file_contents.pop(
                relative_path,
                None,
            )

            # Recovery is explicit: the next useful action is a
            # fresh read, not more speculative searching.
            state.no_progress_steps = 0

            return (
                False,
                (
                    "CONTROLLER_REJECTED: "
                    f"old_text 实际出现 "
                    f"{actual_count} 次，"
                    f"expected_count="
                    f"{expected_count}。\n\n"
                    "CONTROLLER EDIT RECOVERY: "
                    f"{relative_path} 的当前 read lock "
                    "已释放。不要原样重试同一个 "
                    "replace_in_file。\n"
                    "下一步必须重新 read_file 该文件，"
                    "然后仅根据最新文件内容重建精确、"
                    "最小的 old_text/new_text 和 "
                    "expected_count。"
                ),
            )

        result = (
            tools.replace_in_file(
                relative_path=relative_path,
                old_text=old_text,
                new_text=new_text,
                expected_count=expected_count,
            )
        )

        if result.startswith(
            "已精确替换："
        ):

            state.modified_files.add(
                relative_path
            )

            state.invalidate_after_change()
            state.no_progress_steps = 0

            # Refresh current content automatically
            refreshed = (
                tools.read_file(
                    relative_path
                )
            )

            state.file_contents[
                relative_path
            ] = refreshed

            state.read_files.add(
                relative_path
            )

            return (
                False,
                (
                    f"{result}\n\n"
                    "CONTROLLER: "
                    "Modification accepted.\n"
                    "You MUST now run COMPLETE pytest "
                    "before any further replacement.\n"
                    "All previous Git validation "
                    "is invalid."
                ),
            )

        # ----------------------------------------------------
        # Tool-level edit failure recovery
        #
        # If WorkspaceTools rejected/failed the replacement
        # after the controller-side checks, the cached read is
        # no longer trusted for another exact edit attempt.
        # Force a fresh read before retrying this file.
        # ----------------------------------------------------

        state.read_files.discard(
            relative_path
        )

        state.file_contents.pop(
            relative_path,
            None,
        )

        return (
            False,
            (
                f"{result}\n\n"
                "CONTROLLER EDIT RECOVERY: "
                f"{relative_path} 的当前 read lock "
                "已释放。不要原样重试失败的 "
                "replace_in_file。\n"
                "下一步必须重新 read_file 该文件，"
                "并根据最新内容重建精确 replacement。"
            ),
        )

    # ========================================================
    # git_diff_check
    # ========================================================

    if action == "git_diff_check":

        passed, result = (
            run_git_diff_check(
                state.workspace
            )
        )

        state.git_diff_check_seen = True
        state.git_diff_check_passed = passed
        state.git_diff_check_result = result

        return (
            False,
            result,
        )

    # ========================================================
    # git_diff
    # ========================================================

    if action == "git_diff":

        result = (
            tools.git_diff()
        )

        state.git_diff_seen = True
        state.git_diff_result = result

        return (
            False,
            result,
        )

    # ========================================================
    # git_status
    # ========================================================

    if action == "git_status":

        result = (
            tools.git_status()
        )

        state.git_status_seen = True
        state.git_status_result = result

        return (
            False,
            result,
        )

    # ========================================================
    # finish
    # ========================================================

    if action == "finish":

        accepted, reason = (
            validate_finish_payload(
                action_data,
                state,
                task,
            )
        )

        if not accepted:

            return (
                False,
                reason,
            )

        payload = {
            "status":
                action_data.get(
                    "status"
                ),

            "summary":
                action_data.get(
                    "summary"
                ),

            "findings":
                action_data.get(
                    "findings",
                    [],
                ),

            "allow_write":
                state.allow_write,

            "allow_run":
                state.allow_run,

            "modified_files":
                sorted(
                    state.modified_files
                ),

            "modification_count":
                state.modification_count,

            "full_tests_run":
                state.full_tests_run,

            "full_tests_passed":
                state.full_tests_passed,

            "post_change_validation_passed":
                state.post_change_validation_passed,

            "git_diff_check_passed":
                state.git_diff_check_passed,

            "git_diff_checked":
                state.git_diff_seen,

            "git_status_checked":
                state.git_status_seen,

            "controller_validation":
                "PASS",
        }

        return (
            True,
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
            ),
        )

    # ========================================================
    # Unknown
    # ========================================================

    return (
        False,
        (
            "ERROR: unknown action. "
            "Allowed: list_files, read_file, "
            "search_code, run_tests, replace_in_file, "
            "git_diff_check, git_diff, git_status, finish."
        ),
    )


# ============================================================
# Round Guidance
# ============================================================


# ============================================================
# Controller-Driven Post-Change Validation
# ============================================================

def run_controller_post_change_validation(
    tools: WorkspaceTools,
    state: WorkerState,
    task: str,
    initial_result: str,
) -> tuple[bool, str]:
    """
    Run deterministic validation immediately after one successful
    source replacement.

    This helper orchestrates existing action-executor handlers.
    It does not duplicate pytest/Git state-update logic and it
    never auto-generates the final finish action.
    """

    evidence_parts = [
        initial_result,
    ]

    allowed_actions = {
        "run_tests",
        "git_diff_check",
        "git_diff",
        "git_status",
    }

    # Exactly four deterministic validation actions can exist:
    #
    # complete pytest
    # -> git diff --check
    # -> git diff
    # -> git status
    #
    # The final finish action always remains Qwen-controlled.
    for _ in range(4):

        expected = (
            validation_tail_expected_action(
                state
            )
        )

        if expected == "finish":

            evidence_parts.append(
                (
                    "CONTROLLER POST-CHANGE VALIDATION: PASS\n"
                    "Complete pytest and all required Git "
                    "validation steps completed.\n"
                    "Controller did NOT auto-finish. "
                    "Qwen must submit the final finish action."
                )
            )

            return (
                False,
                "\n\n".join(
                    evidence_parts
                ),
            )

        if expected is None:

            if (
                state.full_tests_run
                and not state.full_tests_passed
            ):

                stop_reason = (
                    "CONTROLLER POST-CHANGE VALIDATION: STOP\n"
                    "Complete pytest failed. "
                    "Automatic Git validation was not continued. "
                    "Return control to Qwen for debugging."
                )

            elif (
                state.git_diff_check_seen
                and not state.git_diff_check_passed
            ):

                stop_reason = (
                    "CONTROLLER POST-CHANGE VALIDATION: STOP\n"
                    "git diff --check failed. "
                    "Automatic git diff / git status were not "
                    "continued. changes_complete is not permitted."
                )

            else:

                stop_reason = (
                    "CONTROLLER POST-CHANGE VALIDATION: "
                    "INTERNAL STOP\n"
                    "Validation planner returned no deterministic "
                    "next action. Return control to Qwen."
                )

            evidence_parts.append(
                stop_reason
            )

            return (
                False,
                "\n\n".join(
                    evidence_parts
                ),
            )

        if expected not in allowed_actions:

            evidence_parts.append(
                (
                    "CONTROLLER POST-CHANGE VALIDATION: "
                    "INTERNAL STOP\n"
                    f"Unexpected deterministic action: "
                    f"{expected!r}. "
                    "No automatic continuation was performed."
                )
            )

            return (
                False,
                "\n\n".join(
                    evidence_parts
                ),
            )

        action_data = {
            "action": expected,
            "args": (
                {
                    "target": "",
                }
                if expected == "run_tests"
                else {}
            ),
        }

        step_finished, step_result = (
            execute_action(
                tools,
                action_data,
                state,
                task,
            )
        )

        evidence_parts.append(
            (
                "CONTROLLER AUTO VALIDATION STEP: "
                f"{expected}\n"
                f"{step_result}"
            )
        )

        if step_finished:

            evidence_parts.append(
                (
                    "CONTROLLER POST-CHANGE VALIDATION: "
                    "INTERNAL STOP\n"
                    "A deterministic validation action "
                    "unexpectedly marked the Worker finished. "
                    "Automatic continuation stopped."
                )
            )

            return (
                False,
                "\n\n".join(
                    evidence_parts
                ),
            )

    final_expected = (
        validation_tail_expected_action(
            state
        )
    )

    if final_expected == "finish":

        evidence_parts.append(
            (
                "CONTROLLER POST-CHANGE VALIDATION: PASS\n"
                "Complete pytest and all required Git "
                "validation steps completed.\n"
                "Controller did NOT auto-finish. "
                "Qwen must submit the final finish action."
            )
        )

        return (
            False,
            "\n\n".join(
                evidence_parts
            ),
        )

    evidence_parts.append(
        (
            "CONTROLLER POST-CHANGE VALIDATION: INTERNAL STOP\n"
            "Deterministic validation exceeded its fixed "
            "four-step budget without reaching finish-ready "
            "state. Automatic continuation stopped."
        )
    )

    return (
        False,
        "\n\n".join(
            evidence_parts
        ),
    )


def build_round_guidance(
    task: str,
    round_number: int,
    max_rounds: int,
    state: WorkerState,
) -> str:

    remaining = (
        max_rounds
        - round_number
    )

    lines = [
        "CONTROLLER ROUND STATUS:",
        (
            f"Current round: "
            f"{round_number}/{max_rounds}"
        ),
        (
            f"Rounds remaining after this: "
            f"{remaining}"
        ),
        (
            f"allow_write: "
            f"{state.allow_write}"
        ),
        (
            f"allow_run: "
            f"{state.allow_run}"
        ),
    ]

    # ========================================================
    # Permission reminders
    # ========================================================

    if not state.allow_write:

        lines.append(
            (
                "PERMISSION: This invocation is READ ONLY. "
                "Do not request replace_in_file."
            )
        )

    if not state.allow_run:

        lines.append(
            (
                "PERMISSION: Code/test execution is disabled. "
                "Do not request run_tests."
            )
        )

    # ========================================================
    # Modified state
    # ========================================================

    if state.modified:

        if state.change_needs_test:

            lines.append(
                (
                    "MANDATORY NEXT STEP: "
                    "Run COMPLETE pytest with target=\"\". "
                    "No further source modification "
                    "is allowed until that test run completes."
                )
            )

        elif not state.full_tests_passed:

            lines.append(
                (
                    "FACT: Latest post-change COMPLETE pytest "
                    "still fails. The task is NOT complete. "
                    "Re-read relevant current source files when "
                    "needed, reassess the remaining failures, "
                    "and make only the smallest necessary change."
                )
            )

        elif not state.git_diff_check_seen:

            lines.append(
                (
                    "MANDATORY: Full pytest passes. "
                    "Now run git_diff_check."
                )
            )

        elif not state.git_diff_check_passed:

            lines.append(
                (
                    "MANDATORY: git diff --check FAILED. "
                    "Do not finish. Correct the invalid diff "
                    "with the smallest necessary change."
                )
            )

        elif not state.git_diff_seen:

            lines.append(
                (
                    "MANDATORY: Inspect final git_diff."
                )
            )

        elif not state.git_status_seen:

            lines.append(
                (
                    "MANDATORY: Inspect final git_status."
                )
            )

        else:

            lines.append(
                (
                    "HARD VALIDATION READY: "
                    "pytest passed, git diff --check passed, "
                    "and diff/status were inspected."
                )
            )

            lines.append(
                (
                    "MANDATORY NEXT ACTION: "
                    "Return finish now with "
                    'status="changes_complete". '
                    "Do not perform any additional investigation "
                    "or modification."
                )
            )

    # ========================================================
    # Test/debug reminders for invocations with no successful
    # replacement yet. This is important for dirty workspaces
    # inherited from earlier incomplete repair attempts.
    # ========================================================

    elif (
        state.allow_run
        and task_looks_test_related(
            task
        )
        and not state.tests_run
    ):

        lines.append(
            (
                "HIGH VALUE NEXT STEP: "
                "This is a test/debug task. "
                "Run COMPLETE pytest before broad investigation."
            )
        )

    elif (
        state.allow_run
        and task_looks_test_related(
            task
        )
        and state.full_tests_run
        and not state.full_tests_passed
    ):

        lines.append(
            (
                "FACT: The latest COMPLETE pytest still fails. "
                "The task is NOT complete. "
                "Do not return healthy or changes_complete. "
                "Continue debugging the CURRENT workspace and "
                "remaining failures with the smallest necessary "
                "source change."
            )
        )

    # ========================================================
    # Lean Healthy convergence
    # ========================================================

    if (
        state.allow_run
        and task_looks_test_related(task)
        and state.full_tests_run
        and state.full_tests_passed
        and not state.modified
    ):
        lines.append(
            (
                "CONVERGENCE GUIDANCE: Complete pytest passes and no source modification has occurred. "
                "If enough CURRENT source/test evidence has already been inspected, finish now with status=healthy. "
                "If evidence is still missing, inspect only the smallest missing evidence. "
                "Do not perform additional search or git checks merely for reassurance."
            )
        )

    # ========================================================
    # Investigation convergence / hard search phase gate
    # ========================================================

    search_phase_closed = (
        investigation_search_locked(
            state,
            task,
        )
    )

    if (
        state.allow_write
        and state.allow_run
        and task_looks_test_related(
            task
        )
        and state.full_tests_run
        and not state.full_tests_passed
    ):

        lines.append(
            (
                "INVESTIGATION BUDGET: "
                f"unique searches "
                f"{len(state.search_keys)}/"
                f"{MAX_UNIQUE_SEARCHES_PER_GENERATION}; "
                f"no-progress "
                f"{state.no_progress_steps}/"
                f"{MAX_NO_PROGRESS_BEFORE_SEARCH_LOCK}."
            )
        )

    if search_phase_closed:

        lines.append(
            (
                "CONTROLLER SEARCH PHASE CLOSED: "
                "Do NOT request search_code again in this debugging "
                "generation and do NOT invent a new search phrase. "
                "Current failing-test evidence plus current file "
                "evidence must now drive an executable decision. "
                "If they identify a minimal safe fix, the next useful "
                "action is replace_in_file. If no safe edit is "
                "justified, finish with a non-success status. "
                "A successful edit followed by COMPLETE pytest starts "
                "a new debugging generation and reopens search."
            )
        )

    else:

        if (
            state.allow_run
            and task_looks_test_related(
                task
            )
            and state.full_tests_run
            and not state.full_tests_passed
            and state.no_progress_steps >= 3
        ):

            lines.append(
                (
                    "CONTROLLER NO-PROGRESS WARNING: "
                    "The current COMPLETE pytest still fails, and "
                    "several investigation actions have occurred "
                    "without an executable repair step. "
                    "Do not repeat searches or reread files already "
                    "inspected in this debugging generation. "
                    "If current test evidence plus current source "
                    "evidence identifies a minimal fix, perform that "
                    "exact minimal replace_in_file now."
                )
            )

        if state.no_progress_steps >= 5:

            lines.append(
                (
                    "STRONG CONVERGENCE DIRECTIVE: "
                    "Investigation is stagnating. Stop broad searching. "
                    "Use the best CURRENT evidence to make the smallest "
                    "justified edit, or finish with a non-success status "
                    "if the task cannot be completed safely."
                )
            )

    # ========================================================
    # Convergence
    # ========================================================

    if remaining <= 3:

        lines.append(
            (
                "CONVERGENCE WARNING: "
                "Only perform actions needed to satisfy "
                "remaining validation gates."
            )
        )

    return "\n".join(
        lines
    )


# ============================================================
# Forced Finalization
# ============================================================

def force_final_summary(
    messages: list,
    state: WorkerState,
    task: str,
) -> str | None:

    # ========================================================
    # HARD FORCED-FINALIZATION GATE
    #
    # Forced finalization must never turn an unfinished
    # writable debugging task into a false success.
    # ========================================================

    if state.allow_write:

        # ----------------------------------------------------
        # This invocation actually modified source.
        # It may only be force-finalized after every
        # post-change validation gate has passed.
        # ----------------------------------------------------

        if state.modified:

            ready = (
                state.allow_run
                and not state.change_needs_test
                and state.full_tests_run
                and state.full_tests_passed
                and state.post_change_validation_passed
                and state.git_diff_check_seen
                and state.git_diff_check_passed
                and state.git_diff_seen
                and state.git_status_seen
            )

            if not ready:

                return None

        # ----------------------------------------------------
        # No successful replacement occurred in THIS
        # invocation.
        #
        # A writable test/debug task with failed or missing
        # COMPLETE pytest is unfinished. Never ask the model
        # to manufacture a final success for that state.
        # ----------------------------------------------------

        elif (
            state.allow_run
            and task_looks_test_related(
                task
            )
        ):

            if (
                not state.full_tests_run
                or not state.full_tests_passed
            ):

                return None

    messages.append(
        {
            "role":
                "user",

            "content": (
                "CONTROLLER FINALIZATION:\n"
                "No more tools are available.\n"
                "Return exactly one JSON finish action.\n"
                "Use only evidence already collected.\n"
                "/no_think"
            ),
        }
    )

    try:

        raw = ask_qwen(
            messages,
            max_tokens=900,
        )

        data = extract_json(
            raw
        )

    except Exception:

        return None

    if data.get(
        "action"
    ) != "finish":

        return None

    accepted, _ = (
        validate_finish_payload(
            data,
            state,
            task,
        )
    )

    if not accepted:

        return None

    payload = {
        "status":
            data.get(
                "status"
            ),

        "summary":
            data.get(
                "summary"
            ),

        "findings":
            data.get(
                "findings",
                [],
            ),

        "allow_write":
            state.allow_write,

        "allow_run":
            state.allow_run,

        "modified_files":
            sorted(
                state.modified_files
            ),

        "modification_count":
            state.modification_count,

        "full_tests_run":
            state.full_tests_run,

        "full_tests_passed":
            state.full_tests_passed,

        "post_change_validation_passed":
            state.post_change_validation_passed,

        "git_diff_check_passed":
            state.git_diff_check_passed,

        "git_diff_checked":
            state.git_diff_seen,

        "git_status_checked":
            state.git_status_seen,

        "controller_validation":
            "PASS",
    }

    return json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
    )


# ============================================================
# System Prompt
# ============================================================

def build_system_prompt(
    allow_write: bool,
    allow_run: bool,
) -> str:

    permissions = []

    if allow_write:

        permissions.append(
            (
                "WRITE PERMISSION: ENABLED.\n"
                "replace_in_file may be used when necessary."
            )
        )

    else:

        permissions.append(
            (
                "WRITE PERMISSION: DISABLED.\n"
                "Do NOT request replace_in_file."
            )
        )

    if allow_run:

        permissions.append(
            (
                "RUN PERMISSION: ENABLED.\n"
                "run_tests may be used."
            )
        )

    else:

        permissions.append(
            (
                "RUN PERMISSION: DISABLED.\n"
                "Do NOT request run_tests."
            )
        )

    permission_text = "\n\n".join(
        permissions
    )

    return f"""
You are a local JSON coding worker.

You do NOT use native function calling.
The Python controller executes your JSON actions.

Return exactly ONE JSON object per response.
No Markdown.
No code fences.
No extra prose.


CURRENT PERMISSIONS

{permission_text}


AVAILABLE ACTIONS

1. List project files

{{"action":"list_files","args":{{}}}}


2. Read one file

{{"action":"read_file","args":{{"relative_path":"src/example.py"}}}}


3. Search code

{{"action":"search_code","args":{{"query":"symbol","file_pattern":"*.py","max_results":50}}}}


4. Run pytest

{{"action":"run_tests","args":{{"target":""}}}}

This action requires allow_run=True.


5. Make an exact minimal replacement

{{
  "action":"replace_in_file",
  "args":{{
    "relative_path":"src/example.py",
    "old_text":"exact current text",
    "new_text":"minimal replacement",
    "expected_count":1
  }}
}}

This action requires:
- allow_write=True
- allow_run=True
- the file must have been read first


6. Check whitespace validity

{{"action":"git_diff_check","args":{{}}}}


7. Inspect Git diff

{{"action":"git_diff","args":{{}}}}


8. Inspect Git status

{{"action":"git_status","args":{{}}}}


9. Finish

If source code was modified successfully:

{{"action":"finish","status":"changes_complete","summary":"concise summary of the completed fix","findings":[]}}

If no source code was modified and the project is healthy:

{{"action":"finish","status":"healthy","summary":"concise summary","findings":[]}}

If this was only a read-only investigation:

{{"action":"finish","status":"analysis_complete","summary":"concise summary","findings":[]}}

FINISH STATUS RULES

- If ANY successful source modification occurred in this run,
  status MUST be "changes_complete".
- "changes_complete" is a hard controller-validated success state:
  COMPLETE pytest must pass, git diff --check must pass,
  and final git diff + git status must be inspected.
- The current workspace may contain changes inherited from an earlier
  invocation. Never infer workspace health merely because this invocation
  has not yet performed replace_in_file.
- If no modification occurred and complete tests pass,
  "healthy" may be used.
- "analysis_complete" is only for a true read-only investigation
  with write permission disabled.
- Never use "analysis_complete" after replace_in_file succeeds.

STRICT MODIFICATION RULES

- Never modify a file before read_file.
- Use only exact minimal replacements.
- Never invent old_text.
- If replace_in_file is rejected because old_text or expected_count
  does not match current content, do NOT repeat the same edit unchanged.
  The controller releases that file's read lock; re-read the file first,
  then rebuild the smallest exact replacement from CURRENT content.
- Never hard-code test answers.
- Never modify tests merely to make them pass.
- Never perform unrelated formatting.
- Never make comment-only or whitespace-only changes
  during a normal bug-fix task.
- There is no write_file action.
- There is no delete action.
- There is no shell action.


SEARCH / INVESTIGATION DISCIPLINE

- The controller rejects an identical search_code query + file_pattern
  repeated within the same debugging generation.
- Do not keep searching after the failing test and the relevant current
  source already identify a minimal, justified fix.
- A successful source modification followed by COMPLETE pytest starts a
  new debugging generation and resets the duplicate-search guard.
- In writable test/debug tasks, the controller also enforces a HARD
  investigation budget. After enough searching/no-progress, SEARCH PHASE
  CLOSED is entered and further search_code actions are rejected until a
  successful source edit is followed by COMPLETE pytest.
- When SEARCH PHASE CLOSED appears, do not invent a new search phrase to
  evade the gate. Use CURRENT test + source evidence for the smallest safe
  replace_in_file, or finish with a non-success status if no edit is justified.
- Prefer action over redundant investigation once the evidence is sufficient.


PERMISSION RULES

- If allow_write=False, this is a read-only invocation.
- If allow_run=False, do not request run_tests.
- The Python controller enforces these permissions.
- Attempting a forbidden action will be rejected.


CRITICAL CHANGE-TEST DISCIPLINE

After EACH successful replace_in_file:

1. Immediately run COMPLETE pytest:
   {{"action":"run_tests","args":{{"target":""}}}}

2. Until complete pytest finishes,
   another source modification is forbidden.

3. If pytest still fails:
   - a new debugging generation begins;
   - previously read source files may be read again if needed;
   - re-read the relevant CURRENT file before the next edit;
   - investigate the remaining failure and make only the next
     necessary change.

4. If pytest passes:
   - run git_diff_check
   - inspect git_diff
   - inspect git_status
   - then finish

Never perform a second source edit merely to improve
comments, wording, formatting, or style after the
functional fix succeeds.


VALIDATION TAIL RESERVE

- The normal max_rounds budget is for investigation and repair.
- If that base budget expires AFTER a successful source edit, the
  controller may grant a small validation-only tail.
- The tail is NOT extra coding time. It permits only the single
  validation action currently required by the controller:
  COMPLETE pytest -> git_diff_check -> git_diff -> git_status -> finish.
- Never search, read, list, or modify files in validation tail.
- If post-change COMPLETE pytest fails, the tail stops and the run
  must remain INCOMPLETE rather than receiving extra repair rounds.


EVIDENCE LOCK

Every source-code finding must:
- reference a file read in this run;
- include evidence from CURRENT file content.

The Python controller verifies evidence against
the real current file.

The Python controller, not you, decides whether
the task passes.

INFRASTRUCTURE TELEMETRY

- The controller records request size, approximate context tokens,
  message count, streaming transport, time to headers, time to first
  chunk, and total inference latency for diagnostics.
- Telemetry does not change the task, permissions, or validation rules.

EXACT-MATCH GUARD

- A CONTROLLER_REQUEST_NONCE may appear at the very end of a round
  request. It is transport/runtime metadata only.
- Ignore the nonce when deciding the coding action and never copy it
  into file contents, commands, paths, searches, or final reports.
""".strip()


# ============================================================
# JSON Coding Worker
# ============================================================

def run_json_worker(
    task: str,
    workspace_path: str,
    max_rounds: int = MAX_ROUNDS,
    allow_write: bool = False,
    allow_run: bool = False,
) -> str:
    """
    Local Qwen JSON Coding Worker v0.3.13

    Hard permissions:
    - allow_write=False blocks source modifications.
    - allow_run=False blocks pytest/project execution.

    Safety rule:
    allow_write=True requires allow_run=True because
    every modification must pass complete pytest validation.
    """

    workspace = (
        Path(
            workspace_path
        )
        .expanduser()
        .resolve()
    )

    if not workspace.exists():

        return (
            "CONFIG_ERROR: "
            f"workspace does not exist: "
            f"{workspace}"
        )

    if not workspace.is_dir():

        return (
            "CONFIG_ERROR: "
            f"workspace is not a directory: "
            f"{workspace}"
        )

    # ========================================================
    # Permission compatibility
    # ========================================================

    if (
        allow_write
        and not allow_run
    ):

        return (
            "CONFIG_ERROR: "
            "allow_write=True requires allow_run=True. "
            "Source modifications are not permitted unless "
            "the worker can perform mandatory complete "
            "pytest validation afterward."
        )

    tools = WorkspaceTools(
        workspace
    )

    state = WorkerState(
        workspace=workspace,
        allow_write=bool(
            allow_write
        ),
        allow_run=bool(
            allow_run
        ),
    )

    max_rounds = max(
        1,
        min(
            int(
                max_rounds
            ),
            30,
        ),
    )

    system_prompt = (
        build_system_prompt(
            allow_write=state.allow_write,
            allow_run=state.allow_run,
        )
    )

    messages = [
        {
            "role":
                "system",

            "content":
                system_prompt,
        },

        {
            "role":
                "user",

            "content": (
                f"TASK:\n"
                f"{task}\n\n"
                "Begin now.\n"
                "/no_think"
            ),
        },
    ]

    trace = []

    # Per-round infrastructure telemetry. This records request
    # growth and inference latency without changing behavior.
    request_telemetry = []

    total_start = (
        time.perf_counter()
    )

    # ========================================================
    # Agent Loop
    # ========================================================

    for round_number in range(
        1,
        (
            max_rounds
            + VALIDATION_TAIL_MAX_ROUNDS
            + 1
        ),
    ):

        in_validation_tail = (
            round_number
            > max_rounds
        )

        validation_tail_round = (
            round_number
            - max_rounds
            if in_validation_tail
            else 0
        )

        if in_validation_tail:

            if not validation_tail_needed(
                state
            ):

                break

            print(
                "\n"
                + "!" * 16
                + (
                    f" Validation Tail "
                    f"{validation_tail_round}/"
                    f"{VALIDATION_TAIL_MAX_ROUNDS} "
                )
                + "!" * 16
            )

        print(
            "\n"
            + "=" * 16
            + f" Round {round_number} "
            + "=" * 16
        )

        guidance_round_limit = (
            (
                max_rounds
                + VALIDATION_TAIL_MAX_ROUNDS
            )
            if in_validation_tail
            else max_rounds
        )

        guidance = (
            build_round_guidance(
                task,
                round_number,
                guidance_round_limit,
                state,
            )
        )

        if in_validation_tail:

            expected_tail_action = (
                validation_tail_expected_action(
                    state
                )
            )

            guidance += (
                "\n\n"
                "CONTROLLER VALIDATION TAIL ACTIVE:\n"
                "The normal coding round budget is exhausted. "
                "This is validation-only time; no investigation "
                "or source modification is permitted.\n"
                f"Validation tail round: "
                f"{validation_tail_round}/"
                f"{VALIDATION_TAIL_MAX_ROUNDS}.\n"
                f"The ONLY valid next action is: "
                f"{expected_tail_action}.\n"
                "Required sequence after a successful edit is: "
                "COMPLETE pytest -> git_diff_check -> git_diff -> "
                "git_status -> finish."
            )

        request_nonce = (
            new_controller_request_nonce()
        )

        messages.append(
            {
                "role":
                    "user",

                "content": (
                    guidance
                    + "\n\n"
                    "Return your next JSON action."
                    + "\n/no_think"
                    + "\nCONTROLLER_REQUEST_NONCE="
                    + request_nonce
                ),
            }
        )

        # ----------------------------------------------------
        # Infrastructure telemetry
        # ----------------------------------------------------

        current_request_chars = (
            request_context_chars(
                messages
            )
        )

        current_estimated_tokens = (
            estimate_context_tokens(
                current_request_chars
            )
        )

        current_message_count = len(
            messages
        )

        print(
            "\nRequest telemetry:"
        )

        print(
            f"- round: "
            f"{round_number}"
        )

        print(
            f"- messages: "
            f"{current_message_count}"
        )

        print(
            f"- context chars: "
            f"{current_request_chars}"
        )

        print(
            f"- estimated tokens: "
            f"~{current_estimated_tokens}"
        )

        print(
            f"- request nonce: "
            f"{request_nonce}"
        )

        print(
            f"- phase: "
            f"{'validation_tail' if in_validation_tail else 'base'}"
        )

        if in_validation_tail:

            print(
                f"- validation tail round: "
                f"{validation_tail_round}/"
                f"{VALIDATION_TAIL_MAX_ROUNDS}"
            )

        started = (
            time.perf_counter()
        )

        transport_meta = {
            "transport":
                "stream",
        }

        try:

            raw = ask_qwen(
                messages,
                transport_meta=transport_meta,
            )

        except requests.Timeout:

            elapsed = (
                time.perf_counter()
                - started
            )

            transport_meta.setdefault(
                "total_inference_time_seconds",
                round(
                    elapsed,
                    3,
                ),
            )

            request_telemetry.append(
                {
                    "round":
                        round_number,

                    "message_count":
                        current_message_count,

                    "request_context_chars":
                        current_request_chars,

                    "estimated_context_tokens":
                        current_estimated_tokens,

                    "request_nonce":
                        request_nonce,

                    "phase":
                        (
                            "validation_tail"
                            if in_validation_tail
                            else "base"
                        ),

                    "validation_tail_round":
                        (
                            validation_tail_round
                            if in_validation_tail
                            else None
                        ),

                    "transport":
                        transport_meta.get(
                            "transport",
                            "stream",
                        ),

                    "time_to_headers_seconds":
                        transport_meta.get(
                            "time_to_headers_seconds"
                        ),

                    "time_to_first_chunk_seconds":
                        transport_meta.get(
                            "time_to_first_chunk_seconds"
                        ),

                    "total_inference_time_seconds":
                        transport_meta.get(
                            "total_inference_time_seconds"
                        ),

                    "inference_time_seconds":
                        round(
                            elapsed,
                            3,
                        ),

                    "result":
                        "timeout",
                }
            )

            total_elapsed = (
                time.perf_counter()
                - total_start
            )

            return build_infra_error_payload(
                reason=(
                    "LM Studio streaming request timed out."
                ),
                failed_round=round_number,
                request_chars=current_request_chars,
                estimated_tokens=current_estimated_tokens,
                message_count=current_message_count,
                state=state,
                trace=trace,
                request_telemetry=request_telemetry,
                total_elapsed=total_elapsed,
                transport_meta=transport_meta,
            )

        except requests.RequestException as exc:

            elapsed = (
                time.perf_counter()
                - started
            )

            transport_meta.setdefault(
                "total_inference_time_seconds",
                round(
                    elapsed,
                    3,
                ),
            )

            request_telemetry.append(
                {
                    "round":
                        round_number,

                    "message_count":
                        current_message_count,

                    "request_context_chars":
                        current_request_chars,

                    "estimated_context_tokens":
                        current_estimated_tokens,

                    "request_nonce":
                        request_nonce,

                    "phase":
                        (
                            "validation_tail"
                            if in_validation_tail
                            else "base"
                        ),

                    "validation_tail_round":
                        (
                            validation_tail_round
                            if in_validation_tail
                            else None
                        ),

                    "transport":
                        transport_meta.get(
                            "transport",
                            "stream",
                        ),

                    "time_to_headers_seconds":
                        transport_meta.get(
                            "time_to_headers_seconds"
                        ),

                    "time_to_first_chunk_seconds":
                        transport_meta.get(
                            "time_to_first_chunk_seconds"
                        ),

                    "total_inference_time_seconds":
                        transport_meta.get(
                            "total_inference_time_seconds"
                        ),

                    "inference_time_seconds":
                        round(
                            elapsed,
                            3,
                        ),

                    "result":
                        (
                            "request_exception:"
                            f"{type(exc).__name__}"
                        ),
                }
            )

            total_elapsed = (
                time.perf_counter()
                - total_start
            )

            return build_infra_error_payload(
                reason=(
                    f"{type(exc).__name__}: "
                    f"{exc}"
                ),
                failed_round=round_number,
                request_chars=current_request_chars,
                estimated_tokens=current_estimated_tokens,
                message_count=current_message_count,
                state=state,
                trace=trace,
                request_telemetry=request_telemetry,
                total_elapsed=total_elapsed,
                transport_meta=transport_meta,
            )

        elapsed = (
            time.perf_counter()
            - started
        )

        transport_meta.setdefault(
            "total_inference_time_seconds",
            round(
                elapsed,
                3,
            ),
        )

        request_telemetry.append(
            {
                "round":
                    round_number,

                "message_count":
                    current_message_count,

                "request_context_chars":
                    current_request_chars,

                "estimated_context_tokens":
                    current_estimated_tokens,

                "request_nonce":
                    request_nonce,

                "phase":
                    (
                        "validation_tail"
                        if in_validation_tail
                        else "base"
                    ),

                "validation_tail_round":
                    (
                        validation_tail_round
                        if in_validation_tail
                        else None
                    ),

                "transport":
                    transport_meta.get(
                        "transport",
                        "stream",
                    ),

                "time_to_headers_seconds":
                    transport_meta.get(
                        "time_to_headers_seconds"
                    ),

                "time_to_first_chunk_seconds":
                    transport_meta.get(
                        "time_to_first_chunk_seconds"
                    ),

                "total_inference_time_seconds":
                    transport_meta.get(
                        "total_inference_time_seconds"
                    ),

                "inference_time_seconds":
                    round(
                        elapsed,
                        3,
                    ),

                "result":
                    "ok",
            }
        )

        print(
            f"\nTransport: "
            f"{transport_meta.get('transport', 'stream')}"
        )

        print(
            f"Time to headers: "
            f"{transport_meta.get('time_to_headers_seconds')}s"
        )

        print(
            f"Time to first chunk: "
            f"{transport_meta.get('time_to_first_chunk_seconds')}s"
        )

        print(
            f"Total inference time: "
            f"{transport_meta.get('total_inference_time_seconds')}s"
        )

        print(
            "\nQwen raw output:"
        )

        print(
            raw
        )

        messages.append(
            {
                "role":
                    "assistant",

                "content":
                    raw,
            }
        )

        # ----------------------------------------------------
        # Parse JSON
        # ----------------------------------------------------

        try:

            action_data = (
                extract_json(
                    raw
                )
            )

        except Exception as exc:

            result = (
                "JSON_PARSE_ERROR: "
                f"{type(exc).__name__}: "
                f"{exc}"
            )

            trace.append(
                {
                    "round":
                        round_number,

                    "action":
                        "json_parse_error",

                    "action_data":
                        None,

                    "controller_result":
                        result,

                    "message_count":
                        current_message_count,

                    "request_context_chars":
                        current_request_chars,

                    "estimated_context_tokens":
                        current_estimated_tokens,

                    "request_nonce":
                        request_nonce,

                    "phase":
                        (
                            "validation_tail"
                            if in_validation_tail
                            else "base"
                        ),

                    "validation_tail_round":
                        (
                            validation_tail_round
                            if in_validation_tail
                            else None
                        ),

                    "inference_time_seconds":
                        round(
                            elapsed,
                            3,
                        ),
                }
            )

            messages.append(
                {
                    "role":
                        "user",

                    "content": (
                        "CONTROLLER ERROR:\n"
                        f"{result}\n\n"
                        "Return exactly one valid "
                        "JSON action."
                        "\n/no_think"
                    ),
                }
            )

            continue

        action = action_data.get(
            "action"
        )

        print(
            "\nParsed action:"
        )

        print(
            json.dumps(
                action_data,
                ensure_ascii=False,
                indent=2,
            )
        )

        # ----------------------------------------------------
        # Execute Action
        # ----------------------------------------------------

        try:

            if in_validation_tail:

                tail_action_ok, tail_gate_result = (
                    validate_validation_tail_action(
                        action_data,
                        state,
                    )
                )

                if not tail_action_ok:

                    finished = False
                    result = tail_gate_result

                else:

                    finished, result = (
                        execute_action(
                            tools,
                            action_data,
                            state,
                            task,
                        )
                    )

            else:

                modification_count_before = (
                    state.modification_count
                )

                finished, result = (
                    execute_action(
                        tools,
                        action_data,
                        state,
                        task,
                    )
                )

                successful_source_change = (
                    action == "replace_in_file"
                    and not finished
                    and state.modification_count
                    == modification_count_before + 1
                    and state.change_needs_test
                )

                if successful_source_change:

                    finished, result = (
                        run_controller_post_change_validation(
                            tools,
                            state,
                            task,
                            result,
                        )
                    )

        except Exception as exc:

            finished = False

            result = (
                "CONTROLLER_EXECUTION_ERROR: "
                f"{type(exc).__name__}: "
                f"{exc}"
            )

        trace.append(
            {
                "round":
                    round_number,

                "action":
                    action,

                "action_data":
                    action_data,

                "controller_result":
                    result,

                "message_count":
                    current_message_count,

                "request_context_chars":
                    current_request_chars,

                "estimated_context_tokens":
                    current_estimated_tokens,

                "request_nonce":
                    request_nonce,

                "phase":
                    (
                        "validation_tail"
                        if in_validation_tail
                        else "base"
                    ),

                "validation_tail_round":
                    (
                        validation_tail_round
                        if in_validation_tail
                        else None
                    ),

                "inference_time_seconds":
                    round(
                        elapsed,
                        3,
                    ),
            }
        )
        # ----------------------------------------------------
        # Finish
        # ----------------------------------------------------

        if finished:

            total_elapsed = (
                time.perf_counter()
                - total_start
            )

            print(
                "\n"
                + "=" * 50
            )

            print(
                "JSON Worker SUCCESS"
            )

            print(
                "Controller validation: PASS"
            )

            print(
                "=" * 50
            )

            print(
                "\nFinal report:"
            )

            print(
                result
            )

            print(
                "\nAction trace:"
            )

            for item in trace:

                print(
                    f"- Round "
                    f"{item['round']}: "
                    f"{item['action']}"
                )

            print(
                f"\nTotal worker time: "
                f"{total_elapsed:.2f}s"
            )

            try:

                final_payload = json.loads(
                    result
                )

                if isinstance(
                    final_payload,
                    dict,
                ):

                    final_payload[
                        "action_trace"
                    ] = [
                        {
                            "round": item["round"],
                            "action": item["action"],
                        }
                        for item in trace
                    ]

                    final_payload[
                        "request_telemetry"
                    ] = request_telemetry

                    final_payload[
                        "total_worker_time_seconds"
                    ] = round(
                        total_elapsed,
                        2,
                    )

                    result = json.dumps(
                        final_payload,
                        ensure_ascii=False,
                        indent=2,
                    )

            except Exception:

                pass

            return result

        # ----------------------------------------------------
        # Controller result → Qwen
        # ----------------------------------------------------

        print(
            "\nController result:"
        )

        print(
            result
        )

        messages.append(
            {
                "role":
                    "user",

                "content": (
                    "CONTROLLER RESULT:\n"
                    f"{truncate_result(result)}"
                    "\n\n"
                    "This result is authoritative. "
                    "Choose your next JSON action."
                    "\n/no_think"
                ),
            }
        )

    # ========================================================
    # Forced finalization
    # ========================================================

    summary = (
        force_final_summary(
            messages,
            state,
            task,
        )
    )

    total_elapsed = (
        time.perf_counter()
        - total_start
    )

    if summary:

        print(
            "\n"
            + "=" * 50
        )

        print(
            "JSON Worker SUCCESS"
        )

        print(
            "Controller validation: PASS"
        )

        print(
            "(forced finalization)"
        )

        print(
            "=" * 50
        )

        print(
            summary
        )

        print(
            f"\nTotal worker time: "
            f"{total_elapsed:.2f}s"
        )

        try:

            final_payload = json.loads(
                summary
            )

            if isinstance(
                final_payload,
                dict,
            ):

                final_payload[
                    "action_trace"
                ] = [
                    {
                        "round": item["round"],
                        "action": item["action"],
                    }
                    for item in trace
                ]

                final_payload[
                    "request_telemetry"
                ] = request_telemetry

                final_payload[
                    "total_worker_time_seconds"
                ] = round(
                    total_elapsed,
                    2,
                )

                summary = json.dumps(
                    final_payload,
                    ensure_ascii=False,
                    indent=2,
                )

        except Exception:

            pass

        return summary

        # ========================================================
    # Incomplete
    # ========================================================

    print(
        "\n"
        + "=" * 50
    )

    print(
        "JSON Worker INCOMPLETE"
    )

    print(
        "Controller validation: FAIL"
    )

    print(
        "=" * 50
    )

    print(
        "\nAction trace:"
    )

    for item in trace:

        print(
            f"- Round "
            f"{item['round']}: "
            f"{item['action']}"
        )

    total_elapsed = (
        time.perf_counter()
        - total_start
    )

    print(
        f"\nTotal worker time: "
        f"{total_elapsed:.2f}s"
    )

    # --------------------------------------------------------
    # Return detailed black-box report to MCP caller
    # --------------------------------------------------------

    incomplete_payload = {
        "status": "INCOMPLETE",

        "reason": (
            "validation requirements "
            "were not satisfied"
        ),

        "allow_write":
            state.allow_write,

        "allow_run":
            state.allow_run,

        "modified_files":
            sorted(
                state.modified_files
            ),

        "modification_count":
            state.modification_count,

        "full_tests_run":
            state.full_tests_run,

        "full_tests_passed":
            state.full_tests_passed,

        "git_diff_check_seen":
            state.git_diff_check_seen,

        "git_diff_check_passed":
            state.git_diff_check_passed,

        "git_diff_checked":
            state.git_diff_seen,

        "git_status_checked":
            state.git_status_seen,

        "validation_tail_max_rounds":
            VALIDATION_TAIL_MAX_ROUNDS,

        "validation_tail_still_needed":
            validation_tail_needed(
                state
            ),

        "validation_tail_expected_action":
            validation_tail_expected_action(
                state
            ),

        "request_telemetry":
            request_telemetry,

        "action_trace":
            trace,
    }

    return json.dumps(
        incomplete_payload,
        ensure_ascii=False,
        indent=2,
    )


# ============================================================
# Direct Test
# ============================================================
# ============================================================
# Direct Test
# ============================================================

if __name__ == "__main__":

    print(
        "=" * 50
    )

    print(
        "Local Qwen JSON Coding Worker v0.3.13"
    )

    print(
        "=" * 50
    )

    workspace_path = input(
        "\nWorkspace path: "
    ).strip()

    if not workspace_path:

        workspace_path = (
            r"D:\Projects\qwen-worker-test"
        )

    task = input(
        "\nTask: "
    ).strip()

    if not task:

        task = (
            "Inspect the current project and determine "
            "whether the pytest suite is healthy. "
            "If write permission is enabled and a real "
            "source-code bug exists, make only the minimum "
            "necessary fix. "
            "Never modify tests merely to make them pass."
        )

    # --------------------------------------------------------
    # Permission selection
    # --------------------------------------------------------

    write_answer = input(
        "\nAllow write? [y/N]: "
    ).strip().lower()

    allow_write = (
        write_answer
        in {
            "y",
            "yes",
        }
    )

    run_answer = input(
        "\nAllow run/tests? [Y/n]: "
    ).strip().lower()

    allow_run = (
        run_answer
        not in {
            "n",
            "no",
        }
    )

    print(
        "\n"
        f"Selected permissions: "
        f"allow_write={allow_write}, "
        f"allow_run={allow_run}"
    )

    result = run_json_worker(
        task=task,
        workspace_path=workspace_path,
        allow_write=allow_write,
        allow_run=allow_run,
    )

    print(
        "\nWorker returned:"
    )

    print(
        result
    )