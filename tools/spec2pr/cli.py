#!/usr/bin/env python3
"""
spec2pr CLI - Main entry point for the spec-to-PR pipeline.

Usage:
    python cli.py --issue 123
    python cli.py --issue 123 --repo owner/repo
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from stages.load_spec import load_spec
from stages.plan_tasks import plan_tasks
from stages.run_task import run_task
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


def main():
    parser = argparse.ArgumentParser(description="Run the spec2pr pipeline")
    parser.add_argument("--version", action="version", version=f"spec2pr {__version__}")
    parser.add_argument("--issue", type=int, required=True, help="GitHub issue number")
    parser.add_argument("--repo", type=str, default=None, help="Target repo (owner/repo)")
    parser.add_argument("--dry-run", action="store_true", help="Run pipeline without creating PRs or issues (for testing)")
    args = parser.parse_args()

    # Validate setup
    errors = validate_setup()
    if errors:
        print("Setup validation failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        sys.exit(1)

    # Determine repo from environment if not provided
    repo = args.repo or os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        print("Error: --repo required or GITHUB_REPOSITORY must be set", file=sys.stderr)
        sys.exit(1)

    issue_number = args.issue
    artifacts_dir = Path(f".spec2pr/artifacts/{issue_number}")
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    print(f"=== spec2pr: Processing {repo}#{issue_number} ===")

    # Stage 1: Load spec from GitHub issue
    print("\n[1/6] Loading spec from issue...")
    spec = load_spec(repo, issue_number)
    write_json(artifacts_dir / "spec.json", spec)
    print(f"  Spec: {spec['title']}")

    # Stage 2: Plan tasks (Claude Code headless)
    print("\n[2/6] Planning tasks...")
    tasks = plan_tasks(spec)
    write_json(artifacts_dir / "tasks.json", {"tasks": tasks})
    print(f"  Planned {len(tasks)} task(s)")

    # Track results for combined PR
    accepted_tasks = []
    rejected_tasks = []

    # Stage 3-5: Execute each task (no publishing yet)
    for i, task in enumerate(tasks):
        task_dir = artifacts_dir / task["id"]
        task_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n[3/5] Running task {task['id']}: {task['title']}...")
        result = run_task(task)
        write_json(task_dir / "result.json", result)

        print(f"[4/5] Verifying task {task['id']}...")
        verify_result = verify(task)
        write_json(task_dir / "verify.json", verify_result)

        print(f"[5/5] Judging task {task['id']}...")
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
            accepted_tasks.append({"task": task, "result": result, "verify": verify_result})
            print(f"  ✓ Task accepted")
        else:
            rejected_tasks.append({"task": task, "judgment": judgment})
            print(f"  ✗ Task rejected: {judgment.get('rationale', 'unknown reason')[:100]}")

    # Stage 6: Publish results
    print("\n[6/6] Publishing results...")

    if args.dry_run:
        if accepted_tasks:
            print(f"  [DRY RUN] Would create PR with {len(accepted_tasks)} task(s)")
        for rt in rejected_tasks:
            print(f"  [DRY RUN] Would create issue for rejected task {rt['task']['id']}")
    else:
        # Create single PR for all accepted tasks
        if accepted_tasks:
            pr_url = publish_combined_pr(repo, spec, accepted_tasks, issue_number)
            print(f"  Created PR: {pr_url}")
        else:
            print("  No tasks accepted - skipping PR creation")

        # Create issues for rejected tasks
        for rt in rejected_tasks:
            issue_url = publish_issue(repo, rt["task"], rt["judgment"])
            print(f"  Created issue for {rt['task']['id']}: {issue_url}")

    print("\n=== spec2pr: Complete ===")


if __name__ == "__main__":
    main()
