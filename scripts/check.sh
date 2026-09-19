#!/usr/bin/env bash
set -euo pipefail
cd /workspace
mkdir -p evidence
export COVERAGE_FILE=/scratch/reprohpc-coverage
python -m ruff check src tests scripts ops/slurm/init_cgroups.py
python -m ruff format --check src tests scripts ops/slurm/init_cgroups.py
python scripts/audit_requirements.py
python -m build >evidence/python-build.log 2>&1
python -m pytest tests/unit tests/integration --basetemp=/scratch/reprohpc-check \
  --cov=reprohpc --cov-report=xml --cov-report=term-missing --junitxml=evidence/tests.xml
python scripts/check_coverage.py
