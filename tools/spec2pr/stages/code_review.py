"""Code review stage - uses Claude to review code changes against task spec."""

import json
import re
import subprocess
from pathlib import Path


REVIEWER_PROMPT = Path(__file__).parent.parent / "prompts" / "reviewer.md"


def run_code_review(task: dict, diff: str) -> dict:
    """
    Use Claude to review code changes against task specification.

    Args:
        task: Task dict with id, title, goal, files_allowlist, non_goals
        diff: Git diff output as string

    Returns:
        Review dict matching code_review.schema.json with structure:
        {
            "task": {...},
            "diff": "...",
            "feedback": {
                "verdict": "approve" | "request_changes",
                "issues": [...],
                "summary": "..."
            }
        }
    """
    # Read the reviewer prompt
    prompt = REVIEWER_PROMPT.read_text()

    # Build context for review
    context = {
        "task": task,
        "diff": diff,
    }

    full_prompt = f"""{prompt}

## Task Specification

```json
{json.dumps(task, indent=2)}
```

## Git Diff

```
{diff}
```

Review the changes and output only valid JSON matching this structure:
```json
{{
  "verdict": "approve",
  "issues": [],
  "summary": "Brief assessment"
}}
```
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
        print(f"Reviewer Claude stderr: {result.stderr}", file=sys.stderr)
        print(f"Reviewer Claude stdout: {result.stdout[:1000]}", file=sys.stderr)
        # Default to requesting changes if reviewer fails
        feedback = {
            "verdict": "request_changes",
            "issues": [{
                "severity": "blocking",
                "category": "correctness",
                "message": f"Code review failed: {result.stderr[:200] or 'timeout/error'}"
            }],
            "summary": "Code review process encountered an error",
        }
        return {
            "task": task,
            "diff": diff,
            "feedback": feedback,
        }

    # Parse feedback from output
    output = result.stdout.strip()
    feedback = _parse_feedback(output)

    return {
        "task": task,
        "diff": diff,
        "feedback": feedback,
    }


def _parse_feedback(output: str) -> dict:
    """
    Parse feedback JSON from Claude output.

    Args:
        output: Claude stdout

    Returns:
        Feedback dict with verdict, issues, summary
    """
    feedback = None

    try:
        response = json.loads(output)
        if isinstance(response, dict):
            # Handle wrapped response format
            if "result" in response:
                result_text = response["result"]
                # Strip markdown code block if present
                if result_text.startswith("```"):
                    result_text = re.sub(r'^```(?:json)?\n?', '', result_text)
                    result_text = re.sub(r'\n?```$', '', result_text)
                feedback = json.loads(result_text)
            # Handle direct feedback format
            elif "verdict" in response:
                feedback = response
    except json.JSONDecodeError:
        pass

    # Try to find JSON object in output
    if not feedback:
        json_match = re.search(r'\{[\s\S]*\}', output)
        if json_match:
            try:
                parsed = json.loads(json_match.group())
                if "verdict" in parsed:
                    feedback = parsed
            except json.JSONDecodeError:
                pass

    # Default to requesting changes if we can't parse
    if not feedback:
        return {
            "verdict": "request_changes",
            "issues": [{
                "severity": "blocking",
                "category": "correctness",
                "message": "Failed to parse review feedback"
            }],
            "summary": "Unable to complete code review",
        }

    # Fix inconsistency: "request_changes" with no issues should be "approve"
    if feedback.get("verdict") == "request_changes" and not feedback.get("issues"):
        feedback["verdict"] = "approve"
        feedback["summary"] = feedback.get("summary", "") + " (auto-approved: no issues specified)"

    return feedback
