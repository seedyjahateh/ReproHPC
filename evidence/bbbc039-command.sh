#!/usr/bin/env bash
# Real BBBC039 nuclei images through the full containerized pipeline.
cd /workspace
sif=/scratch/reprohpc-candidate-7.sif
out=/scratch/bbbc039-run
started=$(date +%s)
python reprohpc run --profile local --params-file params/bbbc039.yaml \
  --sif "$sif" --sif-sha256 "$(sha256sum "$sif" | cut -d' ' -f1)" \
  --outdir "$out/run" --work-dir "$out/work" --launch-dir "$out/launch" \
  >"$out.log" 2>&1
echo "exit=$? seconds=$(( $(date +%s) - started ))" >"$out.done"
