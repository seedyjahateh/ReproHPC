#!/usr/bin/env bash
# Candidate 7: real Nextflow/Apptainer report acceptance, then the full frozen-source check.
cd /workspace
sif=/scratch/reprohpc-candidate-7.sif
started=$(date +%s)
python scripts/report_acceptance.py --sif "$sif" \
  --output /scratch/report-acceptance-candidate-7 \
  --export-directory /scratch/report-acceptance-candidate-7-export \
  >/scratch/report-acceptance-candidate-7.log 2>&1
echo "exit=$? seconds=$(( $(date +%s) - started ))" >/scratch/report-acceptance-candidate-7.done

export COVERAGE_FILE=/scratch/reprohpc-coverage-cycle-11
export REPROHPC_SIF="$sif"
export REPROHPC_SLURM=1
started=$(date +%s)
python -m pytest tests/unit tests/integration --basetemp=/scratch/reprohpc-check-cycle-11 \
  --cov=reprohpc --cov-report=xml:/scratch/coverage-cycle-11.xml --cov-report=term \
  --junitxml=/scratch/cycle-11-tests.xml -p no:cacheprovider -rfEs \
  >/scratch/cycle-11-check.log 2>&1
echo "exit=$? seconds=$(( $(date +%s) - started ))" >/scratch/cycle-11-check.done
