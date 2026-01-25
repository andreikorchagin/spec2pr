"""Plan tasks stage - uses Claude Code to break spec into tasks."""

import json
import subprocess
from pathlib import Path


PLANNER_PROMPT = Path(__file__).parent.parent / "prompts" / "planner.md"

# Directories to exclude from file tree
EXCLUDED_DIRS = {
    ".git", ".spec2pr", "__pycache__", "node_modules", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "venv", ".venv", "dist", "build", ".egg-info"
}

# File extensions to include
INCLUDED_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml",
    ".md", ".sh", ".c", ".h", ".go", ".rs", ".rb", ".java", ".kt",
    ".swift", ".css", ".scss", ".html", ".sql", ".toml", ".cfg", ".ini"
}


def build_dependency_graph(tasks: list[dict]) -> list[dict]:
    """
    Build dependency graph and return tasks in topological order.

    Args:
        tasks: List of task dicts, each may have 'depends_on' field

    Returns:
        List of tasks in execution order (topologically sorted)

    Raises:
        ValueError: If task IDs are invalid or dependencies reference missing tasks
    """
    # Build task ID to task mapping
    task_map = {}
    for task in tasks:
        task_id = task.get("id")
        if not task_id:
            raise ValueError(f"Task missing 'id' field: {task}")
        if task_id in task_map:
            raise ValueError(f"Duplicate task ID: {task_id}")
        task_map[task_id] = task

    # Validate all dependencies exist
    for task in tasks:
        depends_on = task.get("depends_on", [])
        for dep_id in depends_on:
            if dep_id not in task_map:
                raise ValueError(
                    f"Task {task['id']} depends on non-existent task {dep_id}"
                )

    # Topological sort using Kahn's algorithm
    # Calculate in-degree for each task
    in_degree = {task_id: 0 for task_id in task_map}
    for task in tasks:
        for dep_id in task.get("depends_on", []):
            in_degree[task["id"]] += 1

    # Queue of tasks with no dependencies
    queue = [task_id for task_id, degree in in_degree.items() if degree == 0]
    result = []

    while queue:
        # Sort queue for deterministic ordering
        queue.sort()
        task_id = queue.pop(0)
        result.append(task_map[task_id])

        # Reduce in-degree for tasks that depend on this one
        for other_task in tasks:
            if task_id in other_task.get("depends_on", []):
                in_degree[other_task["id"]] -= 1
                if in_degree[other_task["id"]] == 0:
                    queue.append(other_task["id"])

    return result


def discover_verification_options() -> list[str]:
    """
    Discover what verification/CI options are available in the repo.

    Returns:
        List of available verification commands.
    """
    options = []

    # Check for ci.sh
    if Path("ci.sh").exists():
        options.append("./ci.sh")

    # Check for Makefile with test target
    if Path("Makefile").exists():
        content = Path("Makefile").read_text()
        if "test:" in content or "check:" in content:
            options.append("make test")

    # Check for package.json with test script
    if Path("package.json").exists():
        import json
        try:
            pkg = json.loads(Path("package.json").read_text())
            if "scripts" in pkg and "test" in pkg["scripts"]:
                options.append("npm test")
        except (json.JSONDecodeError, KeyError):
            pass

    # Check for Python test frameworks
    if Path("pytest.ini").exists() or Path("pyproject.toml").exists() or Path("setup.py").exists():
        options.append("pytest")

    # Check for go.mod
    if Path("go.mod").exists():
        options.append("go test ./...")

    # Check for Cargo.toml
    if Path("Cargo.toml").exists():
        options.append("cargo test")

    return options


def discover_file_tree(max_files: int = 200) -> str:
    """
    Discover the file tree of the current repository.

    Returns:
        A string representation of the file tree for inclusion in prompts.
    """
    files = []
    dirs = set()

    for path in Path(".").rglob("*"):
        # Skip excluded directories
        if any(excluded in path.parts for excluded in EXCLUDED_DIRS):
            continue

        if path.is_file():
            # Include files with known extensions or known names
            if path.suffix in INCLUDED_EXTENSIONS or path.name in {
                "Makefile", "Dockerfile", "Gemfile", "Rakefile", "LICENSE", "README"
            }:
                files.append(str(path))
                # Track parent directories
                dirs.add(str(path.parent))

        if len(files) >= max_files:
            break

    files.sort()

    # Discover verification options
    verification_options = discover_verification_options()

    # Build tree representation
    tree_lines = ["## Repository File Tree", ""]

    # Add verification options section
    tree_lines.append("**Available Verification Commands:**")
    if verification_options:
        for opt in verification_options:
            tree_lines.append(f"- `{opt}`")
    else:
        tree_lines.append("- None detected (use simple checks like `python -c 'import ...'` or `git diff --stat`)")

    tree_lines.append("")
    tree_lines.append("**Directories:**")
    for d in sorted(dirs):
        if d != ".":
            tree_lines.append(f"- {d}/")

    tree_lines.append("")
    tree_lines.append("**Files:**")
    for f in files[:100]:  # Limit to 100 files in output
        tree_lines.append(f"- {f}")

    if len(files) > 100:
        tree_lines.append(f"- ... and {len(files) - 100} more files")

    return "\n".join(tree_lines)


def plan_tasks(spec: dict) -> list[dict]:
    """
    Use Claude Code to plan tasks from a spec.

    Args:
        spec: Parsed spec dict

    Returns:
        List of task dicts matching task.schema.json
    """
    # Read the planner prompt
    prompt = PLANNER_PROMPT.read_text()

    # Discover file tree for context
    file_tree = discover_file_tree()

    # Build the full prompt with spec context
    full_prompt = f"""{prompt}

{file_tree}

## Spec to plan

```json
{json.dumps(spec, indent=2)}
```

**IMPORTANT**: Use ONLY paths from the Repository File Tree above in `files_allowlist`. Do not guess or invent paths.

Output only valid JSON - an array of task objects.
"""

    # Run Claude Code headlessly
    result = subprocess.run(
        [
            "claude",
            "--print",
            "--dangerously-skip-permissions",
            "--allowedTools", "Read,Bash",
            "--output-format", "json",
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

    # Parse the response - extract JSON from output
    output = result.stdout.strip()

    # Handle JSON output format
    try:
        response = json.loads(output)
        # If it's the full response object, extract the result
        if isinstance(response, dict) and "result" in response:
            output = response["result"]
        elif isinstance(response, list):
            return response
    except json.JSONDecodeError:
        pass

    # Try to find JSON array in output
    import re
    json_match = re.search(r'\[[\s\S]*\]', output)
    if json_match:
        return json.loads(json_match.group())

    raise RuntimeError(f"Could not parse tasks from Claude output: {output[:500]}")
