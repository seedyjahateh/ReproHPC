#!/usr/bin/env bash
# Full frozen-source check on analysis candidate 10: every existing guarantee plus the new
# MRI unit tests. The MRI routine's own acceptance is recorded separately.
cd /workspace
export COVERAGE_FILE=/scratch/reprohpc-coverage-cycle-13
export REPROHPC_SIF=/scratch/reprohpc-candidate-10.sif
export REPROHPC_SLURM=1
started=$(date +%s)
python -m pytest tests/unit tests/integration --basetemp=/scratch/reprohpc-check-cycle-13 \
  --cov=reprohpc --cov-report=xml:/scratch/coverage-cycle-13.xml --cov-report=term \
  --junitxml=/scratch/cycle-13-tests.xml -p no:cacheprovider -rfEs \
  >/scratch/cycle-13-check.log 2>&1
echo "exit=$? seconds=$(( $(date +%s) - started ))" >/scratch/cycle-13-check.done
