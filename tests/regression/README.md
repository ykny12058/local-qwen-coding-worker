@'

from pathlib import Path



path = Path(

&#x20;   r"D:\\LocalQwenWorker\\tests\\regression\\README.md"

)



content = """# Regression Harness



Regression coverage for the Local Qwen Coding Worker controller and live Qwen integration.



\## Purpose



The harness protects controller behavior against regressions while keeping deterministic controller tests separate from model-dependent integration tests.



\## Scenarios



| Scenario | Type | Purpose |

| --- | --- | --- |

| `bootstrap` | Deterministic | Creates an isolated temporary Git repository and verifies sandbox setup and cleanup. |

| `healthy\_baseline` | Live Qwen integration | Verifies that a healthy repository remains unchanged and complete pytest validation succeeds. |

| `single\_validator\_bug` | Live Qwen integration | Verifies that Qwen can diagnose and minimally repair a simple source bug, then complete mandatory validation. |

| `edit\_failure\_recovery` | Live Qwen + fault injection | Forces the first replacement attempt to fail and verifies that the worker re-reads the current file before rebuilding the edit. |

| `hard\_search\_budget` | Deterministic scripted controller test | Verifies that the controller closes the search phase after the configured investigation budget is exhausted. |

| `validation\_tail` | Deterministic scripted controller test | Verifies that an exhausted base coding budget can continue only through the mandatory validation-only tail. |



\## Validation Tail Contract



After a successful source modification, the validation-only tail permits the remaining mandatory sequence:



```text

COMPLETE pytest

\-> git diff --check

\-> git diff

\-> git status

\-> finish(status="changes\_complete")

Investigation, file reads, directory listing, search, and source modification are not permitted to consume validation-tail rounds.

Running the Full Suite

From the project root:

.\\\\.venv\\\\Scripts\\\\python.exe .\\\\tests\\\\regression\\\\runner.py

Expected stable baseline:

6 passed, 0 failed

Test Layers

The suite deliberately contains two categories.

Deterministic Controller Regression

These tests do not depend on real model behavior:

\- bootstrap

\- hard\_search\_budget

\- validation\_tail

Scripted model responses are used where necessary to exercise exact controller state transitions.

Live Qwen Integration

These tests exercise the real local model:

\- healthy\_baseline

\- single\_validator\_bug

\- edit\_failure\_recovery

LM Studio must therefore be running and exposing the configured OpenAI-compatible endpoint and model.

Live-model action paths may vary between runs. Assertions focus on final correctness, safety, and controller validation rather than requiring an unnecessarily rigid reasoning path.

Isolation

Every scenario creates its own temporary Git repository.

The harness must not modify the production project workspace.

Temporary repositories are removed after each scenario, including Windows read-only Git objects.

Current Stable Baseline

The first complete regression baseline covers:

\- clean-project recognition

\- minimal bug repair

\- complete pytest enforcement

\- edit failure recovery

\- hard investigation/search budget

\- post-edit validation

\- Git validation

\- validation-tail continuation

\- controller finish gates

&#x20; """

path.parent.mkdir(

&#x20;   parents=True,

&#x20;   exist\_ok=True,

)

path.write\_text(

&#x20;   content,

&#x20;   encoding="utf-8",

)

print("REGRESSION README: PASS")

'@ | ..venv\\Scripts\\python.exe -



应该：



```text

REGRESSION README: PASS
