# spec2pr Development Guide

> **Purpose**: This file contains all rules, constraints, and context for developing spec2pr. Read this before making any changes.

---

## What is spec2pr?

A GitHub-native pipeline that converts Issues (specs) into Pull Requests using Claude Code.

**Flow**: `Issue → Plan Tasks → Execute → Verify → Judge → PR`

**Repository**: `github.com/andreikorchagin/spec2pr`

---

## Hard Rules (Never Violate)

### 1. Human Review Required
**No auto-merge. Human ALWAYS reviews code before merge.**
- PRs are created for human review
- Only humans click the merge button
- Never implement anything that bypasses human code review

### 2. Human Touchpoints (Only These)
1. **Create spec** - Human writes the GitHub Issue
2. **Review PR + merge** - Human reviews code, then merges

Everything else is automated.

### 3. Security
- Only repo owner/collaborators can trigger workflow (via label permissions)
- Allowlist enforcement: workers can only modify files in `files_allowlist`
- Unauthorized changes are auto-reverted

---

## Architecture

```
tools/spec2pr/
├── cli.py                    # Main entry point
├── stages/
│   ├── load_spec.py          # GitHub API → spec dict
│   ├── plan_tasks.py         # Spec → task list (Claude)
│   ├── run_task.py           # Execute task (Claude)
│   ├── verify.py             # Run done_when commands
│   ├── judge.py              # Accept/reject decision (Claude)
│   └── publish.py            # Create PR or Issue
├── prompts/
│   ├── planner.md            # Instructions for task planning
│   └── worker.md             # Instructions for task execution
├── contracts/
│   ├── spec.schema.json      # Input spec format
│   ├── task.schema.json      # Task format
│   └── judgment.schema.json  # Judge output format
└── adapters/
    └── github.py             # GitHub CLI wrapper
```

### Key Behaviors
- **Single PR per issue**: All tasks commit to one branch, one PR created at end
- **Sequential execution**: Tasks run one at a time (GitHub Actions concurrency)
- **Haiku by default**: Uses cheaper model, can specify `model: "sonnet"` in task
- **Path validation**: Verify stage fails fast on hallucinated file paths

---

## Current State

### Working
- Pipeline triggers on `spec2pr/run` label
- OAuth authentication (CLAUDE_CODE_OAUTH_TOKEN)
- Single PR per issue (not cumulative)
- Path validation in verify stage
- Graceful handling of empty commits
- Version flag (`--version`)

### Known Issues
- Planner hallucinates file paths (needs TASK-006)
- `done_when` often references non-existent scripts (needs TASK-004)
- No retry logic for failed tasks (needs TASK-002)

---

## Development Workflow

### For Manual Changes (Bootstrapping)
1. Make changes locally
2. Create branch, commit, push
3. Create PR
4. Human reviews and merges

### For spec2pr Self-Development
1. Create Issue with spec
2. Add `spec2pr/run` label
3. Wait for PR
4. Human reviews and merges

### Testing
- **Dogfood repo**: `andreikorchagin/spec2pr`
- **External test**: `andreikorchagin/llama2.c-test`

---

## Backlog (Priority Order)

See `~/.claude/project-notes/spec2pr-prd.md` for full details.

| Task | Description | Status |
|------|-------------|--------|
| TASK-006 | Auto-detect file paths before planning | **Next** |
| TASK-004 | Validate/infer done_when commands | Ready |
| TASK-002 | Retry logic with model escalation | Ready |
| TASK-003 | LOC cap enforcement | Ready |
| TASK-007 | Task dependency detection | Backlog |

---

## Quick Reference

```bash
# Trigger workflow
gh issue edit <N> --add-label "spec2pr/run"

# Check runs
gh run list --repo andreikorchagin/spec2pr

# View logs
gh run view <run-id> --log

# Local test
python tools/spec2pr/cli.py --issue <N> --repo andreikorchagin/spec2pr --dry-run
```

---

## Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-01-18 | No auto-merge ever | Human must review all code |
| 2026-01-18 | Single PR per issue | Avoids cumulative PR confusion |
| 2026-01-18 | Haiku as default model | 80% cost reduction |
| 2026-01-17 | OAuth over API key | Claude Max subscription available |
| 2026-01-17 | Sequential task execution | Prevent merge conflicts |

---

*Last updated: 2026-01-18*
