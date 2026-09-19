#!/usr/bin/env bash
# Candidate 9: report acceptance, real BBBC039 run, then the full frozen-source check.
cd /workspace
sif=/scratch/reprohpc-candidate-9.sif

started=$(date +%s)
python scripts/report_acceptance.py --sif "$sif" \
  --output /scratch/report-acceptance-candidate-9 \
  --export-directory /scratch/report-acceptance-candidate-9-export \
  >/scratch/report-acceptance-candidate-9.log 2>&1
echo "exit=$? seconds=$(( $(date +%s) - started ))" >/scratch/report-acceptance-candidate-9.done

started=$(date +%s)
out=/scratch/bbbc039-run-9
python reprohpc run --profile local --params-file params/bbbc039.yaml \
  --sif "$sif" --sif-sha256 "$(sha256sum "$sif" | cut -d' ' -f1)" \
  --outdir "$out/run" --work-dir "$out/work" --launch-dir "$out/launch" \
  >"$out.log" 2>&1
code=$?
python scripts/evaluate_bbbc039.py --run "$out/run" \
  --output /workspace/evidence/bbbc039-evaluation.json >>"$out.log" 2>&1
echo "exit=$code seconds=$(( $(date +%s) - started ))" >"$out.done"

export COVERAGE_FILE=/scratch/reprohpc-coverage-cycle-12
export REPROHPC_SIF="$sif"
export REPROHPC_SLURM=1
started=$(date +%s)
python -m pytest tests/unit tests/integration --basetemp=/scratch/reprohpc-check-cycle-12 \
  --cov=reprohpc --cov-report=xml:/scratch/coverage-cycle-12.xml --cov-report=term \
  --junitxml=/scratch/cycle-12-tests.xml -p no:cacheprovider -rfEs \
  >/scratch/cycle-12-check.log 2>&1
echo "exit=$? seconds=$(( $(date +%s) - started ))" >/scratch/cycle-12-check.done
