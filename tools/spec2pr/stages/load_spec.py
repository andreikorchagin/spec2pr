"""Load spec stage - parses GitHub issue into structured spec."""

import re
from adapters.github import get_issue


def parse_sections(body: str) -> dict[str, str]:
    """Parse markdown sections from issue body."""
    sections = {}
    current_section = "overview"
    current_content = []

    for line in body.split("\n"):
        # Check for heading
        heading_match = re.match(r"^#{1,3}\s+(.+)$", line)
        if heading_match:
            # Save previous section
            if current_content:
                sections[current_section] = "\n".join(current_content).strip()
            # Start new section
            current_section = heading_match.group(1).lower().strip()
            current_content = []
        else:
            current_content.append(line)

    # Save final section
    if current_content:
        sections[current_section] = "\n".join(current_content).strip()

    return sections


def parse_list(text: str) -> list[str]:
    """Parse markdown list items."""
    items = []
    for line in text.split("\n"):
        # Match list items (-, *, or numbered)
        match = re.match(r"^[\-\*]\s+(.+)$|^\d+\.\s+(.+)$", line.strip())
        if match:
            items.append(match.group(1) or match.group(2))
    return items


def load_spec(repo: str, issue_number: int) -> dict:
    """
    Load and parse a GitHub issue into a structured spec.

    Supports both structured markdown (with sections like ## Acceptance Criteria)
    and natural language specs (plain text descriptions).

    Args:
        repo: Repository in owner/repo format
        issue_number: GitHub issue number

    Returns:
        Spec dict - either structured or with raw_content for natural language
    """
    issue = get_issue(repo, issue_number)
    body = issue["body"] or ""
    sections = parse_sections(body)

    # Extract acceptance criteria
    acceptance = []
    for key in ["acceptance", "acceptance criteria", "criteria", "requirements"]:
        if key in sections:
            acceptance = parse_list(sections[key])
            break

    # Extract constraints
    constraints = []
    for key in ["constraints", "non-goals", "non goals", "limitations"]:
        if key in sections:
            constraints = parse_list(sections[key])
            break

    # Extract interfaces
    interfaces = []
    for key in ["interfaces", "dependencies", "external"]:
        if key in sections:
            interfaces = parse_list(sections[key])
            break

    # Build overview from remaining content
    overview = sections.get("overview", "")
    if not overview:
        overview = sections.get("description", body)

    # Check if this looks like a natural language spec (no structured sections found)
    is_natural_language = not acceptance and not constraints and not interfaces

    spec = {
        "id": f"{repo}#{issue_number}",
        "title": issue["title"],
        "overview": overview,
        "acceptance": acceptance,
        "constraints": constraints,
        "interfaces": interfaces,
    }

    # For natural language specs, include raw content so planner can interpret it
    if is_natural_language:
        spec["raw_content"] = f"# {issue['title']}\n\n{body}"
        spec["format"] = "natural_language"
    else:
        spec["format"] = "structured"

    return spec
