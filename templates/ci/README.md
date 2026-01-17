# CI Script Templates

Copy the appropriate template to your repository root as `ci.sh` and customize as needed.

## Requirements

- Scripts must be executable: `chmod +x ci.sh`
- Scripts must exit with code 0 on success, non-zero on failure
- Scripts should be deterministic and non-interactive

## Available Templates

| File | Language/Framework |
|------|-------------------|
| `nodejs.sh` | Node.js / npm |
| `python.sh` | Python / pytest |
| `go.sh` | Go |
| `rust.sh` | Rust / Cargo |
| `c.sh` | C / Make |

## Customization Tips

1. **Add linting** - Include linters for code quality
2. **Add type checking** - For TypeScript, mypy, etc.
3. **Keep it fast** - CI runs on every task verification
4. **Be deterministic** - Avoid flaky tests or network dependencies
