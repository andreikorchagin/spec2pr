"""Plan tasks stage - uses Claude Code to break spec into tasks."""

import json
import subprocess
from pathlib import Path


PLANNER_PROMPT = Path(__file__).parent.parent / "prompts" / "planner.md"


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

    # Build the full prompt with spec context
    full_prompt = f"""{prompt}

## Spec to plan

```json
{json.dumps(spec, indent=2)}
```

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
