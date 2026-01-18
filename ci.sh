#!/bin/bash
# Simple CI script for schema validation
set -e

echo "Validating JSON schemas..."

# Check if task.schema.json is valid JSON
python3 -c "
import json
import sys
with open('tools/spec2pr/contracts/task.schema.json') as f:
    schema = json.load(f)
print('✓ task.schema.json is valid JSON')
"

echo "All checks passed!"
