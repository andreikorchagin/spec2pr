#!/bin/bash
# CI script for Node.js projects
set -e

echo "=== Installing dependencies ==="
npm ci

echo "=== Running tests ==="
npm test

echo "=== Running linter ==="
npm run lint || true

echo "=== CI Complete ==="
