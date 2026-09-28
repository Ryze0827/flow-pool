#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
bash "$ROOT/scripts/setup.sh"
exec "$ROOT/.venv/bin/python" "$ROOT/scripts/service.py" restart
