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
        # Log full output for debugging
        import sys
        print(f"Claude Code stderr: {result.stderr}", file=sys.stderr)
        print(f"Claude Code stdout: {result.stdout[:1000]}", file=sys.stderr)
        error_info = result.stderr or result.stdout[:500] or "unknown error (check logs)"
        raise RuntimeError(f"Claude Code failed: {error_info}")

    # Get list of modified files (excluding .spec2pr directory and binaries)
    git_result = subprocess.run(
        ["git", "diff", "--name-only"],
        capture_output=True,
        text=True,
    )

    # Known non-binary files without extensions
    KNOWN_TEXT_FILES = {
        "Makefile", "Dockerfile", "Vagrantfile", "Gemfile", "Rakefile",
        "LICENSE", "README", "CHANGELOG", "AUTHORS", "CONTRIBUTING"
    }

    # Get allowlist from task
    allowlist = set(task.get("files_allowlist", []))

    all_modified = []
    files_modified = []
    unauthorized_files = []

    for f in git_result.stdout.strip().split("\n"):
        if not f or f.startswith(".spec2pr"):
            continue

        # Check if file might be a binary (no extension and not a known text file)
        basename = f.split("/")[-1]
        if "." not in basename and basename not in KNOWN_TEXT_FILES:
            # Check if it's actually a binary
            check = subprocess.run(
                ["file", "--mime", f],
                capture_output=True,
                text=True,
            )
            if "executable" in check.stdout or "binary" in check.stdout:
                # Revert binary files silently
                subprocess.run(["git", "checkout", "--", f], capture_output=True)
                continue

        all_modified.append(f)

        # Check if file is in allowlist
        if f in allowlist:
            files_modified.append(f)
        else:
            unauthorized_files.append(f)

    # Revert unauthorized file changes to keep the task focused
    if unauthorized_files:
        import sys
        print(f"Reverting unauthorized file changes: {unauthorized_files}", file=sys.stderr)
        for f in unauthorized_files:
            subprocess.run(["git", "checkout", "--", f], capture_output=True)

    return {
        "task_id": task["id"],
        "files_modified": files_modified,
        "summary": f"Implemented {task['title']}",
        "claude_output": result.stdout[:2000],  # Truncate for storage
    }
