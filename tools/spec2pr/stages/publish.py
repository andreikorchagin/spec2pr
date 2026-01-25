"""Publish stage - creates PRs or issues based on judgment."""

import re
import subprocess


def clean_title(title: str) -> str:
    """Remove spec2pr failure prefixes from issue titles for cleaner PR titles."""
    # Strip "[spec2pr] Failed: " prefix if present
    cleaned = re.sub(r'^\[spec2pr\]\s*Failed:\s*', '', title)
    return cleaned

from adapters.github import (
    delete_branch_if_exists,
    create_branch,
    commit_changes,
    rebase_on_main,
    push_branch,
    create_pr,
    create_issue,
)


def _build_review_section(accepted_tasks: list) -> str:
    """Build a code review summary section for the PR body."""
    review_items = []
    for item in accepted_tasks:
        result = item["result"]
        history = result.get("review_history", [])
        if not history:
            continue

        task_id = item["task"]["id"]
        review_items.append(f"\n### {task_id}\n")

        for review in history:
            iteration = review.get("iteration", "?")
            feedback = review.get("feedback", {})
            verdict = feedback.get("verdict", "unknown")
            issues = feedback.get("issues", [])
            summary = feedback.get("summary", "")

            if verdict == "approve":
                review_items.append(f"- **Iteration {iteration}**: ✅ Approved")
                if summary:
                    review_items.append(f"  - {summary}")
            else:
                review_items.append(f"- **Iteration {iteration}**: 🔄 Changes requested ({len(issues)} issue(s))")
                for issue in issues:
                    file_path = issue.get("file", "")
                    line = issue.get("line", 0)
                    severity = issue.get("severity", "info").upper()
                    message = issue.get("message", "")
                    review_items.append(f"  - {file_path}:{line} [{severity}] {message}")
                    if issue.get("suggestion"):
                        review_items.append(f"    → {issue.get('suggestion')}")

    if not review_items:
        return ""

    return "## Code Review Summary\n" + "\n".join(review_items) + "\n"


def publish_combined_pr(repo: str, spec: dict, accepted_tasks: list, issue_number: int) -> str:
    """
    Create a single PR for all accepted tasks.

    Args:
        repo: Repository in owner/repo format
        spec: Original spec dict
        accepted_tasks: List of {"task": task, "result": result, "verify": verify_result}
        issue_number: Original spec issue number to close

    Returns:
        PR URL
    """
    branch_name = f"spec2pr/issue-{issue_number}"

    # Clean up any existing remote branch from previous runs (keep local changes!)
    subprocess.run(
        ["git", "push", "origin", "--delete", branch_name],
        capture_output=True,  # Don't fail if branch doesn't exist
    )

    # Create branch from current state (preserving task changes in working dir)
    # First delete local branch if exists
    subprocess.run(["git", "branch", "-D", branch_name], capture_output=True)
    create_branch(branch_name)

    # Commit all current changes (from all tasks)
    all_files = []
    task_summaries = []
    for item in accepted_tasks:
        task = item["task"]
        result = item["result"]
        verify = item["verify"]

        files = result.get("files_modified", [])
        all_files.extend(files)

        status = "✅" if verify.get("passed", False) else "⚠️"
        task_summaries.append(f"- [{status}] **{task['id']}**: {task['title']}")

    # Single commit with all changes
    commit_msg = f"spec2pr: {spec['title']}\n\nTasks completed:\n" + "\n".join(
        f"- {item['task']['id']}: {item['task']['title']}" for item in accepted_tasks
    )
    has_changes = commit_changes(commit_msg)

    if not has_changes:
        import sys
        print("Warning: No changes to commit. Worker may not have made modifications.", file=sys.stderr)
        # Return early - can't create PR without changes
        return "(no changes to commit)"

    # Rebase on latest main
    if not rebase_on_main():
        import sys
        print("Warning: Rebase on main failed (conflicts). PR may have merge conflicts.", file=sys.stderr)

    push_branch(branch_name, force=True)

    # Build review section
    review_section = _build_review_section(accepted_tasks)

    # Build PR body
    body = f"""## Summary

{clean_title(spec['title'])}

Closes #{issue_number}

## Tasks Completed

{chr(10).join(task_summaries)}

## Files Modified

{', '.join(sorted(set(all_files))) if all_files else 'None'}

{review_section}
---
*This PR was created automatically by [spec2pr](https://github.com/andreikorchagin/spec2pr). Please review carefully before merging.*
"""

    return create_pr(repo, branch_name, f"spec2pr: {clean_title(spec['title'])}", body)


def publish_pr(repo: str, task: dict, result: dict, issue_number: int) -> str:
    """
    Create a PR for a successfully completed task.

    Args:
        repo: Repository in owner/repo format
        task: Task dict
        result: Result from run_task
        issue_number: Original spec issue number to close

    Returns:
        PR URL
    """
    branch_name = f"spec2pr/issue-{issue_number}/{task['id']}"

    # Clean up any existing branch from previous runs
    delete_branch_if_exists(branch_name)

    # Create branch, commit, rebase on latest main, and push
    create_branch(branch_name)
    commit_changes(f"spec2pr: {task['title']}\n\nTask: {task['id']}\nGoal: {task['goal']}")

    # Rebase on latest main to avoid conflicts with concurrent PRs
    if not rebase_on_main():
        # If rebase fails, push anyway - PR will show conflicts
        # but at least the work is preserved
        import sys
        print("Warning: Rebase on main failed (conflicts). PR may have merge conflicts.", file=sys.stderr)

    push_branch(branch_name, force=True)  # Force push after rebase

    # Create PR
    body = f"""## Summary

{task['goal']}

Closes #{issue_number}

## Task Details

- **Task ID**: {task['id']}
- **Files modified**: {', '.join(result.get('files_modified', []))}

## Automated PR

This PR was created automatically by [spec2pr](https://github.com/andreikorchagin/spec2pr).

Please review carefully before merging.
"""

    return create_pr(repo, branch_name, f"spec2pr: {task['title']}", body)


def publish_issue(repo: str, task: dict, judgment: dict) -> str:
    """
    Create an issue for a failed task.

    Args:
        repo: Repository in owner/repo format
        task: Task dict
        judgment: Judgment dict with rejection details

    Returns:
        Issue URL
    """
    body = f"""## Task Failed

**Task ID**: {task['id']}
**Task**: {task['title']}
**Goal**: {task['goal']}

## Blocking Issues

{chr(10).join(f'- {issue}' for issue in judgment.get('blocking_issues', ['Unknown']))}

## Judgment Details

- **Verdict**: {judgment['verdict']}
- **Confidence**: {judgment.get('confidence', 'unknown')}
- **Rationale**: {judgment.get('rationale', 'N/A')}

## Next Steps

Please review the blocking issues and either:
1. Fix the issues manually
2. Update the spec with more clarity
3. Re-run spec2pr after addressing the problems

---
*Automated issue created by spec2pr*
"""

    url = create_issue(
        repo,
        f"[spec2pr] Failed: {task['title']}",
        body,
    )
    return url or "(issue creation failed - check logs)"
