#!/usr/bin/env bash
# Cycle 16: full unit + real Apptainer/Slurm integration check on analysis candidate 12,
# after stage 3 added QC montages and the volume distribution chart.
cd /workspace
export COVERAGE_FILE=/scratch/reprohpc-coverage-cycle-16
export REPROHPC_SIF=/scratch/reprohpc-candidate-12.sif
export REPROHPC_SLURM=1
started=$(date +%s)
python -m pytest tests/unit tests/integration --basetemp=/scratch/reprohpc-check-cycle-16 \
  --cov=reprohpc --cov-report=xml:/scratch/coverage-cycle-16.xml --cov-report=term \
  --junitxml=/scratch/cycle-16-tests.xml -p no:cacheprovider -rfEs \
  >/scratch/cycle-16-check.log 2>&1
echo "exit=$? seconds=$(( $(date +%s) - started ))" >/scratch/cycle-16-check.done
