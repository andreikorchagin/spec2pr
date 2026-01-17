# spec2pr v1 Design

## Overview

A GitHub-native, agent-orchestrated software delivery pipeline that converts human-written specs (GitHub Issues) into reviewable PRs using Claude Code in headless mode.

**Core principle:** Bounded automation, not autonomous engineering.

## Core Principles

1. **Pipeline, not magic** - Explicit sequence of stages with defined I/O
2. **Claude Code is a worker** - Never orchestrates, only executes (planning, tasks, judging)
3. **File-based contracts** - Stages communicate via JSON artifacts
4. **GitHub-native** - Actions for runtime, Issues for intake, PRs for review
5. **Governed automation** - CI is truth, humans approve all changes

## System Flow

```
GitHub Issue (spec)
  → load_spec
  → plan_tasks (Claude Code headless)
  → tasks.json
  → run_task (Claude Code headless)
  → verify (ci.sh)
  → judge (Claude Code headless)
  → publish (PR or Issue)
```

## Repository Architecture

### Controller Repo (this repo)

Contains the system itself: orchestration logic, prompts, contracts, reusable workflow.

### Target Repos

Application code repos that opt in via workflow file, issue template, and label. Modified only via PRs created by the system.

### Execution Model

- GitHub Action runs **in the target repo**
- Target repo's workflow calls the controller's reusable workflow
- Controller tools are checked out and executed in the target repo context

## Repo Structure

```
spec2pr/
├── .github/
│   └── workflows/
│       └── spec2pr.yml          # Reusable workflow (called by target repos)
├── tools/
│   └── spec2pr/
│       ├── cli.py               # Main entry point
│       ├── config.yaml          # Default configuration
│       ├── contracts/           # JSON schemas for data contracts
│       │   ├── spec.schema.json
│       │   ├── task.schema.json
│       │   ├── verify.schema.json
│       │   └── judgment.schema.json
│       ├── stages/              # Stage implementations
│       │   ├── load_spec.py
│       │   ├── plan_tasks.py
│       │   ├── run_task.py
│       │   ├── verify.py
│       │   ├── judge.py
│       │   └── publish.py
│       ├── prompts/             # Claude Code prompts (markdown)
│       │   ├── planner.md
│       │   ├── worker.md
│       │   └── judge.md
│       └── adapters/            # GitHub API, file I/O
│           └── github.py
├── templates/                   # For target repos to copy
│   ├── workflow.yml             # Target repo workflow template
│   └── ISSUE_TEMPLATE/
│       └── spec.yml
└── docs/
    └── plans/
```

## Data Contracts

### spec.json

Output of `load_spec`, input to `plan_tasks`.

```json
{
  "id": "owner/repo#123",
  "title": "Short spec title",
  "overview": "High-level description from issue body",
  "acceptance": ["Testable criterion 1", "Testable criterion 2"],
  "constraints": ["Explicit non-goal 1"],
  "interfaces": []
}
```

### task.json

Output of `plan_tasks`, input to `run_task`.

```json
{
  "id": "T001",
  "title": "Task title",
  "goal": "Concrete outcome description",
  "files_allowlist": ["src/", "tests/"],
  "loc_cap": 300,
  "done_when": ["./ci.sh"],
  "non_goals": ["What not to do"]
}
```

### verify.json

Output of `verify` stage.

```json
{
  "passed": true,
  "commands": ["./ci.sh"],
  "logs_path": "artifacts/T001/ci.log",
  "summary": "All tests passed"
}
```

### judgment.json

Output of `judge` stage.

```json
{
  "judge_id": "correctness",
  "verdict": "accept",
  "scores": {"correctness": 4, "scope": 5},
  "blocking_issues": [],
  "confidence": "high"
}
```

## GitHub Workflow

### Reusable Workflow (controller repo)

```yaml
# .github/workflows/spec2pr.yml
name: spec2pr
on:
  workflow_call:
    inputs:
      issue_number:
        required: true
        type: number
      target_repo:
        required: true
        type: string
    secrets:
      ANTHROPIC_API_KEY:
        required: true

permissions:
  contents: write
  pull-requests: write
  issues: read

jobs:
  run-pipeline:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          repository: ${{ inputs.target_repo }}

      - name: Checkout controller (for tools)
        uses: actions/checkout@v4
        with:
          repository: andreikorchagin/spec2pr
          path: .spec2pr

      - name: Install Claude Code
        run: npm install -g @anthropic-ai/claude-code

      - name: Run pipeline
        run: python .spec2pr/tools/spec2pr/cli.py --issue ${{ inputs.issue_number }}
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

### Target Repo Workflow

```yaml
# .github/workflows/spec2pr.yml
name: spec2pr
on:
  issues:
    types: [labeled]

jobs:
  spec2pr:
    if: github.event.label.name == 'spec2pr/run' && !github.event.issue.pull_request
    uses: andreikorchagin/spec2pr/.github/workflows/spec2pr.yml@main
    with:
      issue_number: ${{ github.event.issue.number }}
      target_repo: ${{ github.repository }}
    secrets: inherit
```

## Secret Security

1. **Secrets never leave GitHub's secret store** - Passed via `secrets: inherit`
2. **GitHub auto-masks secrets in logs** - Replaced with `***`
3. **No secrets in artifacts** - Never written to files, PR descriptions, or comments
4. **Claude Code runs without interactive output** - Uses `--print` flag
5. **Fork protection** - Workflow only runs on non-fork events
6. **Scoped permissions** - Only contents:write, pull-requests:write, issues:read

## CLI Execution Model

```python
def main(issue_number: int):
    artifacts_dir = Path(f".spec2pr/artifacts/{issue_number}")
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    # Stage 1: Load spec from GitHub issue
    spec = load_spec(issue_number)
    write_json(artifacts_dir / "spec.json", spec)

    # Stage 2: Plan tasks (Claude Code headless)
    tasks = plan_tasks(spec)
    write_json(artifacts_dir / "tasks.json", tasks)

    # Stage 3-5: Execute each task
    for task in tasks:
        result = run_task(task)
        verify_result = verify(task)
        judgment = judge(task, result)

        if judgment["verdict"] == "reject":
            publish_issue(task, judgment)
        else:
            publish_pr(task, result)
```

## Claude Code Invocation

Headless, tool-restricted:

```bash
claude --print --dangerously-skip-permissions \
  --allowedTools "Read,Edit,Bash" \
  --prompt "$(cat prompts/worker.md)" \
  < task.json
```

Safe in ephemeral GitHub Actions VM context:
- VM destroyed after job
- No persistent state
- Human PR review before merge
- CI verification required

## Prompts

### planner.md (tools: Read, Bash)

Breaks spec into small, independent tasks (<300 LOC each).

### worker.md (tools: Read, Edit, Bash)

Implements exactly what the task describes, constrained by files_allowlist and loc_cap.

### judge.md (tools: Read, Bash)

Evaluates if task was completed correctly. Outputs accept/reject verdict.

## Target Repo Requirements

1. **Workflow file** - Copy from `templates/workflow.yml`
2. **Repository secret** - `ANTHROPIC_API_KEY`
3. **CI script** - `ci.sh` must exist, be deterministic, non-interactive, fast
4. **Issue template** (optional) - Structured spec format

## Non-Goals (v1)

- Ralph loops / self-improvement cycles
- DAG scheduling / parallel task execution
- Training / fine-tuning
- GPU usage
- Long-running servers

## Success Criteria

- Issue + label produces PRs
- All automation is auditable via artifact files
- Claude Code runs headlessly with restricted tools
- Easy to extend without refactors
