# Neuroimaging: FSL brain extraction and volumetry

`fsl-bet-volumetry-v1` runs FSL's BET over public T1-weighted MRI and measures each brain mask, through the same pipeline, provenance and verification as the image routines. FSL does the science: this project builds the command lines, records what ran, and checks the results.

This is engineering evidence. Nothing here is clinical, diagnostic, or validated against manual segmentation.

## What ran

| | |
|---|---|
| Dataset | [OpenNeuro ds000001](https://openneuro.org/datasets/ds000001/versions/1.0.0), version **1.0.0**, DOI [10.18112/openneuro.ds000001.v1.0.0](https://doi.org/10.18112/openneuro.ds000001.v1.0.0), CC0 |
| Subjects | 5 (sub-01 … sub-05), T1w anatomicals only, defaced by the dataset's publishers |
| FSL | **6.0.7.23** (`fsl-bet2` 2111.9, `fsl-avwutils` 2209.6); `bet2` reports `Part of FSL (ID: 2412.6-dirty)` and `BET (Brain Extraction Tool) v2.1` |
| Tools called | `bet` (mask only: `-f <bet_frac> -m -n`), `fslstats -V` (voxels and volume), `fslstats -w` (bounding box) |
| Image | `containers/Dockerfile.fsl`, SIF `e55ee8cc555fd3df177b4572f866d52879d03853ceefcf7e7c9494e6c31239f4`, 396,185,600 bytes |
| Runs | Two local runs in independent work/launch/output directories, and one Slurm run, all on that SIF |
| Evidence | `evidence/mri-candidate-3/` |

### Brain volumes

| Subject | Brain voxels | Volume (mL) | QC |
|---|---|---|---|
| sub-01 | 796,516 | 1,416.0 | none |
| sub-02 | 865,511 | 1,538.7 | none |
| sub-03 | 719,223 | 1,278.6 | none |
| sub-04 | 660,191 | 1,173.7 | none |
| sub-05 | 848,347 | 1,508.2 | none |

Volume is the mask's non-zero voxel count times the image's voxel volume (1.0 × 1.3333 × 1.3333 mm), exactly as `fslstats -V` reports it. The verifier independently re-reads every mask and rejects a run whose recorded voxel count or volume disagrees with the mask's own voxels.

### Determinism

Two local runs and one Slurm run, same SIF and inputs, produced **identical results**: every scientific file matched byte for byte, every mask matched by voxel hash, and the maximum absolute difference in `brain_volume_mm3` was **0.0** across all five subjects, local versus local and local versus Slurm. All three runs also match the stored golden exactly. BET is deterministic here; the gzipped NIfTI masks were byte-identical too, so zlib's framing did not vary between runs.

Untested: determinism across different CPUs. All runs were on one machine, so this says nothing about different instruction sets or a different FSL build.

### Slurm

The Slurm run used native array jobs: five batches became array elements `52_0`–`52_3` plus job `53`, all `COMPLETED` with exit code `0:0`, and every job appears in `provenance/sacct.tsv` (`evidence/mri-candidate-3/slurm-sacct.tsv`). `MRI_BATCH` uses the same resource requests, retry policy and publication rules as the image routine's `ANALYZE_BATCH`.

## How bet_frac was chosen

`bet_frac` is BET's `-f`. It was chosen on **sub-01 and sub-02 only**; sub-03 to sub-05 were never inspected for the choice. For each tuning subject, `bet` ran at `-f` 0.3, 0.4 and 0.5, and the mask outlines were inspected on the mid sagittal, coronal and axial slices.

`0.5` (FSL's default) gave the tightest boundary on both tuning subjects with no visible cortical clipping on those slices. `0.3` pulled a large region below the skull base into the mask, and gave the largest volumes (sub-01 1,622.6 mL at 0.3 against 1,416.0 mL at 0.5).

This is visual inspection of three slices on two subjects, against no ground truth. It is not an optimisation, and a different reader could prefer 0.4.

## Findings worth stating

**Skull-base tissue stays in the mask.** At every `-f` tested, some bright tissue below the brain remained inside the mask on both tuning subjects, and the same appears on sub-05. This is a known BET behaviour on T1 images that include the neck when run without `-R`, `-B`, or a `robustfov` crop first. The volumes above are therefore slight over-estimates of brain tissue, and closer to an intracranial measure in that region.

**sub-04's image was already skull-stripped upstream.** Its T1w is 86.3% exactly-zero voxels, against 7.4–9.4% for the other four subjects, where zeros come only from defacing. Its file is 1.2 MB against about 5.5 MB. So for sub-04 the pipeline ran BET on an image that had already been brain-extracted, and its 1,173.7 mL is not comparable with the others. **None of the QC flags caught this**: the volume is inside the plausible range and the mask is clear of the field-of-view edges. A pre-stripped-input check would be a sensible addition; it does not exist yet.

**The golden is a regression anchor, not an oracle.** `tests/expected/openneuro/ds000001/1.0.0/expected.json` was written from a verified run of this SIF and holds hashes and numbers only. It proves later runs reproduce these results; it does not prove the results are correct. This differs from the demo routine, whose goldens come from an independent implementation in `tests/reference_oracle.py`.

**BET's self-reported version is not the release number.** `bet2` prints `Part of FSL (ID: 2412.6-dirty)`, an internal build id, where "dirty" is FSL's own build marker. Provenance therefore records the release, the installed package versions and that banner verbatim, rather than one "FSL version" string.

**Minimal install, not full FSL.** The image carries only `bet`, `fslstats`, `fslmaths` and their dependencies: 617 MB installed, against 4.7 GB for a full FSL install per FSL's own manifest. Any other FSL tool is absent by design.

## What was not done

- **No FreeSurfer `recon-all`.** It needs a registered licence file and 6–12 hours per subject. Out of scope, not attempted.
- **No clinical or patient data.** Public, defaced OpenNeuro data only. No HIPAA claim is made anywhere in this project; stage 2's DICOM work would be a de-identification workflow, which is a smaller and different claim.
- **No XNAT or REDCap integration.** Not attempted, and not simulated.
- **No accuracy validation.** No comparison against manual segmentation, against another tool (FSL `bet -R`, ANTs, SynthStrip), or against published volumes for this cohort. Agreement with an accepted method is unmeasured.
- **No QC of registration, bias field or motion.** Only brain extraction and volume.
- **Five subjects.** Enough to exercise the pipeline, too few for any statement about the dataset or population.

## Reproducing it

```bash
# 1. Data: downloads with no credentials, or rebuilds it from the committed manifest.
python scripts/fetch_openneuro.py --aws "uvx --from awscli==1.46.1 aws"

# 2. Image: minimal FSL from the explicit lock; record the SIF checksum.
python scripts/build_candidate.py --dockerfile containers/Dockerfile.fsl \
    --image reprohpc-fsl:dev --name reprohpc-fsl --discard-oci

# 3. One run (inside the lab, as researcher).
SIF=/scratch/reprohpc-fsl-candidate-3.sif
python reprohpc run --profile local --params-file params/openneuro.yaml \
    --sif "$SIF" --sif-sha256 "$(sha256sum "$SIF" | cut -d' ' -f1)" \
    --outdir /scratch/mri/run --work-dir /scratch/mri/work --launch-dir /scratch/mri/launch
python reprohpc verify --run /scratch/mri/run
python reprohpc compare --expected tests/expected/openneuro/ds000001/1.0.0 --actual /scratch/mri/run --exact

# 4. The full acceptance evidence above (two local runs, one Slurm run, all comparisons).
python scripts/mri_acceptance.py --sif "$SIF" --output /scratch/mri-acceptance --slurm
```

Add `--profile slurm --account research --partition demo` for the Slurm run. The data is reconstructed from `data/openneuro/ds000001/1.0.0/sources.json`, which pins each file's S3 object version, the size and MD5 that OpenNeuro publishes for snapshot 1.0.0, and the SHA-256 this project records. No image data is committed.

## How it fits the existing pipeline

`VALIDATE_DATASET`, `AGGREGATE` and `REPORT` are the same Nextflow processes for both routines, and `MRI_BATCH` mirrors `ANALYZE_BATCH`. The Python behind those processes dispatches on `algorithm`, because a 3-D brain mask and a brain volume cannot be represented in the 2-D image contract (`width`, `height`, `object_count`, `mask.npy`) without misreporting them. The MRI routine therefore has its own contracts (`schemas/mri_*.json`) and its own static report; every existing schema file is unchanged byte for byte, and the image workspace report is untouched. See [architecture](architecture.md) and [the algorithm contract](algorithm.md) for the demo routine's side.
