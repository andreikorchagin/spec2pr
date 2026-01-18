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


def create_issue(repo: str, title: str, body: str, labels: list[str] = None) -> Optional[str]:
    """Create a new issue and return its URL.

    Returns None if issue creation fails (non-critical for failure reporting).
    """
    args = ["issue", "create", "--repo", repo, "--title", title, "--body", body]
    if labels:
        for label in labels:
            args.extend(["--label", label])
    try:
        output = run_gh(args)
        return output.strip()
    except RuntimeError as e:
        # Issue creation is non-critical - log and continue
        import sys
        print(f"Warning: Could not create issue: {e}", file=sys.stderr)
        return None


def get_pr_for_branch(repo: str, branch: str) -> Optional[str]:
    """Check if a PR exists for the given branch and return its URL."""
    try:
        output = run_gh([
            "pr", "view", branch,
            "--repo", repo,
            "--json", "url"
        ])
        data = json.loads(output)
        return data.get("url")
    except RuntimeError:
        return None


def create_pr(
    repo: str,
    branch: str,
    title: str,
    body: str,
    base: str = "main"
) -> str:
    """Create a pull request and return its URL.

    If PR creation fails but a PR already exists for the branch,
    returns the existing PR URL (handles GitHub API race conditions).
    """
    try:
        output = run_gh([
            "pr", "create",
            "--repo", repo,
            "--head", branch,
            "--base", base,
            "--title", title,
            "--body", body,
        ])
        return output.strip()
    except RuntimeError as e:
        # Check if PR was actually created despite the error
        existing_pr = get_pr_for_branch(repo, branch)
        if existing_pr:
            return existing_pr
        raise e


def delete_branch_if_exists(branch_name: str) -> None:
    """Delete local and remote branch if they exist."""
    # Delete local branch if exists
    subprocess.run(
        ["git", "branch", "-D", branch_name],
        capture_output=True,  # Don't fail if branch doesn't exist
    )
    # Delete remote branch if exists
    subprocess.run(
        ["git", "push", "origin", "--delete", branch_name],
        capture_output=True,  # Don't fail if branch doesn't exist
    )


def create_branch(branch_name: str) -> None:
    """Create and checkout a new branch."""
    subprocess.run(["git", "checkout", "-b", branch_name], check=True)


def commit_changes(message: str) -> bool:
    """Stage all changes and commit.

    Excludes:
    - .spec2pr/ directory (pipeline files, not part of target repo)
    - Compiled binaries (files without extensions that are executable)

    Returns:
        True if changes were committed, False if nothing to commit.
    """
    # Add all changes except .spec2pr directory
    subprocess.run(["git", "add", "-A", ":(exclude).spec2pr"], check=True)

    # Unstage any binary files (compiled executables)
    # These are typically files without extensions that got compiled
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        capture_output=True,
        text=True,
    )
    staged_files = [f for f in result.stdout.strip().split("\n") if f]

    for filename in staged_files:
        # Skip files without extensions that might be binaries
        # (but keep files like Makefile, Dockerfile, etc.)
        if "." not in filename.split("/")[-1] and filename not in [
            "Makefile", "Dockerfile", "Vagrantfile", "Gemfile", "Rakefile",
            "LICENSE", "README", "CHANGELOG", "AUTHORS", "CONTRIBUTING"
        ]:
            # Check if it's a binary file
            check = subprocess.run(
                ["file", "--mime", filename],
                capture_output=True,
                text=True,
            )
            if "executable" in check.stdout or "binary" in check.stdout:
                subprocess.run(["git", "reset", "HEAD", filename], capture_output=True)

    # Check if there are still staged changes
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        capture_output=True,
        text=True,
    )
    if not result.stdout.strip():
        return False  # Nothing to commit

    subprocess.run(["git", "commit", "-m", message], check=True)
    return True


def rebase_on_main() -> bool:
    """Fetch latest main and rebase current branch on it.

    Returns:
        True if rebase succeeded, False if conflicts occurred.
    """
    # Fetch latest main
    subprocess.run(["git", "fetch", "origin", "main"], check=True)

    # Attempt rebase
    result = subprocess.run(
        ["git", "rebase", "origin/main"],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        # Rebase failed (conflicts) - abort and return False
        subprocess.run(["git", "rebase", "--abort"], capture_output=True)
        return False

    return True


def push_branch(branch_name: str, force: bool = False) -> None:
    """Push branch to origin."""
    cmd = ["git", "push", "-u", "origin", branch_name]
    if force:
        cmd.insert(2, "--force-with-lease")
    subprocess.run(cmd, check=True)
