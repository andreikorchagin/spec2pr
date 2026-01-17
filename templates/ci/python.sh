#!/bin/bash
# CI script for Python projects
set -e

echo "=== Installing dependencies ==="
pip install -r requirements.txt

echo "=== Running tests ==="
pytest

echo "=== Running linter ==="
ruff check . || true

echo "=== CI Complete ==="
