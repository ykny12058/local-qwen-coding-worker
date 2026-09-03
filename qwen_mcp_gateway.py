import json
import traceback

from mcp.server import MCPServer

from json_coding_worker import (
    MAX_ROUNDS as JSON_WORKER_DEFAULT_ROUNDS,
    run_json_worker,
)

from qwen_mcp_server import (
    call_qwen,
    resolve_workspace,
)

from worker_tools import WorkspaceTools


# ============================================================
# MCP Server
# ============================================================

mcp = MCPServer(
    "Local Qwen Gateway"
)


# ============================================================
# Helpers
# ============================================================

def normalize_mode(
    mode: str,
) -> str:
    """
    Normalize legacy coding mode names.

    The JSON Worker does not use LM Studio native
    function calling.

    mode only controls round budgets.
    """

    mode = (
        str(mode)
        .lower()
        .strip()
    )

    if mode not in {
        "fast",
        "worker",
        "reasoning",
    }:
        mode = "worker"

    return mode


def resolve_round_budget(
    mode: str,
    max_rounds: int,
) -> int:
    """
    Resolve JSON Worker round budget.
    """

    mode = normalize_mode(
        mode
    )

    try:
        max_rounds = int(
            max_rounds
        )

    except (
        TypeError,
        ValueError,
    ):
        max_rounds = 0

    if max_rounds > 0:

        return max(
            1,
            min(
                max_rounds,
                30,
            ),
        )

    if mode == "fast":
        return 8

    if mode == "reasoning":
        return 24

    return (
        JSON_WORKER_DEFAULT_ROUNDS
    )


def clamp_rounds(
    value: int,
    default: int,
) -> int:
    """
    Clamp Auto Router stage round limits.
    """

    try:
        value = int(
            value
        )

    except (
        TypeError,
        ValueError,
    ):
        value = 0

    if value <= 0:
        value = default

    return max(
        1,
        min(
            value,
            30,
        ),
    )

# ============================================================
# Deterministic Pytest Triage
# ============================================================

def run_pytest_triage(
    workspace,
) -> tuple[str, str]:
    """
    Run one deterministic complete pytest probe.

    Returns:

    ("healthy", result)
    ("tests_failed", result)
    ("INFRA_ERROR", result)

    No LLM is used during this triage step.
    """

    try:

        tools = WorkspaceTools(
            workspace
        )

        result = tools.run_tests(
            target=""
        )

    except Exception as exc:

        return (
            "INFRA_ERROR",
            (
                "PYTEST TRIAGE INFRA_ERROR\n"
                f"Exception: {type(exc).__name__}\n"
                f"Message: {exc}"
            ),
        )

    if (
        "EXIT CODE: 0"
        in result
    ):

        return (
            "healthy",
            result,
        )

    return (
        "tests_failed",
        result,
    )


def build_pytest_triage_payload(
    status: str,
) -> str:
    """
    Build a compact structured report for the router.
    """

    if status == "healthy":

        summary = (
            "Complete pytest passed. "
            "No failing tests currently require repair."
        )

        passed = True

    elif status == "tests_failed":

        summary = (
            "Complete pytest failed. "
            "Escalation is required for diagnosis."
        )

        passed = False

    else:

        summary = (
            "The deterministic pytest probe "
            "could not complete."
        )

        passed = False

    payload = {
        "status": status,
        "summary": summary,
        "findings": [],
        "full_tests_passed": passed,
        "controller_validation": (
            "PASS"
            if status in {
                "healthy",
                "tests_failed",
            }
            else "FAIL"
        ),
        "triage_backend": (
            "deterministic_pytest"
        ),
    }

    return json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
    )

def parse_worker_payload(
    result: str,
) -> dict | None:
    """
    Parse a successful JSON Worker final report.

    Returns None for:
    - INCOMPLETE
    - INFRA_ERROR
    - CONFIG_ERROR
    - malformed/non-JSON results
    """

    try:

        data = json.loads(
            result
        )

    except (
        json.JSONDecodeError,
        TypeError,
    ):

        return None

    if not isinstance(
        data,
        dict,
    ):
        return None

    return data


def worker_state_label(
    result: str,
) -> str:
    """
    Produce a compact stage state for Auto Router reports.
    """

    if result.startswith(
        "INFRA_ERROR:"
    ):
        return "INFRA_ERROR"

    if result.startswith(
        "CONFIG_ERROR:"
    ):
        return "CONFIG_ERROR"

    if result.startswith(
        "INCOMPLETE:"
    ):
        return "INCOMPLETE"

    payload = (
        parse_worker_payload(
            result
        )
    )

    if payload is None:
        return "UNKNOWN"

    if payload.get("status") == "INCOMPLETE":
        return "INCOMPLETE"

    if (
        payload.get(
            "controller_validation"
        )
        == "PASS"
    ):

        return str(
            payload.get(
                "status",
                "SUCCESS",
            )
        )

    return "VALIDATION_FAILED"


def build_auto_report(
    route: str,
    triage_result: str,
    final_result: str | None = None,
) -> str:
    """
    Build a stable MCP-facing Auto Router report.
    """

    triage_state = (
        worker_state_label(
            triage_result
        )
    )

    lines = [
        "=== LOCAL QWEN AUTO ROUTER 2.0 ===",
        f"Route: {route}",
        f"Triage stage: {triage_state}",
    ]

    if final_result is not None:

        final_state = (
            worker_state_label(
                final_result
            )
        )

        lines.append(
            f"Final stage: {final_state}"
        )

    lines.extend(
        [
            "",
            "=== Triage result ===",
            triage_result,
        ]
    )

    if final_result is not None:

        lines.extend(
            [
                "",
                "=== Final stage result ===",
                final_result,
            ]
        )

    return "\n".join(
        lines
    )


# ============================================================
# Normal Qwen Task
# ============================================================

@mcp.tool()
def delegate_to_qwen(
    task: str,
    mode: str = "worker",
) -> str:
    """
    Delegate a normal non-coding task to local Qwen.

    mode: fast, worker, or reasoning.
    """

    try:

        return call_qwen(
            task=task,
            mode=mode,
        )

    except Exception as exc:

        return (
            "=== delegate_to_qwen INTERNAL ERROR ===\n"
            f"Exception: {type(exc).__name__}\n"
            f"Message: {exc}\n\n"
            "=== Traceback ===\n"
            f"{traceback.format_exc()}"
        )


# ============================================================
# Single-stage JSON Coding Worker
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
    Delegate one coding task to the JSON Action Worker.

    Does not use LM Studio native function calling.

    allow_write:
    Permit exact source-code replacements.

    allow_run:
    Permit pytest/project test execution.

    mode:
    fast, worker, or reasoning.

    max_rounds:
    0 uses the mode default.
    """

    try:

        workspace = resolve_workspace(
            workspace_path
        )

        normalized_mode = (
            normalize_mode(
                mode
            )
        )

        rounds = (
            resolve_round_budget(
                mode=normalized_mode,
                max_rounds=max_rounds,
            )
        )

        result = run_json_worker(
            task=task,
            workspace_path=str(
                workspace
            ),
            max_rounds=rounds,
            allow_write=bool(
                allow_write
            ),
            allow_run=bool(
                allow_run
            ),
        )

        return (
            "=== LOCAL QWEN JSON CODING WORKER ===\n"
            "Backend: json_coding_worker v0.3.12\n"
            f"Mode: {normalized_mode}\n"
            f"Max rounds: {rounds}\n"
            f"allow_write: {bool(allow_write)}\n"
            f"allow_run: {bool(allow_run)}\n\n"
            f"{result}"
        )

    except Exception as exc:

        return (
            "=== delegate_coding_task INTERNAL ERROR ===\n"
            f"Exception: {type(exc).__name__}\n"
            f"Message: {exc}\n\n"
            "=== Traceback ===\n"
            f"{traceback.format_exc()}"
        )


# ============================================================
# Auto Router 2.0
# ============================================================

@mcp.tool()
def delegate_coding_task_auto(
    task: str,
    workspace_path: str = "",
    allow_write: bool = False,
    allow_run: bool = False,
    worker_rounds: int = 4,
    reasoning_rounds: int = 18,
) -> str:
    """
    Auto Router 2.2.

    When allow_run=True:
        Stage 1 is a deterministic complete pytest probe.

        No Qwen inference is used for routing.

    When allow_run=False:
        A short read-only JSON Worker triage is used.

    If repair is required:
        Escalate to the full JSON Coding Worker.

    LM Studio native function calling is never used.
    """

    try:

        # ====================================================
        # Workspace
        # ====================================================

        workspace = resolve_workspace(
            workspace_path
        )

        # ====================================================
        # Permission compatibility
        # ====================================================

        if (
            allow_write
            and not allow_run
        ):

            return (
                "=== LOCAL QWEN AUTO ROUTER 2.2 ===\n"
                "Route: CONFIG_ERROR\n\n"
                "allow_write=True requires allow_run=True. "
                "Source modification requires mandatory "
                "post-change pytest validation."
            )

        triage_rounds = (
            clamp_rounds(
                worker_rounds,
                4,
            )
        )

        full_rounds = (
            clamp_rounds(
                reasoning_rounds,
                18,
            )
        )

        # ====================================================
        # ROUTE A:
        # allow_run=True
        #
        # Deterministic pytest routing.
        # No LLM triage.
        # ====================================================

        if allow_run:

            triage_status, pytest_result = (
                run_pytest_triage(
                    workspace
                )
            )

            triage_result = (
                build_pytest_triage_payload(
                    triage_status
                )
            )

            # ------------------------------------------------
            # Infrastructure failure
            # ------------------------------------------------

            if triage_status == "INFRA_ERROR":

                return (
                    build_auto_report(
                        route=(
                            "pytest_probe "
                            "-> INFRA_ERROR"
                        ),
                        triage_result=(
                            pytest_result
                        ),
                    )
                )

            # ------------------------------------------------
            # Healthy
            # ------------------------------------------------

            if triage_status == "healthy":

                return (
                    build_auto_report(
                        route=(
                            "pytest_probe -> healthy"
                        ),
                        triage_result=(
                            triage_result
                        ),
                    )
                )

            # ------------------------------------------------
            # Tests failed + read-only caller
            # ------------------------------------------------

            if not allow_write:

                # Give Qwen a larger read-only investigation.
                read_only_task = (
                    f"{task}\n\n"

                    "AUTO ROUTER EXTENDED READ-ONLY "
                    "INVESTIGATION.\n\n"

                    "The router has already run the complete "
                    "pytest suite and confirmed that tests fail.\n\n"

                    "PYTEST FAILURE OUTPUT:\n"
                    f"{pytest_result[-12000:]}\n\n"

                    "Investigate the root cause using only "
                    "read-only actions. "
                    "Do not modify source files. "
                    "Use the existing pytest failure output "
                    "as authoritative evidence. "
                    "Finish with an evidence-backed report."
                )

                final_result = run_json_worker(
                    task=read_only_task,
                    workspace_path=str(
                        workspace
                    ),
                    max_rounds=full_rounds,
                    allow_write=False,
                    allow_run=True,
                )

                return (
                    build_auto_report(
                        route=(
                            "pytest_probe "
                            "-> extended_read_only"
                        ),
                        triage_result=(
                            triage_result
                        ),
                        final_result=(
                            final_result
                        ),
                    )
                )

            # ------------------------------------------------
            # Tests failed + write-enabled caller
            # ------------------------------------------------

            full_worker_task = (
                f"{task}\n\n"

                "AUTO ROUTER FULL WORKER STAGE.\n\n"

                "The router has already run the COMPLETE "
                "pytest suite and confirmed that it fails.\n\n"

                "PYTEST FAILURE OUTPUT:\n"
                f"{pytest_result[-12000:]}\n\n"

                "Use this failure output as routing evidence. "
                "Verify the relevant source code against the "
                "CURRENT workspace before modifying anything.\n\n"

                "Locate the root cause and make only the "
                "minimum necessary source-code change. "
                "Do not modify tests merely to make them pass.\n\n"

                "All normal JSON Worker validation gates "
                "remain mandatory after modification:\n"
                "- complete pytest\n"
                "- git diff --check\n"
                "- git diff\n"
                "- git status\n"
                "- controller validation"
            )

            final_result = run_json_worker(
                task=full_worker_task,
                workspace_path=str(
                    workspace
                ),
                max_rounds=full_rounds,
                allow_write=True,
                allow_run=True,
            )

            return (
                build_auto_report(
                    route=(
                        "pytest_probe -> full_worker"
                    ),
                    triage_result=(
                        triage_result
                    ),
                    final_result=(
                        final_result
                    ),
                )
            )

        # ====================================================
        # ROUTE B:
        # allow_run=False
        #
        # Cannot use pytest.
        # Keep a short read-only Qwen triage.
        # ====================================================

        triage_task = (
            "AUTO ROUTER READ-ONLY TRIAGE.\n\n"
            f"Original task:\n{task}\n\n"

            "Execution is disabled. "
            "Perform a focused read-only investigation. "
            "Do not modify files. "
            "Do not request run_tests. "
            "Avoid broad unnecessary investigation. "
            "Finish as soon as enough evidence exists."
        )

        triage_result = run_json_worker(
            task=triage_task,
            workspace_path=str(
                workspace
            ),
            max_rounds=triage_rounds,
            allow_write=False,
            allow_run=False,
        )

        triage_state = (
            worker_state_label(
                triage_result
            )
        )

        triage_payload = (
            parse_worker_payload(
                triage_result
            )
        )

        # ----------------------------------------------------
        # Infra/config
        # ----------------------------------------------------

        if triage_state in {
            "INFRA_ERROR",
            "CONFIG_ERROR",
        }:

            return (
                build_auto_report(
                    route=(
                        "read_only_triage -> "
                        f"{triage_state}"
                    ),
                    triage_result=(
                        triage_result
                    ),
                )
            )

        # ----------------------------------------------------
        # Completed read-only triage
        # ----------------------------------------------------

        if (
            triage_payload
            and triage_payload.get(
                "controller_validation"
            )
            == "PASS"
        ):

            return (
                build_auto_report(
                    route=(
                        "read_only_triage -> "
                        f"{triage_payload.get('status')}"
                    ),
                    triage_result=(
                        triage_result
                    ),
                )
            )

        # ----------------------------------------------------
        # Short read-only triage incomplete
        # ----------------------------------------------------

        extended_task = (
            f"{task}\n\n"

            "AUTO ROUTER EXTENDED READ-ONLY STAGE.\n"

            "The short read-only triage stage did not "
            "complete the investigation. "
            "Continue with the larger investigation budget. "
            "Remain read-only and finish with an "
            "evidence-backed report."
        )

        final_result = run_json_worker(
            task=extended_task,
            workspace_path=str(
                workspace
            ),
            max_rounds=full_rounds,
            allow_write=False,
            allow_run=False,
        )

        return (
            build_auto_report(
                route=(
                    "read_only_triage "
                    "-> extended_read_only"
                ),
                triage_result=(
                    triage_result
                ),
                final_result=(
                    final_result
                ),
            )
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
    
if __name__ == "__main__":

    mcp.run()