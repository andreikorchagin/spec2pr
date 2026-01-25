#!/usr/bin/env python3
"""
spec2pr CLI - Main entry point for the spec-to-PR pipeline.

Usage:
    python cli.py --issue 123
    python cli.py --issue 123 --repo owner/repo
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
import threading
import time

from stages.load_spec import load_spec
from stages.plan_tasks import plan_tasks, build_dependency_graph
from stages.run_task import run_task
from stages.code_review import run_code_review
from stages.verify import verify
from stages.judge import judge
from stages.publish import publish_pr, publish_issue, publish_combined_pr

__version__ = "0.1.0"


def write_json(path: Path, data: dict) -> None:
    """Write JSON data to file with pretty formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def read_json(path: Path) -> dict:
    """Read JSON data from file."""
    with open(path) as f:
        return json.load(f)


def check_status() -> tuple[dict, int]:
    """Check spec2pr setup and return (status_dict, exit_code)."""
    status = {
        "gh_cli": {"ok": False, "message": ""},
        "claude_cli": {"ok": False, "message": ""},
        "auth_token": {"ok": False, "message": ""},
        "ci_script": {"ok": False, "message": ""},
    }
    exit_code = 0

    # Check gh CLI
    if shutil.which("gh"):
        status["gh_cli"]["ok"] = True
        status["gh_cli"]["message"] = "gh CLI found"
    else:
        status["gh_cli"]["message"] = "gh CLI not found. Install from https://cli.github.com/"
        exit_code = 1

    # Check claude CLI
    if shutil.which("claude"):
        status["claude_cli"]["ok"] = True
        status["claude_cli"]["message"] = "claude CLI found"
    else:
        status["claude_cli"]["message"] = "claude CLI not found. Install with: npm install -g @anthropic-ai/claude-code"
        exit_code = 1

    # Check for API key or OAuth token
    if os.environ.get("ANTHROPIC_API_KEY"):
        status["auth_token"]["ok"] = True
        status["auth_token"]["message"] = "ANTHROPIC_API_KEY is set"
    elif os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        status["auth_token"]["ok"] = True
        status["auth_token"]["message"] = "CLAUDE_CODE_OAUTH_TOKEN is set"
    else:
        status["auth_token"]["message"] = "Neither ANTHROPIC_API_KEY nor CLAUDE_CODE_OAUTH_TOKEN is set"
        exit_code = 1

    # Check for ci.sh
    ci_script = Path("ci.sh")
    if ci_script.exists():
        if os.access(ci_script, os.X_OK):
            status["ci_script"]["ok"] = True
            status["ci_script"]["message"] = "ci.sh found and executable"
        else:
            status["ci_script"]["message"] = "ci.sh found but not executable. Run: chmod +x ci.sh"
            exit_code = 1
    else:
        status["ci_script"]["message"] = "ci.sh not found"
        exit_code = 1

    return status, exit_code


def validate_setup() -> list[str]:
    """Validate that the environment is properly configured. Returns list of errors."""
    errors = []

    # Check for required tools
    if not shutil.which("gh"):
        errors.append("gh CLI not found. Install from https://cli.github.com/")

    if not shutil.which("claude"):
        errors.append("claude CLI not found. Install with: npm install -g @anthropic-ai/claude-code")

    # Check for API key or OAuth token
    if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        errors.append("Neither ANTHROPIC_API_KEY nor CLAUDE_CODE_OAUTH_TOKEN is set")

    # Check for ci.sh (optional - warn but don't fail)
    ci_script = Path("ci.sh")
    if not ci_script.exists():
        print("Warning: ci.sh not found - task verification may be limited", file=sys.stderr)
    elif not os.access(ci_script, os.X_OK):
        print("Warning: ci.sh is not executable. Run: chmod +x ci.sh", file=sys.stderr)

    # Check git config
    result = subprocess.run(
        ["git", "config", "user.name"],
        capture_output=True,
        text=True,
    )
    if not result.stdout.strip():
        errors.append("git user.name not configured")

    return errors


def execute_task_with_stages(
    task: dict,
    artifacts_dir: Path,
    task_lock: threading.Lock
) -> tuple[dict, dict, dict, dict]:
    """
    Execute a single task including run, review, verify, and judge stages.

    Args:
        task: Task dict
        artifacts_dir: Base artifacts directory
        task_lock: Thread lock for serializing git operations

    Returns:
        Tuple of (task, result, verify_result, judgment)
    """
    task_dir = artifacts_dir / task["id"]
    task_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n[3/6] Running task {task['id']}: {task['title']}...")

    # Run task execution (may modify files)
    with task_lock:
        result = run_task(task)
        write_json(task_dir / "result.json", result)

    # Log retry information
    attempts = result.get("attempts", [])
    if len(attempts) > 1:
        print(f"  Completed after {len(attempts)} attempt(s), final model: {result.get('model', 'unknown')}")
    if not result.get("success", True):
        print(f"  Warning: All attempts failed", file=sys.stderr)

    print(f"[4/6] Reviewing code changes for task {task['id']}...")
    # Get git diff after task execution
    with task_lock:
        diff_result = subprocess.run(
            ["git", "diff", "--cached"],
            capture_output=True,
            text=True,
        )
    diff = diff_result.stdout
    review_result = run_code_review(task, diff)
    write_json(task_dir / "review.json", review_result)

    # Log review verdict
    review_verdict = review_result.get("feedback", {}).get("verdict", "unknown")
    if review_verdict == "approve":
        print(f"  ✓ Code review approved")
    else:
        print(f"  ⚠ Code review requested changes")

    print(f"[5/6] Verifying task {task['id']}...")
    with task_lock:
        verify_result = verify(task)
        write_json(task_dir / "verify.json", verify_result)

    print(f"[6/6] Judging task {task['id']}...")
    judgment = judge(task, result, verify_result)
    write_json(task_dir / "judgment.json", judgment)

    # Validate judgment has required fields
    verdict = judgment.get("verdict")
    if verdict not in ("accept", "reject"):
        print(f"  Warning: Invalid judgment (verdict={verdict}), treating as reject", file=sys.stderr)
        judgment["verdict"] = "reject"
        judgment["blocking_issues"] = judgment.get("blocking_issues", []) + [
            f"Judge returned invalid verdict: {verdict}"
        ]
        verdict = "reject"

    if verdict == "accept":
        print(f"  ✓ Task accepted")
    else:
        print(f"  ✗ Task rejected: {judgment.get('rationale', 'unknown reason')[:100]}")

    return task, result, verify_result, judgment


def execute_tasks_parallel(
    tasks: list[dict],
    artifacts_dir: Path,
    max_workers: int = 4
) -> tuple[list[dict], list[dict]]:
    """
    Execute tasks in parallel while respecting dependency order.

    Args:
        tasks: List of tasks (already sorted in dependency order)
        artifacts_dir: Artifacts directory for storing results
        max_workers: Maximum number of parallel workers

    Returns:
        Tuple of (accepted_tasks, rejected_tasks)
    """
    accepted_tasks = []
    rejected_tasks = []
    task_lock = threading.Lock()

    # Build dependency map for quick lookup
    task_map = {task["id"]: task for task in tasks}
    completed = set()
    failed = set()

    # Group tasks by dependency level
    ready_queue = []
    waiting = []

    for task in tasks:
        deps = task.get("depends_on", [])
        if not deps:
            ready_queue.append(task)
        else:
            waiting.append(task)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}

        # Submit initial ready tasks
        for task in ready_queue:
            future = executor.submit(execute_task_with_stages, task, artifacts_dir, task_lock)
            futures[future] = task

        # Process completed tasks and submit new ones
        while futures:
            # Wait for next completed future
            done_futures = []
            for future in as_completed(futures):
                done_futures.append(future)
                break  # Process one at a time to update waiting queue

            for future in done_futures:
                task = futures.pop(future)
                task_obj, result, verify_result, judgment = future.result()

                if judgment["verdict"] == "accept":
                    accepted_tasks.append({"task": task_obj, "result": result, "verify": verify_result})
                    completed.add(task_obj["id"])
                else:
                    rejected_tasks.append({"task": task_obj, "judgment": judgment})
                    failed.add(task_obj["id"])

            # Check if any waiting tasks are now ready
            newly_ready = []
            still_waiting = []

            for waiting_task in waiting:
                deps = waiting_task.get("depends_on", [])
                # Task is ready if all deps are completed (skip if any dep failed)
                deps_failed = any(dep_id in failed for dep_id in deps)
                deps_ready = all(dep_id in completed for dep_id in deps)

                if deps_failed:
                    # Dependency failed, skip this task
                    print(f"  Skipping {waiting_task['id']} - dependency failed")
                    rejected_tasks.append({
                        "task": waiting_task,
                        "judgment": {
                            "verdict": "reject",
                            "rationale": "Skipped due to failed dependency",
                            "blocking_issues": ["Dependency task failed"]
                        }
                    })
                    failed.add(waiting_task["id"])
                elif deps_ready:
                    newly_ready.append(waiting_task)
                else:
                    still_waiting.append(waiting_task)

            waiting = still_waiting

            # Submit newly ready tasks
            for task in newly_ready:
                future = executor.submit(execute_task_with_stages, task, artifacts_dir, task_lock)
                futures[future] = task

    return accepted_tasks, rejected_tasks


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add CLI arguments to the parser."""
    parser.add_argument("--version", action="version", version=f"spec2pr {__version__}")
    parser.add_argument("--status", action="store_true", help="Check spec2pr setup configuration")
    parser.add_argument("--issue", type=int, default=None, help="GitHub issue number")
    parser.add_argument("--issues", type=str, default=None, help="Comma-separated list of issue numbers for batch processing")
    parser.add_argument("--repo", type=str, default=None, help="Target repo (owner/repo)")
    parser.add_argument("--dry-run", action="store_true", help="Run pipeline without creating PRs or issues (for testing)")


def process_single_issue(repo: str, issue_number: int, dry_run: bool = False) -> dict:
    """
    Process a single issue through the spec2pr pipeline.

    Returns:
        Dict with keys: issue, branch, pr_url, status (success/failure)
    """
    artifacts_dir = Path(f".spec2pr/artifacts/{issue_number}")
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    start_time_dt = datetime.now()
    start_time = start_time_dt.strftime("%Y-%m-%d %H:%M:%S")
    print(f"Pipeline started at: {start_time}")
    print(f"=== spec2pr: Processing {repo}#{issue_number} ===")

    result = {
        "issue": issue_number,
        "branch": f"spec2pr/issue-{issue_number}",
        "pr_url": None,
        "status": "failure"
    }

    try:
        # Stage 1: Load spec from GitHub issue
        print("\n[1/7] Loading spec from issue...")
        spec = load_spec(repo, issue_number)
        write_json(artifacts_dir / "spec.json", spec)
        print(f"  Spec: {spec['title']}")

        # Stage 2: Plan tasks (Claude Code headless)
        print("\n[2/7] Planning tasks...")
        tasks = plan_tasks(spec)
        write_json(artifacts_dir / "tasks.json", {"tasks": tasks})
        print(f"  Planned {len(tasks)} task(s)")

        # Build dependency graph and get execution order
        try:
            ordered_tasks = build_dependency_graph(tasks)
            print(f"  Tasks ordered by dependencies")
        except ValueError as e:
            print(f"Error in task dependencies: {e}", file=sys.stderr)
            return result

        # Stage 3-6: Execute tasks in parallel (respecting dependencies)
        print("\n[3-6/7] Executing tasks in parallel...")
        accepted_tasks, rejected_tasks = execute_tasks_parallel(ordered_tasks, artifacts_dir)

        # Build executed_tasks summary
        executed_tasks = []
        for at in accepted_tasks:
            executed_tasks.append({
                "id": at["task"]["id"],
                "title": at["task"]["title"],
                "status": "accept"
            })
        for rt in rejected_tasks:
            executed_tasks.append({
                "id": rt["task"]["id"],
                "title": rt["task"]["title"],
                "status": "reject"
            })

        # Stage 7: Publish results
        print("\n[7/7] Publishing results...")

        if dry_run:
            if accepted_tasks:
                print(f"  [DRY RUN] Would create PR with {len(accepted_tasks)} task(s)")
            for rt in rejected_tasks:
                print(f"  [DRY RUN] Would create issue for rejected task {rt['task']['id']}")
        else:
            # Create single PR for all accepted tasks
            if accepted_tasks:
                pr_url = publish_combined_pr(repo, spec, accepted_tasks, issue_number)
                print(f"  Created PR: {pr_url}")
                result["pr_url"] = pr_url
            else:
                print("  No tasks accepted - skipping PR creation")

            # Create issues for rejected tasks
            for rt in rejected_tasks:
                issue_url = publish_issue(repo, rt["task"], rt["judgment"])
                print(f"  Created issue for {rt['task']['id']}: {issue_url}")

        # Generate and print summary
        end_time_dt = datetime.now()
        end_time = end_time_dt.strftime("%Y-%m-%d %H:%M:%S")
        final_status = "success" if accepted_tasks or not rejected_tasks else "partial"
        duration = int((end_time_dt - start_time_dt).total_seconds())

        summary = {
            "issue": issue_number,
            "repo": repo,
            "start_time": start_time,
            "end_time": end_time,
            "duration_seconds": duration,
            "final_status": final_status,
            "tasks_planned": len(tasks),
            "tasks_executed": len(executed_tasks),
            "tasks_accepted": len(accepted_tasks),
            "tasks_rejected": len(rejected_tasks),
            "executed_tasks": executed_tasks
        }

        # Write summary to file
        summary_file = Path(f".spec2pr/artifacts/{issue_number}/summary.json")
        write_json(summary_file, summary)

        # Print human-readable summary
        print("\n=== Pipeline Summary ===")
        print(f"Issue: {repo}#{issue_number}")
        print(f"Start: {start_time}")
        print(f"End: {end_time}")
        print(f"Duration: {duration}s")
        print(f"Status: {final_status}")
        print(f"Tasks: {len(accepted_tasks)} accepted, {len(rejected_tasks)} rejected out of {len(executed_tasks)} executed")
        print("=== spec2pr: Complete ===")

        result["status"] = "success"
        return result

    except Exception as e:
        print(f"Error processing issue {issue_number}: {e}", file=sys.stderr)
        return result


def process_batch(repo: str, issue_numbers: list[int], dry_run: bool = False) -> list[dict]:
    """
    Process multiple issues sequentially through the spec2pr pipeline.

    Args:
        repo: Target repository (owner/repo)
        issue_numbers: List of issue numbers to process
        dry_run: If True, don't create PRs or issues

    Returns:
        List of result dicts, each with keys: issue, branch, pr_url, status
    """
    batch_results = []

    for issue_number in issue_numbers:
        result = process_single_issue(repo, issue_number, dry_run)
        batch_results.append(result)

    return batch_results


def main():
    parser = argparse.ArgumentParser(description="Run the spec2pr pipeline")
    add_arguments(parser)
    args = parser.parse_args()

    # Handle --status flag
    if args.status:
        status, exit_code = check_status()
        for component, result in status.items():
            status_symbol = "✓" if result["ok"] else "✗"
            print(f"{status_symbol} {component}: {result['message']}")
        sys.exit(exit_code)

    # Determine repo from environment if not provided
    repo = args.repo or os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        print("Error: --repo required or GITHUB_REPOSITORY must be set", file=sys.stderr)
        sys.exit(1)

    # Collect issue numbers from different sources
    issue_numbers = []

    if args.issues:
        # Parse comma-separated issue numbers
        try:
            issue_numbers = [int(n.strip()) for n in args.issues.split(",")]
        except ValueError:
            print("Error: --issues must be comma-separated integers (e.g., '1,2,3')", file=sys.stderr)
            sys.exit(1)
    elif args.issue is not None:
        issue_numbers = [args.issue]
    else:
        parser.error("One of --issue or --issues is required (or use --status to check configuration)")

    # Validate setup
    errors = validate_setup()
    if errors:
        print("Setup validation failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        sys.exit(1)

    # Process issues using batch orchestrator
    process_batch(repo, issue_numbers, args.dry_run)


if __name__ == "__main__":
    main()
