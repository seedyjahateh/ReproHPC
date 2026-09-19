#!/usr/bin/env bash
set -euo pipefail
python "$(dirname "$0")/serial_baseline.py" "$@"
