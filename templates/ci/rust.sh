#!/bin/bash
# CI script for Rust projects
set -e

echo "=== Building ==="
cargo build

echo "=== Running tests ==="
cargo test

echo "=== Running clippy ==="
cargo clippy -- -D warnings || true

echo "=== CI Complete ==="
