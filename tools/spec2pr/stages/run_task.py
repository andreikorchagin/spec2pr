"""Run task stage - uses Claude Code to implement a task."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


WORKER_PROMPT = Path(__file__).parent.parent / "prompts" / "worker.md"

# Model for task execution (sonnet 4.5 for cost/quality balance)
# Could add escalation back later: ["sonnet", "sonnet", "opus"]
MODEL_ESCALATION = ["sonnet"]


def run_task(task: dict) -> dict:
    """
    Use Claude Code to implement a task with retry logic.

    Retries failed tasks with escalating models: haiku → sonnet → opus.
    Previous failure context is included in retry prompts.

    Args:
        task: Task dict matching task.schema.json

    Returns:
        Result dict with success status, files_modified, summary, attempts
    """
    attempts = []

    for attempt_num, model in enumerate(MODEL_ESCALATION):
        print(f"  Attempt {attempt_num + 1}/{len(MODEL_ESCALATION)} with model: {model}", file=sys.stderr)

        # Build previous failures context for retries
        previous_failures = None
        if attempts:
            previous_failures = "\n\n".join([
                f"### Attempt {i+1} ({a['model']}) - FAILED\n{a.get('error', 'Unknown error')}"
                for i, a in enumerate(attempts)
            ])

        result = _execute_task(task, model, previous_failures)
        result["model"] = model
        result["attempt"] = attempt_num + 1
        # Store a copy in attempts to avoid circular reference when we add attempts to result
        attempts.append({**result})

        if result.get("success", False):
            result["attempts"] = attempts
            return result

        print(f"  Attempt {attempt_num + 1} failed: {result.get('error', 'unknown')[:100]}", file=sys.stderr)

    # All attempts failed - return last result with full attempt history
    # Create a new dict to avoid circular reference (attempts contains copies)
    final_result = {**attempts[-1], "attempts": attempts}
    return final_result


def _execute_task(task: dict, model: str, previous_failures: str | None = None) -> dict:
    """
    Execute a single attempt at implementing a task.

    Args:
        task: Task dict matching task.schema.json
        model: Model to use (haiku, sonnet, opus)
        previous_failures: Context from previous failed attempts

    Returns:
        Result dict with success status, files_modified, summary, error
    """
    # Read the worker prompt
    prompt = WORKER_PROMPT.read_text()

    # Build previous failures section if retrying
    retry_context = ""
    if previous_failures:
        retry_context = f"""
## Previous Attempts (FAILED)

The following attempts have already failed. Learn from these errors and try a different approach.

{previous_failures}

---

"""

    # Build the full prompt with task context
    full_prompt = f"""{prompt}
{retry_context}
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
            "--model", model,
            "-p", full_prompt,
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        # Log output for debugging
        print(f"Claude Code stderr: {result.stderr}", file=sys.stderr)
        print(f"Claude Code stdout: {result.stdout[:1000]}", file=sys.stderr)
        error_info = result.stderr or result.stdout[:500] or "unknown error (check logs)"
        return {
            "task_id": task["id"],
            "success": False,
            "error": f"Claude Code failed: {error_info}",
            "files_modified": [],
            "summary": "Task execution failed",
            "claude_output": result.stdout[:2000],
        }

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
    allowlist = task.get("files_allowlist", [])

    def is_allowed(filepath: str) -> bool:
        """Check if file is allowed (exact match or under allowed directory)."""
        for allowed in allowlist:
            if allowed.endswith("/"):
                # Directory prefix match
                if filepath.startswith(allowed):
                    return True
            else:
                # Exact file match
                if filepath == allowed:
                    return True
        return False

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

        # Check if file is in allowlist (supports directory prefixes)
        if is_allowed(f):
            files_modified.append(f)
        else:
            unauthorized_files.append(f)

    # Revert unauthorized file changes to keep the task focused
    if unauthorized_files:
        print(f"Reverting unauthorized file changes: {unauthorized_files}", file=sys.stderr)
        for f in unauthorized_files:
            subprocess.run(["git", "checkout", "--", f], capture_output=True)

    # Check LOC cap if specified
    loc_cap = task.get("loc_cap", 300)
    loc_count = _count_changed_lines(files_modified)

    if loc_count > loc_cap:
        # Revert all changes to allowed files
        print(f"LOC cap exceeded: {loc_count} lines > {loc_cap} limit", file=sys.stderr)
        for f in files_modified:
            subprocess.run(["git", "checkout", "--", f], capture_output=True)

        return {
            "task_id": task["id"],
            "success": False,
            "files_modified": [],
            "summary": f"Task exceeded LOC limit ({loc_count} > {loc_cap})",
            "error": f"Task exceeded LOC cap: {loc_count} lines changed, limit is {loc_cap}",
            "claude_output": result.stdout[:2000],
        }

    return {
        "task_id": task["id"],
        "success": True,
        "files_modified": files_modified,
        "summary": f"Implemented {task['title']}",
        "claude_output": result.stdout[:2000],  # Truncate for storage
    }


def _count_changed_lines(files: list[str]) -> int:
    """
    Count total lines added/deleted in the given files using git diff --stat.

    Args:
        files: List of file paths to count changes in

    Returns:
        Total number of lines changed (additions + deletions)
    """
    if not files:
        return 0

    result = subprocess.run(
        ["git", "diff", "--stat"] + files,
        capture_output=True,
        text=True,
    )

    total_lines = 0
    # Parse output format: "filename | X insertions(+), Y deletions(-)"
    for line in result.stdout.strip().split("\n"):
        if not line or line.startswith(" "):
            continue
        # Extract the number part after the pipe character
        if "|" in line:
            stats = line.split("|")[1].strip()
            # Parse insertions/deletions counts
            parts = stats.split()
            for i, part in enumerate(parts):
                if "insertion" in part or "deletion" in part:
                    # Get the number before the word
                    if i > 0:
                        try:
                            total_lines += int(parts[i - 1])
                        except ValueError:
                            pass

    return total_lines
