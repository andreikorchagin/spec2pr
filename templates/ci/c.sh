#!/bin/bash
# CI script for C projects
set -e

echo "=== Building ==="
make

echo "=== Running tests ==="
make test

echo "=== CI Complete ==="
