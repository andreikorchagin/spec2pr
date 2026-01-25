"""Run task stage - uses Claude Code to implement a task."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from stages.code_review import code_review
from stages.verify import verify


WORKER_PROMPT = Path(__file__).parent.parent / "prompts" / "worker.md"

# Model for task execution (sonnet 4.5 for cost/quality balance)
# Could add escalation back later: ["sonnet", "sonnet", "opus"]
MODEL_ESCALATION = ["sonnet"]

# Max iterations of verify+code-review loop
MAX_ITERATIONS = 3


def run_task(task: dict) -> dict:
    """
    Use Claude Code to implement a task with retry and iteration logic.

    Retries failed tasks with escalating models: haiku → sonnet → opus.
    After successful implementation, runs verify and code-review loop up to 3 times.

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
            # Run verification and code-review iteration loop
            result = _iterate_with_feedback(task, result, attempts)
            result["attempts"] = attempts
            return result

        print(f"  Attempt {attempt_num + 1} failed: {result.get('error', 'unknown')[:100]}", file=sys.stderr)

    # All attempts failed - return last result with full attempt history
    # Create a new dict to avoid circular reference (attempts contains copies)
    final_result = {**attempts[-1], "attempts": attempts}
    return final_result


def _iterate_with_feedback(task: dict, result: dict, attempts: list) -> dict:
    """
    Run verify and code-review loop with up to MAX_ITERATIONS iterations.

    If code-review requests changes, gather diff and provide feedback for fixes.

    Args:
        task: Task dict
        result: Initial execution result
        attempts: List of previous attempts for context

    Returns:
        Result dict after iteration (success or failure after max iterations)
    """
    review_history = []

    for iteration in range(1, MAX_ITERATIONS + 1):
        print(f"  Iteration {iteration}/{MAX_ITERATIONS}: Running verify + code-review...", file=sys.stderr)

        # Run verification
        verify_result = verify(task)
        if not verify_result.get("passed", False):
            print(f"    Verify failed, skipping code-review this iteration", file=sys.stderr)
            continue

        # Get git diff for code-review
        diff_result = subprocess.run(
            ["git", "diff", "HEAD"],
            capture_output=True,
            text=True,
        )
        diff = diff_result.stdout

        # Run code-review
        review = code_review(task, diff)
        review["iteration"] = iteration

        if review.get("verdict") == "approve":
            print(f"    Code-review approved", file=sys.stderr)
            review_history.append(review)
            result["review_history"] = review_history
            return result

        # Code-review requested changes
        issues = review.get("issues", [])
        print(f"    Code-review requested changes ({len(issues)} issue(s))", file=sys.stderr)
        review_history.append(review)

        if iteration < MAX_ITERATIONS:
            # Try to fix issues
            feedback = _format_code_review_feedback(review)
            print(f"    Attempting fixes based on feedback...", file=sys.stderr)

            fix_result = _execute_task_with_feedback(task, result["model"], feedback)
            if not fix_result.get("success", False):
                print(f"    Failed to apply fixes, giving up", file=sys.stderr)
                result["review_history"] = review_history
                return result
            result = fix_result

    # Max iterations reached - return final result
    print(f"  Reached max iterations, finalizing", file=sys.stderr)
    result["review_history"] = review_history
    return result


def _format_code_review_feedback(review: dict) -> str:
    """Format code review issues into feedback text."""
    issues = review.get("issues", [])
    if not issues:
        return ""

    feedback_lines = ["## Code Review Feedback\n"]
    for issue in issues:
        feedback_lines.append(f"- {issue.get('file', 'unknown')}:{issue.get('line', 0)} "
                            f"[{issue.get('severity', 'info').upper()}] {issue.get('message', '')}")
        if issue.get("suggestion"):
            feedback_lines.append(f"  Suggestion: {issue.get('suggestion')}")

    return "\n".join(feedback_lines)


def _execute_task_with_feedback(task: dict, model: str, feedback: str) -> dict:
    """
    Execute a task with code-review feedback context.

    Args:
        task: Task dict
        model: Model to use
        feedback: Formatted code-review feedback

    Returns:
        Result dict
    """
    prompt = WORKER_PROMPT.read_text()

    feedback_context = ""
    if feedback:
        feedback_context = f"""
## Code Review Feedback from Previous Iteration

{feedback}

Please address these issues in your next attempt.

---

"""

    full_prompt = f"""{prompt}
{feedback_context}
## Task to implement

```json
{json.dumps(task, indent=2)}
```

Implement this task now. Only modify files in the allowlist.
"""

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
        print(f"Claude Code stderr: {result.stderr}", file=sys.stderr)
        error_info = result.stderr or result.stdout[:500] or "unknown error"
        return {
            "success": False,
            "error": f"Claude Code failed: {error_info}",
            "files_modified": [],
            "summary": "Feedback iteration failed",
        }

    # Get list of modified files
    git_result = subprocess.run(
        ["git", "diff", "--name-only"],
        capture_output=True,
        text=True,
    )

    KNOWN_TEXT_FILES = {
        "Makefile", "Dockerfile", "Vagrantfile", "Gemfile", "Rakefile",
        "LICENSE", "README", "CHANGELOG", "AUTHORS", "CONTRIBUTING"
    }

    allowlist = task.get("files_allowlist", [])

    def is_allowed(filepath: str) -> bool:
        """Check if file is allowed (exact match or under allowed directory)."""
        for allowed in allowlist:
            if allowed.endswith("/"):
                if filepath.startswith(allowed):
                    return True
            else:
                if filepath == allowed:
                    return True
        return False

    files_modified = []
    unauthorized_files = []

    for f in git_result.stdout.strip().split("\n"):
        if not f or f.startswith(".spec2pr"):
            continue

        basename = f.split("/")[-1]
        if "." not in basename and basename not in KNOWN_TEXT_FILES:
            check = subprocess.run(
                ["file", "--mime", f],
                capture_output=True,
                text=True,
            )
            if "executable" in check.stdout or "binary" in check.stdout:
                subprocess.run(["git", "checkout", "--", f], capture_output=True)
                continue

        if is_allowed(f):
            files_modified.append(f)
        else:
            unauthorized_files.append(f)

    if unauthorized_files:
        print(f"Reverting unauthorized file changes: {unauthorized_files}", file=sys.stderr)
        for f in unauthorized_files:
            subprocess.run(["git", "checkout", "--", f], capture_output=True)

    return {
        "success": True,
        "files_modified": files_modified,
        "summary": "Applied feedback fixes",
    }


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
