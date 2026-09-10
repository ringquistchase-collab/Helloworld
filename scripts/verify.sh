#!/usr/bin/env bash
# verify.sh
# Local equivalent of .github/workflows/verify.yml -- runs the same
# checks CI runs, from a Git Bash / POSIX shell, without needing to
# push and wait for GitHub Actions.
#
# Run from anywhere: bash scripts/verify.sh

set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "=== Installing dependencies ==="
python -m pip install -q -r requirements.txt

echo ""
echo "=== project_identifier.py ==="
python project_identifier.py

echo ""
echo "=== pytest ==="
python -m pytest tests/ -v

echo ""
echo "=== Interop smoke test (Python client against a live node) ==="
python scripts/local_verify_node.py &
NODE_PID=$!
trap 'kill "$NODE_PID" 2>/dev/null || true; rm -f "$ROOT/local_verify_node.dna.json"' EXIT
sleep 2

set +e
python interop_client.py 127.0.0.1 18765
INTEROP_EXIT=$?
set -e

if [ "$INTEROP_EXIT" -ne 0 ]; then
  echo "Interop smoke test FAILED (exit $INTEROP_EXIT)"
  exit "$INTEROP_EXIT"
fi

echo ""
echo "=== All checks passed ==="
