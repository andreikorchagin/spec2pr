# spec2pr

A GitHub-native, agent-orchestrated software delivery pipeline that converts specs (GitHub Issues) into reviewable Pull Requests using Claude Code.

## Overview

spec2pr automates the path from specification to implementation:

```
GitHub Issue (spec) → Planner → Tasks → Worker → CI → Judge → PR
```

**This is bounded automation, not autonomous engineering.** Humans write specs, humans review PRs. Agents do the implementation work in between.

## Quick Start

### 1. Add the workflow to your repository

Copy `templates/workflow.yml` to `.github/workflows/spec2pr.yml` in your target repository.

### 2. Add your Anthropic API key

Add `ANTHROPIC_API_KEY` as a repository secret in Settings → Secrets → Actions.

### 3. Configure workflow permissions

In your repository, go to Settings → Actions → General → Workflow permissions:

1. Select **"Read and write permissions"**
2. Check **"Allow GitHub Actions to create and approve pull requests"**
3. Click Save

> Without these permissions, the workflow will fail with `startup_failure` or permission denied errors.

### 4. Create a CI script

spec2pr requires a `ci.sh` script in your repository root:

```bash
#!/bin/bash
set -e
npm test      # or pytest, go test, etc.
npm run lint  # optional
```

See `templates/ci/` for examples for different project types.

### 5. Create a spec issue

Create a GitHub issue with:
- Clear overview of what to build
- Specific acceptance criteria
- Constraints / non-goals

### 6. Trigger the pipeline

Add the `spec2pr/run` label to the issue. The pipeline will:
1. Parse your spec
2. Plan implementation tasks
3. Execute each task with Claude Code
4. Verify with CI
5. Judge the results
6. Create PRs (or issues if failed)

## How It Works

### Stages

| Stage | Description | Claude Tools |
|-------|-------------|--------------|
| load_spec | Parse issue into structured spec | - |
| plan_tasks | Break spec into small tasks | Read, Bash |
| run_task | Implement each task | Read, Edit, Bash |
| verify | Run CI to verify | - |
| judge | Evaluate completion | Read, Bash |
| publish | Create PR or issue | - |

### Data Contracts

All stages communicate via JSON files in `.spec2pr/artifacts/`:
- `spec.json` - Parsed specification
- `tasks.json` - Planned tasks
- `verify.json` - CI results
- `judgment.json` - Agent verdicts

## Configuration

See `tools/spec2pr/config.yaml` for defaults.

## Security

- Secrets are never written to files or logs
- Claude Code runs in ephemeral GitHub Actions VMs
- All changes require human PR review
- Fork PRs cannot trigger the workflow

## Requirements

Target repositories must have:
- `ci.sh` script (deterministic, non-interactive)
- `ANTHROPIC_API_KEY` secret
- Workflow permissions set to "Read and write"
- "Allow GitHub Actions to create and approve pull requests" enabled
- Branch protection rules (recommended)

## License

MIT
