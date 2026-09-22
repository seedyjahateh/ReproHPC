# Task: FSL neuroimaging routine on public OpenNeuro data

Written 2026-09-21. Target: usable evidence by **2026-09-27**, because the application it supports closes
**2026-10-02** (UNC Chapel Hill, Research Software Developer, Psychiatry).

This extends ReproHPC with a second real-data routine. It does **not** start a new project: the Nextflow DSL2
workflow, Apptainer execution, Slurm profiles, provenance records, determinism checks and golden comparisons
already exist and must keep working unchanged.

## Why

The role requires "experience with neuroimaging tools such as FreeSurfer, FSL, AFNI" and "neuroimaging or other
large biomedical datasets". Today ReproHPC processes synthetic images (`demo-cv-v1`) and BBBC039 microscopy.
Running FSL over public MRI data through this pipeline makes a truthful, checkable claim: *containerized FSL,
executed on Slurm through a reproducible workflow, with byte-identical reruns.*

## Non-goals — do not do these

- **No FreeSurfer `recon-all`.** It needs a registered license file and 6-12 hours per subject. Out of scope for
  this window; say so in the docs rather than half-doing it.
- **No clinical or patient data.** Public OpenNeuro data only. Never claim HIPAA compliance anywhere; the DICOM
  stage below is a *de-identification workflow*, which is a different and smaller claim.
- **No XNAT or REDCap integration.** Familiarity cannot be faked in a weekend; that gap gets an honest answer.
- Do not weaken any existing guarantee to make this fit. If determinism cannot hold for an FSL step, record that
  finding in the docs — a documented limitation is worth more than a quiet fudge.

## Stage 1 — FSL skull-strip and volumetry (required)

**Data.** 3-5 subjects' T1w anatomicals from one public OpenNeuro BIDS dataset. Follow the BBBC039 precedent:

- `scripts/fetch_openneuro.py` — downloads by accession id with `aws s3 sync --no-sign-request`
  (no credentials needed), writes `data/openneuro/<accession>/<version>/` with `dataset.json` and `samples.csv`
  carrying per-file SHA-256, exactly like `scripts/fetch_bbbc039.py` does.
- Record the accession, version and file hashes. The dataset must be reconstructible from the manifest alone.

**Container.** `containers/Dockerfile.fsl` (or a second stage in the existing Dockerfile) with FSL pinned to an
exact version. Build the SIF, record its SHA-256, and pass it the same way `--sif` / `--sif-sha256` work now.
FSL is free for non-commercial use; note the licence in `THIRD_PARTY_NOTICES.md`.

**Task verb.** Extend the Python package rather than bypassing it, so provenance keeps working:

- `src/reprohpc/mri.py` — one function per step: `skull_strip()` (calls `bet`), `volumes()` (calls `fslstats`),
  `qc_mask()`. Shell out with explicit arguments, capture stdout, and fail loudly on a non-zero exit.
- `python -m reprohpc.task mri --spec-b64 ... --output result <nifti files>` mirroring the existing `analyze`
  verb, writing `result/task.json` with the same provenance fields, including the FSL version string.

**Module.** `modules/local/mri_batch.nf`, modelled on `analyze_batch.nf`: same `tag`, `cpus`, `memory`, `time`,
`maxRetries`, `errorStrategy` and `publishDir` shape. Feed its outputs into the existing `AGGREGATE` and `REPORT`
processes so the report gains a brain-volume table with no changes to the report contract.

**Params.** `params/openneuro.yaml` in the shape of `params/bbbc039.yaml`: dataset, input_manifest, reference,
`bet_frac` (BET's `-f`), `batch_size`. Say in a comment how `bet_frac` was chosen and on which subjects.

**Acceptance criteria (all must hold):**

1. `python reprohpc run --profile local --params-file params/openneuro.yaml ...` completes and prints
   `Status: success`.
2. The same run on `--profile slurm` completes, with `provenance/sacct.tsv` recorded.
3. `python reprohpc verify --run <dir>` passes, and `compare` against a stored golden passes.
4. **Determinism:** two runs over the same subjects produce identical masks and identical volumes
   (maximum float difference 0.0), proven in an evidence file the way `evidence/offline-candidate-5/` does.
5. Existing tests still pass (157 today) and new unit tests cover `mri.py` argument construction and failure
   handling, with FSL mocked so CI needs no FSL.
6. `docs/neuroimaging.md` states exactly what was run, on which dataset and version, what FSL version, what was
   *not* done (FreeSurfer, clinical data, XNAT/REDCap) and any determinism caveat found.

## Stage 2 — DICOM ingest and de-identification (preferred qual, ~half day)

- `dcm2niix` in the same container for DICOM to NIfTI.
- `src/reprohpc/deident.py` using `pydicom`: strip PatientName, PatientID, PatientBirthDate, institution and
  referring-physician tags before conversion; keep the pseudonym mapping outside the archive; list every tag
  removed and every tag deliberately kept in `docs/neuroimaging.md`.
- Test on a public sample DICOM series. Unit-test the tag stripping against a synthetic dataset.

## Stage 3 — QC visualization (1-2 hours)

Per-subject montage (nilearn or matplotlib) plus a volume distribution plot, added to the existing report.
Static images only, offline, consistent with the current no-network rule.

## Order and timing

| By | Stage |
|---|---|
| 09/23 | Stage 1 local run + determinism evidence |
| 09/25 | Stage 1 Slurm run, goldens, docs; Stage 2 |
| 09/27 | Stage 3, README and TASKS.md updated, pushed |

If a stage slips, ship what is done and apply anyway. The deadline does not move and the posting is not open
until filled.

## When it is finished

Tell career-os. It adds verified bullets to `profile/master_profile.yaml` (code-checked, no invented numbers),
builds the research-type CV for J-0301, and drafts the four supplemental answers, two of which are open-ended:
neuroimaging tools, and scientific software and computational workflows.
