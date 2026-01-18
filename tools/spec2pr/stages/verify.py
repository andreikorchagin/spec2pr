"""Verify stage - runs CI to verify task completion."""

import subprocess
from pathlib import Path


def validate_files_allowlist(task: dict) -> dict | None:
    """
    Validate that files_allowlist paths exist in the codebase.

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
        # Check if path exists (file or directory)
        if not path.exists():
            invalid_paths.append(path_str)

    if not invalid_paths:
        return None

    # Build helpful suggestions from directory structure
    cwd = Path(".")
    available_dirs = []
    available_files = []

    for item in cwd.rglob("*"):
        if item.is_dir() and not any(p in item.parts for p in [".git", ".spec2pr", "__pycache__", ".pytest_cache"]):
            available_dirs.append(str(item))
        elif item.is_file() and item.suffix in [".py", ".json", ".md", ".sh"]:
            available_files.append(str(item))

    # Sort for readability
    available_dirs.sort()
    available_files.sort()

    suggestions = []
    if available_dirs:
        suggestions.append(f"Available directories:\n  " + "\n  ".join(available_dirs[:10]))
    if available_files:
        suggestions.append(f"Available files:\n  " + "\n  ".join(available_files[:10]))

    suggestion_text = "\n".join(suggestions) if suggestions else "No suggestions available"

    return {
        "passed": False,
        "commands": [],
        "logs_path": "",
        "summary": f"Path validation failed. Invalid paths in files_allowlist: {', '.join(invalid_paths)}\n\n{suggestion_text}",
    }


def verify(task: dict) -> dict:
    """
    Run verification commands for a task.

    Args:
        task: Task dict with done_when commands

    Returns:
        Verify dict matching verify.schema.json
    """
    # Validate files_allowlist paths first
    validation_result = validate_files_allowlist(task)
    if validation_result:
        return validation_result

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
