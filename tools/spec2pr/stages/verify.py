"""Verify stage - runs CI to verify task completion."""

import subprocess
from pathlib import Path


def verify(task: dict) -> dict:
    """
    Run verification commands for a task.

    Args:
        task: Task dict with done_when commands

    Returns:
        Verify dict matching verify.schema.json
    """
    commands = task.get("done_when", ["./ci.sh"])
    logs = []
    all_passed = True

    for cmd in commands:
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
