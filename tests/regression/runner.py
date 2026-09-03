from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


import json_coding_worker as worker_module
from json_coding_worker import run_json_worker
from worker_tools import WorkspaceTools


@dataclass
class ScenarioResult:
    name: str
    passed: bool
    detail: str


def run_command(
    args: list[str],
    cwd: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


def require_success(
    result: subprocess.CompletedProcess[str],
    step: str,
) -> None:
    if result.returncode == 0:
        return

    raise RuntimeError(
        f"{step} failed\n"
        f"returncode: {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


def cleanup_workspace(path: Path) -> bool:
    def remove_readonly(
        func,
        target,
        exc,
    ) -> None:
        try:
            os.chmod(
                target,
                stat.S_IWRITE,
            )
        except OSError:
            pass

        func(target)

    for _ in range(3):
        try:
            shutil.rmtree(
                path,
                onexc=remove_readonly,
            )
        except OSError:
            time.sleep(0.2)

        if not path.exists():
            return True

    return False


def init_git_repo(workspace: Path) -> None:
    result = run_command(
        ["git", "init", "-b", "main"],
        workspace,
    )
    require_success(
        result,
        "git init",
    )

    result = run_command(
        [
            "git",
            "config",
            "user.name",
            "Regression Harness",
        ],
        workspace,
    )
    require_success(
        result,
        "git config user.name",
    )

    result = run_command(
        [
            "git",
            "config",
            "user.email",
            "regression@example.invalid",
        ],
        workspace,
    )
    require_success(
        result,
        "git config user.email",
    )

    gitignore = workspace / ".gitignore"
    gitignore.write_text(
        "__pycache__/\n"
        "*.py[cod]\n"
        ".pytest_cache/\n",
        encoding="utf-8",
    )


def commit_baseline(workspace: Path) -> None:
    result = run_command(
        ["git", "add", "."],
        workspace,
    )
    require_success(
        result,
        "git add",
    )

    result = run_command(
        [
            "git",
            "commit",
            "-m",
            "test: establish regression fixture",
        ],
        workspace,
    )
    require_success(
        result,
        "git commit",
    )

    result = run_command(
        [
            "git",
            "status",
            "--porcelain",
        ],
        workspace,
    )
    require_success(
        result,
        "git status",
    )

    if result.stdout.strip():
        raise RuntimeError(
            "Temporary repository is not clean:\n"
            f"{result.stdout}"
        )


def create_healthy_fixture(
    workspace: Path,
) -> None:
    source_dir = workspace / "src"
    tests_dir = workspace / "tests"

    source_dir.mkdir(
        parents=True,
        exist_ok=True,
    )
    tests_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    validator = source_dir / "validator.py"
    validator.write_text(
        "def is_valid_quantity(quantity: int) -> bool:\n"
        "    return quantity > 0\n",
        encoding="utf-8",
    )

    test_file = tests_dir / "test_validator.py"
    test_file.write_text(
        "from src.validator import is_valid_quantity\n"
        "\n"
        "\n"
        "def test_positive_quantity_is_valid():\n"
        "    assert is_valid_quantity(1) is True\n"
        "\n"
        "\n"
        "def test_zero_quantity_is_invalid():\n"
        "    assert is_valid_quantity(0) is False\n"
        "\n"
        "\n"
        "def test_negative_quantity_is_invalid():\n"
        "    assert is_valid_quantity(-1) is False\n",
        encoding="utf-8",
    )


def get_git_status(workspace: Path) -> str:
    result = run_command(
        [
            "git",
            "status",
            "--porcelain",
        ],
        workspace,
    )
    require_success(
        result,
        "git status",
    )
    return result.stdout.strip()



def create_single_bug_fixture(
    workspace: Path,
) -> None:
    source_dir = workspace / "src"
    tests_dir = workspace / "tests"

    source_dir.mkdir(
        parents=True,
        exist_ok=True,
    )
    tests_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    validator = source_dir / "validator.py"
    validator.write_text(
        "def is_valid_quantity(quantity: int) -> bool:\n"
        "    return quantity >= 0\n",
        encoding="utf-8",
    )

    test_file = tests_dir / "test_validator.py"
    test_file.write_text(
        "from src.validator import is_valid_quantity\n"
        "\n"
        "\n"
        "def test_positive_quantity_is_valid():\n"
        "    assert is_valid_quantity(1) is True\n"
        "\n"
        "\n"
        "def test_zero_quantity_is_invalid():\n"
        "    assert is_valid_quantity(0) is False\n"
        "\n"
        "\n"
        "def test_negative_quantity_is_invalid():\n"
        "    assert is_valid_quantity(-1) is False\n",
        encoding="utf-8",
    )


def run_single_validator_bug() -> ScenarioResult:
    name = "single_validator_bug"

    temp_root = Path(
        tempfile.mkdtemp(
            prefix="qwen_regression_single_bug_"
        )
    ).resolve()

    print()
    print(
        f"=== Scenario: {name} ==="
    )
    print(
        f"Workspace: {temp_root}"
    )

    try:
        init_git_repo(
            temp_root
        )

        create_single_bug_fixture(
            temp_root
        )

        failing_test = run_command(
            [
                str(
                    Path(
                        os.sys.executable
                    )
                ),
                "-m",
                "pytest",
                "-q",
            ],
            temp_root,
        )

        if failing_test.returncode == 0:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Bug fixture unexpectedly passed pytest "
                    "before Worker execution."
                ),
            )

        print(
            "Injected bug confirmed by pytest: PASS"
        )

        commit_baseline(
            temp_root
        )

        worker_result = run_json_worker(
            task=(
                "Fix the failing quantity validation bug. "
                "Run the complete pytest suite, inspect the "
                "relevant source and tests, make only the "
                "minimal required source change, then complete "
                "all required validation and finish."
            ),
            workspace_path=str(
                temp_root
            ),
            max_rounds=6,
            allow_write=True,
            allow_run=True,
        )

        print()
        print(
            "--- Worker Result ---"
        )
        print(
            worker_result
        )

        validator = (
            temp_root
            / "src"
            / "validator.py"
        )

        final_source = validator.read_text(
            encoding="utf-8"
        )

        if "return quantity > 0" not in final_source:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Expected validator fix was not present."
                ),
            )

        if "return quantity >= 0" in final_source:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Buggy validator expression remained."
                ),
            )

        final_test = run_command(
            [
                str(
                    Path(
                        os.sys.executable
                    )
                ),
                "-m",
                "pytest",
                "-q",
            ],
            temp_root,
        )

        if final_test.returncode != 0:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Final pytest failed.\n"
                    f"stdout:\n"
                    f"{final_test.stdout}\n"
                    f"stderr:\n"
                    f"{final_test.stderr}"
                ),
            )

        diff_result = run_command(
            [
                "git",
                "diff",
                "--",
                "src/validator.py",
            ],
            temp_root,
        )
        require_success(
            diff_result,
            "git diff validator",
        )

        if "quantity >= 0" not in diff_result.stdout:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Expected old expression was not found "
                    "in git diff."
                ),
            )

        if "quantity > 0" not in diff_result.stdout:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Expected corrected expression was not "
                    "found in git diff."
                ),
            )

        if (
            "controller_validation: PASS"
            not in worker_result
            and
            '"controller_validation": "PASS"'
            not in worker_result
        ):
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Worker result did not report "
                    "controller_validation PASS."
                ),
            )

        return ScenarioResult(
            name=name,
            passed=True,
            detail=(
                "Injected validator bug was fixed, "
                "complete pytest passed, and expected "
                "minimal diff was present."
            ),
        )

    except Exception as exc:
        return ScenarioResult(
            name=name,
            passed=False,
            detail=(
                f"{type(exc).__name__}: {exc}"
            ),
        )

    finally:
        cleanup_ok = cleanup_workspace(
            temp_root
        )

        if cleanup_ok:
            print(
                "Cleanup: PASS"
            )
        else:
            print(
                "Cleanup: FAIL"
            )


def run_edit_failure_recovery() -> ScenarioResult:
    name = "edit_failure_recovery"

    temp_root = Path(
        tempfile.mkdtemp(
            prefix="qwen_regression_edit_recovery_"
        )
    ).resolve()

    print()
    print(
        f"=== Scenario: {name} ==="
    )
    print(
        f"Workspace: {temp_root}"
    )

    original_replace = (
        WorkspaceTools.replace_in_file
    )

    injection_state = {
        "calls": 0,
        "failed_once": False,
    }

    def fail_first_replace(
        self,
        relative_path: str,
        old_text: str,
        new_text: str,
        expected_count: int = 1,
    ) -> str:
        injection_state["calls"] += 1

        if not injection_state["failed_once"]:
            injection_state["failed_once"] = True

            print(
                "FAULT INJECTION: "
                "first replace_in_file forced to fail"
            )

            return (
                "??????FAULT_INJECTION?"
                "?? old_text / expected_count "
                "???????????"
            )

        return original_replace(
            self,
            relative_path=relative_path,
            old_text=old_text,
            new_text=new_text,
            expected_count=expected_count,
        )

    try:
        init_git_repo(
            temp_root
        )

        create_single_bug_fixture(
            temp_root
        )

        failing_test = run_command(
            [
                str(
                    Path(
                        os.sys.executable
                    )
                ),
                "-m",
                "pytest",
                "-q",
            ],
            temp_root,
        )

        if failing_test.returncode == 0:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Bug fixture unexpectedly passed "
                    "before Worker execution."
                ),
            )

        print(
            "Injected validator bug confirmed: PASS"
        )

        commit_baseline(
            temp_root
        )

        WorkspaceTools.replace_in_file = (
            fail_first_replace
        )

        try:
            worker_result = run_json_worker(
                task=(
                    "Fix the known quantity validation bug. "
                    "Zero must be invalid, therefore the "
                    "validator must require quantity > 0. "
                    "Run COMPLETE pytest first and read the "
                    "current source before editing. "
                    "If replace_in_file fails, follow the "
                    "controller recovery instruction exactly: "
                    "re-read src/validator.py before retrying. "
                    "Make only the minimal source fix and "
                    "complete all required validation."
                ),
                workspace_path=str(
                    temp_root
                ),
                max_rounds=10,
                allow_write=True,
                allow_run=True,
            )
        finally:
            WorkspaceTools.replace_in_file = (
                original_replace
            )

        print()
        print(
            "--- Worker Result ---"
        )
        print(
            worker_result
        )

        # -------------------------------------------------
        # Fault injection must actually have fired
        # -------------------------------------------------

        if not injection_state["failed_once"]:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Fault injection was never triggered."
                ),
            )

        if injection_state["calls"] < 2:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Worker did not retry replace_in_file "
                    "after the injected failure."
                ),
            )

        # -------------------------------------------------
        # Parse controller report
        # -------------------------------------------------

        try:
            payload = json.loads(
                worker_result
            )
        except json.JSONDecodeError as exc:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Worker result was not valid JSON: "
                    f"{exc}"
                ),
            )

        if (
            payload.get(
                "controller_validation"
            )
            != "PASS"
        ):
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Controller validation did not PASS."
                ),
            )

        if payload.get("status") != "changes_complete":
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Expected status changes_complete, "
                    f"got {payload.get('status')!r}."
                ),
            )

        # -------------------------------------------------
        # Action trace must prove:
        #
        # replace failure
        # -> read_file
        # -> replacement retry
        # -------------------------------------------------

        actions = [
            item.get("action")
            for item in payload.get(
                "action_trace",
                [],
            )
        ]

        replace_indices = [
            index
            for index, action
            in enumerate(actions)
            if action == "replace_in_file"
        ]

        if len(replace_indices) < 2:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Action trace did not contain "
                    "two replace_in_file attempts."
                ),
            )

        first_replace = replace_indices[0]
        second_replace = replace_indices[1]

        recovery_actions = actions[
            first_replace + 1:
            second_replace
        ]

        if "read_file" not in recovery_actions:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Worker retried replacement without "
                    "re-reading the file after edit failure."
                ),
            )

        # -------------------------------------------------
        # Final source must be fixed
        # -------------------------------------------------

        validator = (
            temp_root
            / "src"
            / "validator.py"
        )

        final_source = validator.read_text(
            encoding="utf-8"
        )

        if "return quantity > 0" not in final_source:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Expected validator fix was "
                    "not present."
                ),
            )

        if "return quantity >= 0" in final_source:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Original buggy expression remained."
                ),
            )

        # -------------------------------------------------
        # Complete pytest must be green
        # -------------------------------------------------

        final_test = run_command(
            [
                str(
                    Path(
                        os.sys.executable
                    )
                ),
                "-m",
                "pytest",
                "-q",
            ],
            temp_root,
        )

        if final_test.returncode != 0:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Final pytest failed.\n"
                    f"stdout:\n"
                    f"{final_test.stdout}\n"
                    f"stderr:\n"
                    f"{final_test.stderr}"
                ),
            )

        # -------------------------------------------------
        # Only expected source file should be dirty
        # -------------------------------------------------

        status_after = get_git_status(
            temp_root
        )

        status_lines = [
            line.strip()
            for line in status_after.splitlines()
            if line.strip()
        ]

        if status_lines != [
            "M src/validator.py"
        ]:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Unexpected Git working-tree state:\n"
                    f"{status_after}"
                ),
            )

        return ScenarioResult(
            name=name,
            passed=True,
            detail=(
                "First edit was deterministically rejected; "
                "Worker re-read current source, retried the "
                "replacement successfully, passed complete "
                "pytest, and completed controller validation."
            ),
        )

    except Exception as exc:
        return ScenarioResult(
            name=name,
            passed=False,
            detail=(
                f"{type(exc).__name__}: {exc}"
            ),
        )

    finally:
        # Safety restoration even if something failed
        WorkspaceTools.replace_in_file = (
            original_replace
        )

        cleanup_ok = cleanup_workspace(
            temp_root
        )

        if cleanup_ok:
            print(
                "Cleanup: PASS"
            )
        else:
            print(
                "Cleanup: FAIL"
            )


def run_hard_search_budget() -> ScenarioResult:
    name = "hard_search_budget"

    temp_root = Path(
        tempfile.mkdtemp(
            prefix="qwen_regression_search_budget_"
        )
    ).resolve()

    print()
    print(
        f"=== Scenario: {name} ==="
    )
    print(
        f"Workspace: {temp_root}"
    )

    original_ask_qwen = worker_module.ask_qwen
    original_unique_budget = (
        worker_module.MAX_UNIQUE_SEARCHES_PER_GENERATION
    )
    original_no_progress_budget = (
        worker_module.MAX_NO_PROGRESS_BEFORE_SEARCH_LOCK
    )

    # Deterministic scripted model.
    #
    # Hard-search gate prerequisites:
    # 1. writable + runnable test/debug task
    # 2. COMPLETE pytest has failed
    # 3. at least one current file has been read
    # 4. search budget has been exhausted
    #
    # With unique-search budget temporarily set to 2:
    #
    # Round 1: failing COMPLETE pytest
    # Round 2: current source read
    # Round 3: search #1 accepted
    # Round 4: search #2 accepted
    # Round 5: search #3 rejected by hard gate

    scripted_replies = [
        (
            '{"action":"run_tests",'
            '"args":{"target":""}}'
        ),
        (
            '{"action":"read_file",'
            '"args":{"relative_path":"src/validator.py"}}'
        ),
        (
            '{"action":"search_code",'
            '"args":{"query":"quantity",'
            '"file_pattern":"*.py",'
            '"max_results":50}}'
        ),
        (
            '{"action":"search_code",'
            '"args":{"query":"is_valid_quantity",'
            '"file_pattern":"*.py",'
            '"max_results":50}}'
        ),
        (
            '{"action":"search_code",'
            '"args":{"query":"validator",'
            '"file_pattern":"*.py",'
            '"max_results":50}}'
        ),
    ]

    scripted_state = {
        "index": 0,
    }

    def scripted_ask_qwen(
        messages: list,
        max_tokens: int = 850,
        transport_meta: dict | None = None,
    ) -> str:
        index = scripted_state["index"]

        if index >= len(scripted_replies):
            raise RuntimeError(
                "Scripted model was called more times "
                "than expected."
            )

        scripted_state["index"] += 1

        if transport_meta is not None:
            transport_meta.update(
                {
                    "transport": "scripted",
                    "time_to_headers_seconds": 0.0,
                    "time_to_first_chunk_seconds": 0.0,
                    "total_inference_time_seconds": 0.0,
                    "inference_time_seconds": 0.0,
                }
            )

        return scripted_replies[index]

    try:
        init_git_repo(
            temp_root
        )

        create_single_bug_fixture(
            temp_root
        )

        failing_test = run_command(
            [
                str(
                    Path(
                        os.sys.executable
                    )
                ),
                "-m",
                "pytest",
                "-q",
            ],
            temp_root,
        )

        if failing_test.returncode == 0:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Bug fixture unexpectedly passed "
                    "before Worker execution."
                ),
            )

        print(
            "Failing fixture confirmed: PASS"
        )

        commit_baseline(
            temp_root
        )

        # Isolate this regression specifically to the
        # unique-search hard budget.
        worker_module.MAX_UNIQUE_SEARCHES_PER_GENERATION = 2
        worker_module.MAX_NO_PROGRESS_BEFORE_SEARCH_LOCK = 999
        worker_module.ask_qwen = scripted_ask_qwen

        worker_result = run_json_worker(
            task=(
                "Investigate the failing quantity validation "
                "tests. This is a writable test/debug task "
                "used to verify the controller hard "
                "investigation budget. Do not modify files."
            ),
            workspace_path=str(
                temp_root
            ),
            max_rounds=5,
            allow_write=True,
            allow_run=True,
        )

        print()
        print(
            "--- Worker Result ---"
        )
        print(
            worker_result
        )

        try:
            payload = json.loads(
                worker_result
            )
        except json.JSONDecodeError as exc:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Worker result was not valid JSON: "
                    f"{exc}"
                ),
            )

        trace = payload.get(
            "action_trace",
            [],
        )

        actions = [
            item.get("action")
            for item in trace
        ]

        expected_actions = [
            "run_tests",
            "read_file",
            "search_code",
            "search_code",
            "search_code",
        ]

        if actions != expected_actions:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Unexpected scripted action trace.\n"
                    f"Expected: {expected_actions}\n"
                    f"Observed: {actions}"
                ),
            )

        third_search_result = (
            trace[4].get(
                "controller_result",
                "",
            )
        )

        if (
            "CONTROLLER SEARCH PHASE CLOSED"
            not in third_search_result
        ):
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Third unique search was not rejected "
                    "by the hard search gate.\n"
                    "Controller result:\n"
                    f"{third_search_result}"
                ),
            )

        if (
            "Unique searches used: 2/2"
            not in third_search_result
        ):
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Search gate closed, but expected "
                    "2/2 unique-search accounting was "
                    "not reported.\n"
                    "Controller result:\n"
                    f"{third_search_result}"
                ),
            )

        # Verify first two searches were genuinely executed,
        # not rejected early.
        for index in (2, 3):
            controller_result = (
                trace[index].get(
                    "controller_result",
                    "",
                )
            )

            if (
                "CONTROLLER SEARCH PHASE CLOSED"
                in controller_result
            ):
                return ScenarioResult(
                    name=name,
                    passed=False,
                    detail=(
                        "Search gate closed earlier than "
                        "the configured two-search budget."
                    ),
                )

        if scripted_state["index"] != 5:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Expected exactly five scripted model "
                    "calls, observed "
                    f"{scripted_state['index']}."
                ),
            )

        # This scenario deliberately does not repair the
        # failing fixture, so final Worker status may be
        # INCOMPLETE. That is expected and not the object
        # under test.
        if payload.get("status") != "INCOMPLETE":
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Expected Worker to end INCOMPLETE after "
                    "the five-round controller-only probe, "
                    f"got {payload.get('status')!r}."
                ),
            )

        status_after = get_git_status(
            temp_root
        )

        if status_after:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Search-budget scenario modified "
                    "the repository:\n"
                    f"{status_after}"
                ),
            )

        return ScenarioResult(
            name=name,
            passed=True,
            detail=(
                "After failing COMPLETE pytest and a current "
                "source read, the controller accepted exactly "
                "two unique searches and rejected the third "
                "with SEARCH PHASE CLOSED. No repository "
                "changes occurred."
            ),
        )

    except Exception as exc:
        return ScenarioResult(
            name=name,
            passed=False,
            detail=(
                f"{type(exc).__name__}: {exc}"
            ),
        )

    finally:
        worker_module.ask_qwen = (
            original_ask_qwen
        )

        worker_module.MAX_UNIQUE_SEARCHES_PER_GENERATION = (
            original_unique_budget
        )

        worker_module.MAX_NO_PROGRESS_BEFORE_SEARCH_LOCK = (
            original_no_progress_budget
        )

        cleanup_ok = cleanup_workspace(
            temp_root
        )

        if cleanup_ok:
            print(
                "Cleanup: PASS"
            )
        else:
            print(
                "Cleanup: FAIL"
            )


def run_controller_failure_regression(
    failure_mode: str,
) -> ScenarioResult:

    if failure_mode not in {
        "pytest",
        "diffcheck",
    }:
        raise ValueError(
            f"Unsupported failure mode: {failure_mode}"
        )

    if failure_mode == "pytest":
        name = "controller_pytest_failure"
        prefix = "qwen_regression_controller_pytest_fail_"
    else:
        name = "controller_diffcheck_failure"
        prefix = "qwen_regression_controller_diffcheck_fail_"

    temp_root = Path(
        tempfile.mkdtemp(
            prefix=prefix
        )
    ).resolve()

    print()
    print(
        f"=== Scenario: {name} ==="
    )
    print(
        f"Workspace: {temp_root}"
    )

    original_ask_qwen = (
        worker_module.ask_qwen
    )

    original_run_tests = (
        WorkspaceTools.run_tests
    )

    original_diff_check = (
        worker_module.run_git_diff_check
    )

    original_git_diff = (
        WorkspaceTools.git_diff
    )

    original_git_status = (
        WorkspaceTools.git_status
    )

    injection_state = {
        "run_tests_calls": 0,
        "diff_check_calls": 0,
        "git_diff_calls": 0,
        "git_status_calls": 0,
        "injected": False,
        "stop_checked": False,
    }

    if failure_mode == "pytest":

        # Qwen path:
        #
        # 1 initial COMPLETE pytest -> real fixture failure
        # 2 read source
        # 3 replace source
        #
        # Controller AUTO:
        #   COMPLETE pytest -> INJECTED FAILURE
        #   STOP: no Git validation may execute
        #
        # Qwen recovery:
        # 4 COMPLETE pytest -> real PASS
        # 5 git diff --check
        # 6 git diff
        # 7 git status
        # 8 finish

        scripted_replies = [
            (
                '{"action":"run_tests",'
                '"args":{"target":""}}'
            ),
            (
                '{"action":"read_file",'
                '"args":{"relative_path":"src/validator.py"}}'
            ),
            (
                '{"action":"replace_in_file",'
                '"args":{'
                '"relative_path":"src/validator.py",'
                '"old_text":"return quantity >= 0",'
                '"new_text":"return quantity > 0",'
                '"expected_count":1'
                '}}'
            ),
            (
                '{"action":"run_tests",'
                '"args":{"target":""}}'
            ),
            (
                '{"action":"git_diff_check",'
                '"args":{}}'
            ),
            (
                '{"action":"git_diff",'
                '"args":{}}'
            ),
            (
                '{"action":"git_status",'
                '"args":{}}'
            ),
            (
                '{"action":"finish",'
                '"status":"changes_complete",'
                '"summary":"Recovered from injected pytest '
                'failure and completed validation.",'
                '"findings":[]}'
            ),
        ]

        expected_actions = [
            "run_tests",
            "read_file",
            "replace_in_file",
            "run_tests",
            "git_diff_check",
            "git_diff",
            "git_status",
            "finish",
        ]

    else:

        # Qwen path:
        #
        # 1 initial COMPLETE pytest -> real fixture failure
        # 2 read source
        # 3 replace source
        #
        # Controller AUTO:
        #   COMPLETE pytest -> PASS
        #   git diff --check -> INJECTED FAILURE
        #   STOP: git diff / git status may NOT execute
        #
        # Qwen recovery:
        # 4 git diff --check -> real PASS
        # 5 git diff
        # 6 git status
        # 7 finish

        scripted_replies = [
            (
                '{"action":"run_tests",'
                '"args":{"target":""}}'
            ),
            (
                '{"action":"read_file",'
                '"args":{"relative_path":"src/validator.py"}}'
            ),
            (
                '{"action":"replace_in_file",'
                '"args":{'
                '"relative_path":"src/validator.py",'
                '"old_text":"return quantity >= 0",'
                '"new_text":"return quantity > 0",'
                '"expected_count":1'
                '}}'
            ),
            (
                '{"action":"git_diff_check",'
                '"args":{}}'
            ),
            (
                '{"action":"git_diff",'
                '"args":{}}'
            ),
            (
                '{"action":"git_status",'
                '"args":{}}'
            ),
            (
                '{"action":"finish",'
                '"status":"changes_complete",'
                '"summary":"Recovered from injected diff-check '
                'failure and completed validation.",'
                '"findings":[]}'
            ),
        ]

        expected_actions = [
            "run_tests",
            "read_file",
            "replace_in_file",
            "git_diff_check",
            "git_diff",
            "git_status",
            "finish",
        ]

    scripted_state = {
        "index": 0,
    }

    def counted_run_tests(
        self,
        *args,
        **kwargs,
    ) -> str:

        injection_state[
            "run_tests_calls"
        ] += 1

        if (
            failure_mode == "pytest"
            and
            injection_state[
                "run_tests_calls"
            ]
            == 2
        ):
            injection_state[
                "injected"
            ] = True

            print(
                "FAULT INJECTION: "
                "automatic post-change pytest forced to fail"
            )

            return (
                "=== REGRESSION INJECTED PYTEST FAILURE ===\n"
                "1 failed in 0.01s\n"
                "EXIT CODE: 1"
            )

        return original_run_tests(
            self,
            *args,
            **kwargs,
        )

    def counted_diff_check(
        *args,
        **kwargs,
    ):

        injection_state[
            "diff_check_calls"
        ] += 1

        if (
            failure_mode == "diffcheck"
            and
            injection_state[
                "diff_check_calls"
            ]
            == 1
        ):
            injection_state[
                "injected"
            ] = True

            print(
                "FAULT INJECTION: automatic "
                "git diff --check forced to fail"
            )

            return (
                False,
                (
                    "=== REGRESSION INJECTED "
                    "GIT DIFF CHECK FAILURE ===\n"
                    "trailing whitespace detected\n"
                    "EXIT CODE: 1"
                ),
            )

        return original_diff_check(
            *args,
            **kwargs,
        )

    def counted_git_diff(
        self,
        *args,
        **kwargs,
    ) -> str:

        injection_state[
            "git_diff_calls"
        ] += 1

        return original_git_diff(
            self,
            *args,
            **kwargs,
        )

    def counted_git_status(
        self,
        *args,
        **kwargs,
    ) -> str:

        injection_state[
            "git_status_calls"
        ] += 1

        return original_git_status(
            self,
            *args,
            **kwargs,
        )

    def scripted_ask_qwen(
        messages: list,
        max_tokens: int = 850,
        transport_meta: dict | None = None,
    ) -> str:

        index = scripted_state[
            "index"
        ]

        if index >= len(
            scripted_replies
        ):
            raise RuntimeError(
                "Scripted model was called more "
                "times than expected."
            )

        # ----------------------------------------------------
        # index == 3 means the Worker has completed the
        # successful replacement AND returned from the
        # automatic Controller pipeline.
        #
        # We inspect counters BEFORE giving Qwen its recovery
        # action. This proves fail-stop behavior at the exact
        # control-transfer boundary.
        # ----------------------------------------------------

        if index == 3:

            if not injection_state[
                "injected"
            ]:
                raise RuntimeError(
                    "Expected failure injection "
                    "did not fire before Qwen recovery."
                )

            if failure_mode == "pytest":

                if (
                    injection_state[
                        "run_tests_calls"
                    ]
                    != 2
                ):
                    raise RuntimeError(
                        "Unexpected pytest call count at "
                        "pytest failure boundary: "
                        f"{injection_state['run_tests_calls']}"
                    )

                if (
                    injection_state[
                        "diff_check_calls"
                    ]
                    != 0
                    or
                    injection_state[
                        "git_diff_calls"
                    ]
                    != 0
                    or
                    injection_state[
                        "git_status_calls"
                    ]
                    != 0
                ):
                    raise RuntimeError(
                        "Controller continued into Git "
                        "validation after pytest failure: "
                        f"{injection_state}"
                    )

            else:

                if (
                    injection_state[
                        "run_tests_calls"
                    ]
                    != 2
                ):
                    raise RuntimeError(
                        "Expected initial + automatic pytest "
                        "before diff-check failure; observed "
                        f"{injection_state['run_tests_calls']}."
                    )

                if (
                    injection_state[
                        "diff_check_calls"
                    ]
                    != 1
                ):
                    raise RuntimeError(
                        "Unexpected diff-check call count at "
                        "failure boundary: "
                        f"{injection_state['diff_check_calls']}"
                    )

                if (
                    injection_state[
                        "git_diff_calls"
                    ]
                    != 0
                    or
                    injection_state[
                        "git_status_calls"
                    ]
                    != 0
                ):
                    raise RuntimeError(
                        "Controller continued into git diff "
                        "or git status after diff-check "
                        f"failure: {injection_state}"
                    )

            injection_state[
                "stop_checked"
            ] = True

            print(
                "FAIL-STOP BOUNDARY: PASS"
            )

        scripted_state[
            "index"
        ] += 1

        if transport_meta is not None:
            transport_meta.update(
                {
                    "transport": "scripted",
                    "time_to_headers_seconds": 0.0,
                    "time_to_first_chunk_seconds": 0.0,
                    "total_inference_time_seconds": 0.0,
                    "inference_time_seconds": 0.0,
                }
            )

        return scripted_replies[
            index
        ]

    try:
        init_git_repo(
            temp_root
        )

        create_single_bug_fixture(
            temp_root
        )

        initial_test = run_command(
            [
                str(
                    Path(
                        os.sys.executable
                    )
                ),
                "-m",
                "pytest",
                "-q",
            ],
            temp_root,
        )

        if initial_test.returncode == 0:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Bug fixture unexpectedly passed "
                    "before Worker execution."
                ),
            )

        print(
            "Failing fixture confirmed: PASS"
        )

        commit_baseline(
            temp_root
        )

        worker_module.ask_qwen = (
            scripted_ask_qwen
        )

        WorkspaceTools.run_tests = (
            counted_run_tests
        )

        worker_module.run_git_diff_check = (
            counted_diff_check
        )

        WorkspaceTools.git_diff = (
            counted_git_diff
        )

        WorkspaceTools.git_status = (
            counted_git_status
        )

        worker_result = run_json_worker(
            task=(
                "Fix the failing quantity validation bug. "
                "Zero must be invalid. Make only the minimal "
                "source change. If deterministic validation "
                "fails, recover using the returned evidence "
                "and complete all mandatory validation."
            ),
            workspace_path=str(
                temp_root
            ),
            max_rounds=10,
            allow_write=True,
            allow_run=True,
        )

        print()
        print(
            "--- Worker Result ---"
        )
        print(
            worker_result
        )

        if not injection_state[
            "injected"
        ]:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Fault injection was never triggered."
                ),
            )

        if not injection_state[
            "stop_checked"
        ]:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Fail-stop boundary was never observed "
                    "before Qwen recovery."
                ),
            )

        if (
            scripted_state[
                "index"
            ]
            != len(
                scripted_replies
            )
        ):
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Unexpected scripted model call count: "
                    f"{scripted_state['index']} / "
                    f"{len(scripted_replies)}."
                ),
            )

        try:
            payload = json.loads(
                worker_result
            )
        except json.JSONDecodeError as exc:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Worker result was not valid JSON: "
                    f"{exc}"
                ),
            )

        if (
            payload.get(
                "controller_validation"
            )
            != "PASS"
        ):
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Final controller validation "
                    "did not PASS."
                ),
            )

        if (
            payload.get("status")
            != "changes_complete"
        ):
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Expected final status "
                    "changes_complete, got "
                    f"{payload.get('status')!r}."
                ),
            )

        actions = [
            item.get("action")
            for item in payload.get(
                "action_trace",
                [],
            )
        ]

        if actions != expected_actions:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Unexpected Qwen action sequence.\n"
                    f"Expected: {expected_actions}\n"
                    f"Observed: {actions}"
                ),
            )

        # ----------------------------------------------------
        # Final exact call counts prove recovery happened only
        # after Controller returned control to Qwen.
        # ----------------------------------------------------

        if failure_mode == "pytest":

            expected_counts = {
                "run_tests_calls": 3,
                "diff_check_calls": 1,
                "git_diff_calls": 1,
                "git_status_calls": 1,
            }

        else:

            expected_counts = {
                "run_tests_calls": 2,
                "diff_check_calls": 2,
                "git_diff_calls": 1,
                "git_status_calls": 1,
            }

        for key, expected in expected_counts.items():

            observed = injection_state[
                key
            ]

            if observed != expected:
                return ScenarioResult(
                    name=name,
                    passed=False,
                    detail=(
                        f"Unexpected {key}: "
                        f"expected {expected}, "
                        f"observed {observed}."
                    ),
                )

        validator = (
            temp_root
            / "src"
            / "validator.py"
        )

        final_source = validator.read_text(
            encoding="utf-8"
        )

        if (
            "return quantity > 0"
            not in final_source
        ):
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Expected validator fix "
                    "was not present."
                ),
            )

        final_test = run_command(
            [
                str(
                    Path(
                        os.sys.executable
                    )
                ),
                "-m",
                "pytest",
                "-q",
            ],
            temp_root,
        )

        if final_test.returncode != 0:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Final external pytest failed.\n"
                    f"stdout:\n{final_test.stdout}\n"
                    f"stderr:\n{final_test.stderr}"
                ),
            )

        status_after = get_git_status(
            temp_root
        )

        status_lines = [
            line.strip()
            for line in status_after.splitlines()
            if line.strip()
        ]

        if status_lines != [
            "M src/validator.py"
        ]:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Unexpected final Git state:\n"
                    f"{status_after}"
                ),
            )

        if failure_mode == "pytest":
            detail = (
                "Injected automatic pytest failure stopped "
                "the Controller before all Git validation; "
                "Qwen regained control, recovered, and "
                "completed validation successfully."
            )
        else:
            detail = (
                "Injected automatic diff-check failure "
                "stopped the Controller before git diff and "
                "git status; Qwen regained control, recovered, "
                "and completed validation successfully."
            )

        return ScenarioResult(
            name=name,
            passed=True,
            detail=detail,
        )

    except Exception as exc:
        return ScenarioResult(
            name=name,
            passed=False,
            detail=(
                f"{type(exc).__name__}: {exc}"
            ),
        )

    finally:
        worker_module.ask_qwen = (
            original_ask_qwen
        )

        WorkspaceTools.run_tests = (
            original_run_tests
        )

        worker_module.run_git_diff_check = (
            original_diff_check
        )

        WorkspaceTools.git_diff = (
            original_git_diff
        )

        WorkspaceTools.git_status = (
            original_git_status
        )

        cleanup_ok = cleanup_workspace(
            temp_root
        )

        if cleanup_ok:
            print(
                "Cleanup: PASS"
            )
        else:
            print(
                "Cleanup: FAIL"
            )


def run_controller_pytest_failure() -> ScenarioResult:
    return run_controller_failure_regression(
        "pytest"
    )


def run_controller_diffcheck_failure() -> ScenarioResult:
    return run_controller_failure_regression(
        "diffcheck"
    )


def run_validation_tail() -> ScenarioResult:
    name = "validation_tail"

    temp_root = Path(
        tempfile.mkdtemp(
            prefix="qwen_regression_validation_tail_"
        )
    ).resolve()

    print()
    print(
        f"=== Scenario: {name} ==="
    )
    print(
        f"Workspace: {temp_root}"
    )

    original_ask_qwen = worker_module.ask_qwen

    # Exactly three normal coding rounds:
    #
    # Base 1: COMPLETE pytest fails
    # Base 2: read current source
    # Base 3: perform the fix
    #
    # The successful replacement must immediately trigger
    # Controller-driven post-change validation INSIDE Base 3:
    #
    # complete pytest
    # -> git diff --check
    # -> git diff
    # -> git status
    #
    # The base coding budget is then exhausted.
    # Validation Tail must require only the final Qwen finish.
    #
    # Therefore exactly four model calls are expected:
    #
    # 1 run_tests
    # 2 read_file
    # 3 replace_in_file
    # 4 finish

    scripted_replies = [
        (
            '{"action":"run_tests",'
            '"args":{"target":""}}'
        ),
        (
            '{"action":"read_file",'
            '"args":{"relative_path":"src/validator.py"}}'
        ),
        (
            '{"action":"replace_in_file",'
            '"args":{'
            '"relative_path":"src/validator.py",'
            '"old_text":"return quantity >= 0",'
            '"new_text":"return quantity > 0",'
            '"expected_count":1'
            '}}'
        ),
        (
            '{"action":"finish",'
            '"status":"changes_complete",'
            '"summary":"Fixed quantity validation and '
            'completed controller-driven validation.",'
            '"findings":[]}'
        ),
    ]

    scripted_state = {
        "index": 0,
    }

    def scripted_ask_qwen(
        messages: list,
        max_tokens: int = 850,
        transport_meta: dict | None = None,
    ) -> str:
        index = scripted_state["index"]

        if index >= len(scripted_replies):
            raise RuntimeError(
                "Scripted model was called more times "
                "than expected."
            )

        scripted_state["index"] += 1

        if transport_meta is not None:
            transport_meta.update(
                {
                    "transport": "scripted",
                    "time_to_headers_seconds": 0.0,
                    "time_to_first_chunk_seconds": 0.0,
                    "total_inference_time_seconds": 0.0,
                    "inference_time_seconds": 0.0,
                }
            )

        return scripted_replies[index]

    try:
        init_git_repo(
            temp_root
        )

        create_single_bug_fixture(
            temp_root
        )

        failing_test = run_command(
            [
                str(
                    Path(
                        os.sys.executable
                    )
                ),
                "-m",
                "pytest",
                "-q",
            ],
            temp_root,
        )

        if failing_test.returncode == 0:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Bug fixture unexpectedly passed "
                    "before Worker execution."
                ),
            )

        print(
            "Failing fixture confirmed: PASS"
        )

        commit_baseline(
            temp_root
        )

        worker_module.ask_qwen = (
            scripted_ask_qwen
        )

        worker_result = run_json_worker(
            task=(
                "Fix the failing quantity validation test. "
                "Zero must be invalid. Make the smallest "
                "safe source change and complete all "
                "mandatory post-change validation."
            ),
            workspace_path=str(
                temp_root
            ),
            max_rounds=3,
            allow_write=True,
            allow_run=True,
        )

        print()
        print(
            "--- Worker Result ---"
        )
        print(
            worker_result
        )

        try:
            payload = json.loads(
                worker_result
            )
        except json.JSONDecodeError as exc:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Worker result was not valid JSON: "
                    f"{exc}"
                ),
            )

        # -------------------------------------------------
        # Final controller success
        # -------------------------------------------------

        if payload.get("status") != "changes_complete":
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Expected status changes_complete, "
                    f"got {payload.get('status')!r}."
                ),
            )

        if (
            payload.get(
                "controller_validation"
            )
            != "PASS"
        ):
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Controller validation did not PASS."
                ),
            )

        trace = payload.get(
            "action_trace",
            [],
        )

        actions = [
            item.get("action")
            for item in trace
        ]

        expected_actions = [
            "run_tests",
            "read_file",
            "replace_in_file",
            "finish",
        ]

        if actions != expected_actions:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Unexpected Validation Tail action "
                    "sequence.\n"
                    f"Expected: {expected_actions}\n"
                    f"Observed: {actions}"
                ),
            )

        # -------------------------------------------------
        # Phase information lives in request_telemetry.
        #
        # Successful final reports intentionally expose a
        # compact action_trace containing only round/action.
        # -------------------------------------------------

        telemetry = payload.get(
            "request_telemetry",
            [],
        )

        if len(telemetry) != 4:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Expected exactly four telemetry "
                    "records, observed "
                    f"{len(telemetry)}."
                ),
            )

        # First three requests must be normal base phase.
        for index in range(3):
            item = telemetry[index]

            if (
                item.get("phase")
                != "base"
            ):
                return ScenarioResult(
                    name=name,
                    passed=False,
                    detail=(
                        "A pre-tail request was not marked "
                        "phase=base."
                    ),
                )

            if (
                item.get(
                    "validation_tail_round"
                )
                is not None
            ):
                return ScenarioResult(
                    name=name,
                    passed=False,
                    detail=(
                        "Base request unexpectedly had a "
                        "validation_tail_round."
                    ),
                )

        # Exactly one request remains after the base budget:
        # final finish in Validation Tail round 1.
        tail_telemetry = telemetry[3:]

        if len(tail_telemetry) != 1:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Expected exactly one Validation Tail "
                    "telemetry record, observed "
                    f"{len(tail_telemetry)}."
                ),
            )

        tail_item = tail_telemetry[0]

        if (
            tail_item.get("phase")
            != "validation_tail"
        ):
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Final finish request was not marked "
                    "phase=validation_tail."
                ),
            )

        if (
            tail_item.get(
                "validation_tail_round"
            )
            != 1
        ):
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Expected final finish in Validation Tail "
                    "round 1, got "
                    f"{tail_item.get('validation_tail_round')}."
                ),
            )

        # -------------------------------------------------
        # Validation state must prove the whole chain.
        # -------------------------------------------------

        if not payload.get(
            "full_tests_run"
        ):
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Complete pytest was not recorded."
                ),
            )

        if not payload.get(
            "full_tests_passed"
        ):
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Post-change complete pytest "
                    "did not pass."
                ),
            )

        if not payload.get(
            "post_change_validation_passed"
        ):
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Post-change validation was not "
                    "recorded as passed."
                ),
            )

        if not payload.get(
            "git_diff_check_passed"
        ):
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "git diff --check did not pass."
                ),
            )

        if not payload.get(
            "git_diff_checked"
        ):
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Final git diff was not inspected."
                ),
            )

        if not payload.get(
            "git_status_checked"
        ):
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Final git status was not inspected."
                ),
            )

        # -------------------------------------------------
        # Final source and repository state.
        # -------------------------------------------------

        validator = (
            temp_root
            / "src"
            / "validator.py"
        )

        final_source = validator.read_text(
            encoding="utf-8"
        )

        if (
            "return quantity > 0"
            not in final_source
        ):
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Expected validator fix was absent."
                ),
            )

        final_test = run_command(
            [
                str(
                    Path(
                        os.sys.executable
                    )
                ),
                "-m",
                "pytest",
                "-q",
            ],
            temp_root,
        )

        if final_test.returncode != 0:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Independent final pytest failed."
                ),
            )

        status_after = get_git_status(
            temp_root
        )

        status_lines = [
            line.strip()
            for line in status_after.splitlines()
            if line.strip()
        ]

        if status_lines != [
            "M src/validator.py"
        ]:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Unexpected final Git state:\n"
                    f"{status_after}"
                ),
            )

        if scripted_state["index"] != 4:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Expected exactly four scripted "
                    "model calls, observed "
                    f"{scripted_state['index']}."
                ),
            )

        return ScenarioResult(
            name=name,
            passed=True,
            detail=(
                "Successful source replacement triggered "
                "Controller-driven pytest and Git validation "
                "without extra LLM rounds; after the base "
                "budget expired, Validation Tail required "
                "only the final Qwen finish."
            ),
        )

    except Exception as exc:
        return ScenarioResult(
            name=name,
            passed=False,
            detail=(
                f"{type(exc).__name__}: {exc}"
            ),
        )

    finally:
        worker_module.ask_qwen = (
            original_ask_qwen
        )

        cleanup_ok = cleanup_workspace(
            temp_root
        )

        if cleanup_ok:
            print(
                "Cleanup: PASS"
            )
        else:
            print(
                "Cleanup: FAIL"
            )

def run_healthy_baseline() -> ScenarioResult:
    name = "healthy_baseline"

    temp_root = Path(
        tempfile.mkdtemp(
            prefix="qwen_regression_healthy_"
        )
    ).resolve()

    print()
    print(
        f"=== Scenario: {name} ==="
    )
    print(
        f"Workspace: {temp_root}"
    )

    try:
        init_git_repo(
            temp_root
        )

        create_healthy_fixture(
            temp_root
        )

        baseline_test = run_command(
            [
                str(
                    Path(
                        os.sys.executable
                    )
                ),
                "-m",
                "pytest",
                "-q",
            ],
            temp_root,
        )
        require_success(
            baseline_test,
            "baseline pytest",
        )

        print(
            "Fixture pytest baseline: PASS"
        )

        commit_baseline(
            temp_root
        )

        worker_result = run_json_worker(
            task=(
                "Inspect this project and determine "
                "whether there is a bug in the quantity "
                "validation behavior. Run the complete "
                "test suite and inspect the relevant "
                "source and tests. If the implementation "
                "is already correct, do not modify any "
                "file. Finish with an evidence-backed "
                "conclusion."
            ),
            workspace_path=str(
                temp_root
            ),
            max_rounds=8,
            allow_write=True,
            allow_run=True,
        )

        print()
        print(
            "--- Worker Result ---"
        )
        print(
            worker_result
        )

        status_after = get_git_status(
            temp_root
        )

        if status_after:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Worker modified a healthy "
                    "repository:\n"
                    f"{status_after}"
                ),
            )

        final_test = run_command(
            [
                str(
                    Path(
                        os.sys.executable
                    )
                ),
                "-m",
                "pytest",
                "-q",
            ],
            temp_root,
        )

        if final_test.returncode != 0:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Final pytest failed.\n"
                    f"stdout:\n"
                    f"{final_test.stdout}\n"
                    f"stderr:\n"
                    f"{final_test.stderr}"
                ),
            )

        if (
            "controller_validation: PASS"
            not in worker_result
            and
            '"controller_validation": "PASS"'
            not in worker_result
        ):
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Worker result did not report "
                    "controller_validation PASS."
                ),
            )

        try:
            worker_data = json.loads(
                worker_result
            )
        except json.JSONDecodeError:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Lean Healthy regression: "
                    "Worker result was not valid JSON."
                ),
            )

        actions = [
            item.get("action")
            for item in worker_data.get("action_trace", [])
            if isinstance(item, dict)
        ]

        if not actions:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Lean Healthy regression: "
                    "Worker result had no action_trace."
                ),
            )

        if actions[-1] != "finish":
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Lean Healthy regression: "
                    "healthy path did not end with finish. "
                    f"Trace: {actions}"
                ),
            )

        forbidden_git_actions = {
            "git_diff_check",
            "git_diff",
            "git_status",
        }

        unexpected_git_actions = [
            action
            for action in actions
            if action in forbidden_git_actions
        ]

        if unexpected_git_actions:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Lean Healthy regression: "
                    "unmodified healthy path performed "
                    "unnecessary Git reassurance actions: "
                    f"{unexpected_git_actions}. "
                    f"Trace: {actions}"
                ),
            )

        if len(actions) > 6:
            return ScenarioResult(
                name=name,
                passed=False,
                detail=(
                    "Lean Healthy regression: "
                    "healthy path exceeded 6 LLM rounds. "
                    f"Trace: {actions}"
                ),
            )

        return ScenarioResult(
            name=name,
            passed=True,
            detail=(
                "Healthy repository remained clean "
                "and complete pytest passed."
            ),
        )

    except Exception as exc:
        return ScenarioResult(
            name=name,
            passed=False,
            detail=(
                f"{type(exc).__name__}: {exc}"
            ),
        )

    finally:
        cleanup_ok = cleanup_workspace(
            temp_root
        )

        if cleanup_ok:
            print(
                "Cleanup: PASS"
            )
        else:
            print(
                "Cleanup: FAIL"
            )


def run_bootstrap_check() -> ScenarioResult:
    name = "bootstrap"

    temp_root = Path(
        tempfile.mkdtemp(
            prefix="qwen_regression_bootstrap_"
        )
    ).resolve()

    print(
        "Regression sandbox created:"
    )
    print(
        temp_root
    )

    try:
        init_git_repo(
            temp_root
        )

        print(
            "Git repository initialized: PASS"
        )

        create_healthy_fixture(
            temp_root
        )

        print(
            "Fixture created: PASS"
        )

        commit_baseline(
            temp_root
        )

        print(
            "Clean repository baseline: PASS"
        )

        return ScenarioResult(
            name=name,
            passed=True,
            detail=(
                "Regression sandbox bootstrap "
                "completed successfully."
            ),
        )

    except Exception as exc:
        return ScenarioResult(
            name=name,
            passed=False,
            detail=(
                f"{type(exc).__name__}: {exc}"
            ),
        )

    finally:
        cleanup_ok = cleanup_workspace(
            temp_root
        )

        if cleanup_ok:
            print(
                "Cleanup: PASS"
            )
        else:
            print(
                "Cleanup: FAIL"
            )


def print_summary(
    results: list[ScenarioResult],
) -> int:
    print()
    print(
        "========================================"
    )
    print(
        "REGRESSION HARNESS SUMMARY"
    )
    print(
        "========================================"
    )

    passed_count = 0

    for result in results:
        status = (
            "PASS"
            if result.passed
            else "FAIL"
        )

        print(
            f"[{status}] {result.name}"
        )
        print(
            f"       {result.detail}"
        )

        if result.passed:
            passed_count += 1

    failed_count = (
        len(results)
        - passed_count
    )

    print()
    print(
        f"{passed_count} passed, "
        f"{failed_count} failed"
    )

    if failed_count:
        return 1

    return 0


def main() -> int:
    results: list[ScenarioResult] = []

    results.append(
        run_bootstrap_check()
    )

    results.append(
        run_healthy_baseline()
    )

    results.append(
        run_single_validator_bug()
    )

    results.append(
        run_edit_failure_recovery()
    )

    results.append(
        run_hard_search_budget()
    )

    results.append(
        run_validation_tail()
    )

    results.append(
        run_controller_pytest_failure()
    )

    results.append(
        run_controller_diffcheck_failure()
    )

    return print_summary(
        results
    )


if __name__ == "__main__":
    raise SystemExit(
        main()
    )