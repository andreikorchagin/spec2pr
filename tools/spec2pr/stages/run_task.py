"""Run task stage - uses Claude Code to implement a task."""

import json
import subprocess
from pathlib import Path


WORKER_PROMPT = Path(__file__).parent.parent / "prompts" / "worker.md"


def run_task(task: dict) -> dict:
    """
    Use Claude Code to implement a task.

    Args:
        task: Task dict matching task.schema.json

    Returns:
        Result dict with files_modified, summary
    """
    # Read the worker prompt
    prompt = WORKER_PROMPT.read_text()

    # Build the full prompt with task context
    full_prompt = f"""{prompt}

## Task to implement

```json
{json.dumps(task, indent=2)}
```

Implement this task now. Only modify files in the allowlist.
"""

    # Run Claude Code headlessly
    result = subprocess.run(
        [
            "claude",
            "--print",
            "--dangerously-skip-permissions",
            "--allowedTools", "Read,Edit,Bash",
            "-p", full_prompt,
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(f"Claude Code failed: {result.stderr}")

    # Get list of modified files
    git_result = subprocess.run(
        ["git", "diff", "--name-only"],
        capture_output=True,
        text=True,
    )
    files_modified = [f for f in git_result.stdout.strip().split("\n") if f]

    return {
        "task_id": task["id"],
        "files_modified": files_modified,
        "summary": f"Implemented {task['title']}",
        "claude_output": result.stdout[:2000],  # Truncate for storage
    }
