"""Publish stage - creates PRs or issues based on judgment."""

from adapters.github import (
    create_branch,
    commit_changes,
    push_branch,
    create_pr,
    create_issue,
)


def publish_pr(repo: str, task: dict, result: dict) -> str:
    """
    Create a PR for a successfully completed task.

    Args:
        repo: Repository in owner/repo format
        task: Task dict
        result: Result from run_task

    Returns:
        PR URL
    """
    branch_name = f"spec2pr/{task['id']}"

    # Create branch, commit, and push
    create_branch(branch_name)
    commit_changes(f"spec2pr: {task['title']}\n\nTask: {task['id']}\nGoal: {task['goal']}")
    push_branch(branch_name)

    # Create PR
    body = f"""## Summary

{task['goal']}

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

    return create_issue(
        repo,
        f"[spec2pr] Failed: {task['title']}",
        body,
        labels=["spec2pr", "failed"],
    )
