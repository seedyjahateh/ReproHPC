#!/usr/bin/env bash
# Cycle 14: full unit + real Apptainer/Slurm integration check on analysis candidate 10,
# with the stage-2 de-identification tests now runnable (pydicom is in the rebuilt lab).
cd /workspace
export COVERAGE_FILE=/scratch/reprohpc-coverage-cycle-15
export REPROHPC_SIF=/scratch/reprohpc-candidate-10.sif
export REPROHPC_SLURM=1
started=$(date +%s)
python -m pytest tests/unit tests/integration --basetemp=/scratch/reprohpc-check-cycle-15 \
  --cov=reprohpc --cov-report=xml:/scratch/coverage-cycle-15.xml --cov-report=term \
  --junitxml=/scratch/cycle-15-tests.xml -p no:cacheprovider -rfEs \
  >/scratch/cycle-15-check.log 2>&1
echo "exit=$? seconds=$(( $(date +%s) - started ))" >/scratch/cycle-15-check.done
