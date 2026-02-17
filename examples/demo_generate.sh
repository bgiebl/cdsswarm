#!/usr/bin/env bash
# Demo: generate a request file from a template and preview it.
#
# Usage:
#   bash examples/demo_generate.sh

set -euo pipefail

TEMPLATE="examples/era5_template.json"

echo "=== Template ==="
cat "$TEMPLATE"
echo

echo "=== Dry run (show targets) ==="
cdsswarm generate "$TEMPLATE" --dry-run
echo

echo "=== Generate to stdout (first 30 lines) ==="
cdsswarm generate "$TEMPLATE" | head -30
echo "..."
echo

echo "=== Override split_by to only split by variable ==="
cdsswarm generate "$TEMPLATE" --split-by variable --dry-run
echo

echo "=== Write to file and preview with cdsswarm --dry-run ==="
cdsswarm generate "$TEMPLATE" -o /tmp/era5_requests.json
cdsswarm /tmp/era5_requests.json --dry-run
