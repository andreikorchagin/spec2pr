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
import sys
from pathlib import Path

from stages.load_spec import load_spec
from stages.plan_tasks import plan_tasks
from stages.run_task import run_task
from stages.verify import verify
from stages.judge import judge
from stages.publish import publish_pr, publish_issue


def write_json(path: Path, data: dict) -> None:
    """Write JSON data to file with pretty formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def read_json(path: Path) -> dict:
    """Read JSON data from file."""
    with open(path) as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(description="Run the spec2pr pipeline")
    parser.add_argument("--issue", type=int, required=True, help="GitHub issue number")
    parser.add_argument("--repo", type=str, default=None, help="Target repo (owner/repo)")
    parser.add_argument("--dry-run", action="store_true", help="Don't create PRs/issues")
    args = parser.parse_args()

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

    # Stage 3-6: Execute each task
    for i, task in enumerate(tasks):
        task_dir = artifacts_dir / task["id"]
        task_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n[3/6] Running task {task['id']}: {task['title']}...")
        result = run_task(task)
        write_json(task_dir / "result.json", result)

        print(f"[4/6] Verifying task {task['id']}...")
        verify_result = verify(task)
        write_json(task_dir / "verify.json", verify_result)

        print(f"[5/6] Judging task {task['id']}...")
        judgment = judge(task, result, verify_result)
        write_json(task_dir / "judgment.json", judgment)

        print(f"[6/6] Publishing task {task['id']}...")
        if args.dry_run:
            print(f"  [DRY RUN] Would {'create PR' if judgment['verdict'] == 'accept' else 'create issue'}")
        elif judgment["verdict"] == "reject":
            issue_url = publish_issue(repo, task, judgment)
            print(f"  Created issue: {issue_url}")
        else:
            pr_url = publish_pr(repo, task, result, issue_number)
            print(f"  Created PR: {pr_url}")

    print("\n=== spec2pr: Complete ===")


if __name__ == "__main__":
    main()
