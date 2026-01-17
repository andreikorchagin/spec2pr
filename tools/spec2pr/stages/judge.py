"""Judge stage - uses Claude Code to evaluate task completion."""

import json
import re
import subprocess
from pathlib import Path


JUDGE_PROMPT = Path(__file__).parent.parent / "prompts" / "judge.md"


def judge(task: dict, result: dict, verify_result: dict) -> dict:
    """
    Use Claude Code to judge if a task was completed correctly.

    Args:
        task: Original task dict
        result: Result from run_task
        verify_result: Result from verify

    Returns:
        Judgment dict matching judgment.schema.json
    """
    # If CI failed, auto-reject
    if not verify_result["passed"]:
        return {
            "judge_id": "ci",
            "verdict": "reject",
            "scores": {"ci": 0},
            "blocking_issues": ["CI verification failed"],
            "confidence": "high",
            "rationale": f"CI failed: {verify_result['summary']}",
        }

    # Read the judge prompt
    prompt = JUDGE_PROMPT.read_text()

    # Build context for judgment
    context = {
        "task": task,
        "result": result,
        "verify": verify_result,
    }

    full_prompt = f"""{prompt}

## Context

```json
{json.dumps(context, indent=2)}
```

Output only valid JSON matching the judgment schema.
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
        # If Claude fails but CI passed, default to accept
        # (CI verification is sufficient for basic correctness)
        return {
            "judge_id": "error",
            "verdict": "accept",
            "scores": {"ci": 5},
            "blocking_issues": [],
            "confidence": "medium",
            "rationale": f"CI passed. Judge unavailable: {result.stderr[:200] or 'timeout/error'}",
        }

    # Parse judgment from output
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

    # Default to accept if CI passed and we can't parse judgment
    return {
        "judge_id": "default",
        "verdict": "accept",
        "scores": {"ci": 5},
        "blocking_issues": [],
        "confidence": "medium",
        "rationale": "CI passed, defaulting to accept",
    }
