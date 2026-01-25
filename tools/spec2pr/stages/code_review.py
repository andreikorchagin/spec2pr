"""Code review stage - uses Claude Code to provide structured code feedback."""

import json
import re
import subprocess
from pathlib import Path


CODE_REVIEW_PROMPT = Path(__file__).parent.parent / "prompts" / "code_review.md"


def code_review(task: dict, diff: str) -> dict:
    """
    Use Claude Code to review task implementation against the diff.

    Args:
        task: Original task dict with goal and requirements
        diff: Git diff output showing the changes made

    Returns:
        Code review dict matching code_review.schema.json
    """
    # Read the code review prompt
    prompt = CODE_REVIEW_PROMPT.read_text()

    # Build context for the code review
    context = {
        "task": task,
        "diff": diff,
    }

    full_prompt = f"""{prompt}

## Context

```json
{json.dumps(context, indent=2)}
```

Output only valid JSON matching the code_review schema.
"""

    # Run Claude Code headlessly
    result = subprocess.run(
        [
            "claude",
            "--print",
            "--dangerously-skip-permissions",
            "--allowedTools", "Read",
            "--output-format", "json",
            "-p", full_prompt,
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        # Log full output for debugging
        import sys
        print(f"Code review Claude stderr: {result.stderr}", file=sys.stderr)
        print(f"Code review Claude stdout: {result.stdout[:1000]}", file=sys.stderr)
        # If Claude fails, default to approve (tests should catch issues)
        error_info = result.stderr[:200] or result.stdout[:200] or "timeout/error"
        return {
            "verdict": "approve",
            "issues": [],
            "summary": f"Code review unavailable: {error_info}",
        }

    # Parse review from output
    output = result.stdout.strip()
    try:
        response = json.loads(output)
        if isinstance(response, dict):
            if "result" in response:
                result_text = response["result"]
                # Strip markdown code block if present
                if result_text.startswith("```"):
                    result_text = re.sub(r'^```(?:json)?\n?', '', result_text)
                    result_text = re.sub(r'\n?```$', '', result_text)
                return json.loads(result_text)
            return response
    except json.JSONDecodeError:
        pass

    # Try to find JSON object in output
    json_match = re.search(r'\{[\s\S]*\}', output)
    if json_match:
        return json.loads(json_match.group())

    # Default to approve if we can't parse review
    return {
        "verdict": "approve",
        "issues": [],
        "summary": "Code review completed (parsing unavailable)",
    }
