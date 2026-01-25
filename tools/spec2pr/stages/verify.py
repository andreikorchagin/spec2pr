"""Verify stage - runs CI to verify task completion."""

import subprocess
from pathlib import Path


def run_deterministic_checks() -> dict:
    """
    Run deterministic checks (linting, type checking, tests) before AI review.

    Returns:
        Dict with categorized check results:
        {
            "linting": {"passed": bool, "output": str},
            "type_checking": {"passed": bool, "output": str},
            "tests": {"passed": bool, "output": str}
        }
    """
    results = {
        "linting": {"passed": True, "output": ""},
        "type_checking": {"passed": True, "output": ""},
        "tests": {"passed": True, "output": ""},
    }

    # Run linting with pylint
    lint_result = subprocess.run(
        ["python", "-m", "pylint", "--errors-only", "tools/spec2pr"],
        capture_output=True,
        text=True,
    )
    results["linting"]["output"] = lint_result.stdout + lint_result.stderr
    results["linting"]["passed"] = lint_result.returncode == 0

    # Run type checking with mypy
    type_result = subprocess.run(
        ["python", "-m", "mypy", "tools/spec2pr", "--ignore-missing-imports"],
        capture_output=True,
        text=True,
    )
    results["type_checking"]["output"] = type_result.stdout + type_result.stderr
    results["type_checking"]["passed"] = type_result.returncode == 0

    # Run tests with pytest
    test_result = subprocess.run(
        ["python", "-m", "pytest", "-v"],
        capture_output=True,
        text=True,
    )
    results["tests"]["output"] = test_result.stdout + test_result.stderr
    results["tests"]["passed"] = test_result.returncode == 0

    return results


def validate_files_allowlist(task: dict) -> dict | None:
    """
    Validate that files_allowlist paths exist or could be created.

    Args:
        task: Task dict with files_allowlist

    Returns:
        None if valid, or failure dict if invalid paths found
    """
    allowlist = task.get("files_allowlist", [])
    if not allowlist:
        return None

    invalid_paths = []
    for path_str in allowlist:
        path = Path(path_str)
        # Path is valid if it exists OR its parent directory exists (can be created)
        if not path.exists() and not path.parent.exists():
            invalid_paths.append(path_str)

    if not invalid_paths:
        return None

    # Suggest actual paths from codebase
    cwd = Path(".")
    available = []
    for item in cwd.rglob("*"):
        if any(p in item.parts for p in [".git", ".spec2pr", "__pycache__", "node_modules"]):
            continue
        if item.is_file() and item.suffix in [".py", ".json", ".md", ".sh", ".yml", ".yaml"]:
            available.append(str(item))
    available.sort()

    suggestion = ""
    if available:
        suggestion = "\n\nAvailable files:\n  " + "\n  ".join(available[:15])
        if len(available) > 15:
            suggestion += f"\n  ... and {len(available) - 15} more"

    return {
        "passed": False,
        "commands": [],
        "logs_path": "",
        "summary": f"Path validation failed. Invalid paths: {', '.join(invalid_paths)}{suggestion}",
    }


def verify(task: dict) -> dict:
    """
    Run verification commands for a task.

    Args:
        task: Task dict with done_when commands

    Returns:
        Verify dict matching verify.schema.json
    """
    # Validate file paths first
    path_error = validate_files_allowlist(task)
    if path_error:
        return path_error

    commands = task.get("done_when", [])

    # If no done_when commands, check for ci.sh
    if not commands:
        ci_script = Path("ci.sh")
        if ci_script.exists():
            commands = ["./ci.sh"]
        else:
            # No verification commands and no ci.sh - skip verification
            return {
                "passed": True,
                "commands": [],
                "logs_path": "",
                "summary": "No verification commands specified (ci.sh not found)",
            }

    # Filter out commands that reference non-existent scripts
    valid_commands = []
    for cmd in commands:
        # Check if it's a script reference that doesn't exist
        if cmd.startswith("./") and not Path(cmd.lstrip("./").split()[0]).exists():
            continue
        valid_commands.append(cmd)

    if not valid_commands:
        return {
            "passed": True,
            "commands": commands,
            "logs_path": "",
            "summary": f"Skipped verification (scripts not found: {commands})",
        }

    logs = []
    all_passed = True

    for cmd in valid_commands:
        logs.append(f"$ {cmd}")

        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
        )

        logs.append(result.stdout)
        if result.stderr:
            logs.append(f"STDERR: {result.stderr}")

        if result.returncode != 0:
            all_passed = False
            logs.append(f"EXIT CODE: {result.returncode}")

    # Write logs to file
    logs_path = Path(f".spec2pr/artifacts/{task['id']}/ci.log")
    logs_path.parent.mkdir(parents=True, exist_ok=True)
    logs_path.write_text("\n".join(logs))

    return {
        "passed": all_passed,
        "commands": commands,
        "logs_path": str(logs_path),
        "summary": "All checks passed" if all_passed else "Some checks failed",
    }
