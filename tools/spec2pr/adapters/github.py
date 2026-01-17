"""GitHub API adapter using gh CLI."""

import json
import subprocess
from typing import Optional


def run_gh(args: list[str], input_data: Optional[str] = None) -> str:
    """Run a gh CLI command and return stdout."""
    result = subprocess.run(
        ["gh"] + args,
        capture_output=True,
        text=True,
        input=input_data,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh command failed: {result.stderr}")
    return result.stdout


def get_issue(repo: str, issue_number: int) -> dict:
    """Fetch issue data from GitHub."""
    output = run_gh([
        "issue", "view", str(issue_number),
        "--repo", repo,
        "--json", "title,body,labels"
    ])
    return json.loads(output)


def create_issue(repo: str, title: str, body: str, labels: list[str] = None) -> str:
    """Create a new issue and return its URL."""
    args = ["issue", "create", "--repo", repo, "--title", title, "--body", body]
    if labels:
        for label in labels:
            args.extend(["--label", label])
    output = run_gh(args)
    return output.strip()


def create_pr(
    repo: str,
    branch: str,
    title: str,
    body: str,
    base: str = "main"
) -> str:
    """Create a pull request and return its URL."""
    output = run_gh([
        "pr", "create",
        "--repo", repo,
        "--head", branch,
        "--base", base,
        "--title", title,
        "--body", body,
    ])
    return output.strip()


def create_branch(branch_name: str) -> None:
    """Create and checkout a new branch."""
    subprocess.run(["git", "checkout", "-b", branch_name], check=True)


def commit_changes(message: str) -> None:
    """Stage all changes and commit."""
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-m", message], check=True)


def push_branch(branch_name: str) -> None:
    """Push branch to origin."""
    subprocess.run(["git", "push", "-u", "origin", branch_name], check=True)
