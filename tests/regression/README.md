# Regression Harness

Regression coverage for the Local Qwen Coding Worker Controller and live Qwen integration.

## Purpose

The harness protects Controller behavior against regressions while keeping deterministic Controller tests separate from model-dependent integration tests.

## Scenarios

| Scenario | Type | Purpose |
| --- | --- | --- |
| `bootstrap` | Deterministic | Verifies isolated temporary Git repository setup and cleanup. |
| `healthy_baseline` | Live Qwen integration | Verifies healthy-project convergence without unnecessary Git validation. |
| `single_validator_bug` | Live Qwen integration | Verifies minimal source repair and the complete modified-path validation workflow. |
| `edit_failure_recovery` | Live Qwen + fault injection | Verifies read-refresh recovery after a deterministically rejected replacement. |
| `hard_search_budget` | Deterministic scripted Controller test | Verifies enforcement of the investigation/search budget. |
| `validation_tail` | Deterministic scripted Controller test | Verifies successful Controller-driven validation followed by Qwen-owned final finish in Validation Tail. |
| `controller_pytest_failure` | Deterministic fault injection | Verifies that automatic post-change pytest failure stops before Git validation and returns control to Qwen. |
| `controller_diffcheck_failure` | Deterministic fault injection | Verifies that automatic `git diff --check` failure stops before `git diff` and `git status` and returns control to Qwen. |

## Controller-Driven Post-Change Validation

After a successful source modification, the Controller may automatically execute:

```text
complete pytest
-> git diff --check
-> git diff
-> git status
This pipeline is fail-closed.
If complete pytest fails:
STOP
-> return evidence to Qwen
No automatic Git validation follows.
If git diff --check fails:
STOP
-> return evidence to Qwen
No automatic git diff or git status follows.
The Controller never auto-submits finish.
Final completion remains Qwen-owned and continues to pass through the existing finish hard gate.
Validation Tail
Validation Tail remains a validation-only reserve rather than additional debugging time.
When Controller-driven pytest and Git validation have already succeeded, an exhausted base coding budget requires only the final Qwen finish action in the tail.
Running the Full Suite
From the project root:
.\.venv\Scripts\python.exe .\tests\regression\runner.py
Current stable baseline:
8 passed, 0 failed
Test Layers
Deterministic Controller Regression
- bootstrap
- hard_search_budget
- validation_tail
- controller_pytest_failure
- controller_diffcheck_failure
Live Qwen Integration
- healthy_baseline
- single_validator_bug
- edit_failure_recovery
LM Studio must be running and exposing the configured OpenAI-compatible endpoint and model for live scenarios.
Isolation
Every scenario creates its own temporary Git repository.
The harness must not modify the production project workspace.
Current Stable Coverage
The suite covers:
- clean-project recognition
- Lean Healthy convergence
- minimal bug repair
- complete pytest enforcement
- edit failure recovery
- hard investigation/search budget
- Controller-driven post-change validation
- pytest fail-stop behavior
- git diff --check fail-stop behavior
- Git finalization
- Validation Tail continuation
- Qwen-owned final finish
- Controller finish gates

## Search-Driven Evidence Acquisition
v0.3.14 adds a bounded Search-to-Read Bridge for qualifying failing-test investigations.
The official Regression Harness remains 8 scenarios. No new runner.py scenario is added in this release.
Release qualification additionally verified these Bridge properties outside the official eight-scenario runner:
- bounded successful acquisition
- malformed-search fallback
- too-many-files fallback
- oversized-evidence rollback
- read-exception rollback
The release-candidate deterministic Bridge probes passed 5/5, and the official production Regression Harness passed 8/8.
