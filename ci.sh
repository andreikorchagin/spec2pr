#!/bin/bash
set -e

# Test the --version flag
echo "Testing --version flag..."
output=$(python tools/spec2pr/cli.py --version)
expected="spec2pr 0.1.0"

if [ "$output" = "$expected" ]; then
    echo "✓ --version flag works correctly"
    exit 0
else
    echo "✗ --version flag failed"
    echo "Expected: $expected"
    echo "Got: $output"
    exit 1
fi
