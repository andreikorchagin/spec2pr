#!/bin/bash
# CI script for Go projects
set -e

echo "=== Running tests ==="
go test ./...

echo "=== Running vet ==="
go vet ./...

echo "=== Building ==="
go build ./...

echo "=== CI Complete ==="
