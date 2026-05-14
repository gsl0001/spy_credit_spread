#!/bin/bash
# Live-trading runner. NO --reload — file saves during in-flight orders will
# orphan spreads at the broker (see 2026-05-11 incident). Use run-dev.sh when
# editing code without active positions.
set -euo pipefail
cd "$(dirname "$0")"
exec python3 -m uvicorn main:app --host 127.0.0.1 --port 8000 "$@"
