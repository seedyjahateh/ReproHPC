# Acceptance evidence

[TASKS.md](../TASKS.md) contains all 74 numbered PRD requirements, seven additional deliverables, acceptance criteria, decisions, and continuation notes. Checked means implemented and verified. P1 work is optional. The PRD remains authoritative.

Candidate 4 passed the complete Linux check: **152 tests, zero failures/skips**, 89.7% statement and 82.4% branch coverage. Candidate 5 adds a finalization ordering repair and five regression cases; its complete frozen-source check passed with **157 tests, zero failures/skips**, 90.0% statement and 82.4% branch coverage. Neither candidate is a published release.

| Gate | Evidence | Current evidence category |
|---|---|---|
| T-01/T-02 scientific/contracts; G-01, US-02/03, FR-01–07, M-03 | `tests/unit/`, independent `tests/reference_oracle.py`, immutable `tests/expected/demo/`; negative preflight checks | Passed with exact threshold/empty/border/calibration behavior. Synthetic reference only; no supplied domain routine or independent endorsement. |
| T-03/T-04 local workflow; FR-08/10, NFR-01/05/09, M-01 | `test_workflow.py`; `evidence/cycle-9-tests.xml` and `evidence/cycle-9-raw/` | Actual Nextflow/Apptainer clean runs, goldens, exact repetition, independent result copies, export/extraction all passed. |
| T-05–T-08 recovery/invalidation; US-04/07, FR-12/13, M-09 | `test_recovery.py`, `test_invalidation.py`, `test_workflow.py`; raw traces and status | Real interruption, hard kill/manual cleanup, input/reference/parameter/SIF changes, cache repair, transient retry, deterministic failure, missing output and finalization failure all passed. |
| T-09/T-10 Slurm; US-06, FR-11, NFR-12, M-10 | `test_slurm.py`, cycle-8 raw accounting/trace/commands | Native array groups 4/4/2, single-task retry, actual requests, enforced OOM/TIMEOUT and corrected-resource resume passed. Tasks ran as UID 1001; cleanup checks passed. |
| Provenance; US-05, NFR-08, M-11, A-02 | Schemas and relationship tests; actual export at `artifacts/tutorial-candidate-4/` | Output-to-task/input/reference/software lineage, original cached accounting, explicit missing metrics, JSON-LD units/distributions and versioned term dictionary verified. Export is not a public DOI deposit. |
| T-11 offline; NFR-06, M-02 | `evidence/offline-candidate-5/evidence.json` and raw local/Slurm records | Candidate-5 SIF, host without NumPy/OpenCV, no default route, failed external probes, exact masks/counts/QC and all float maximum differences 0.0. Network restored. |
| Envelope; NFR-02/03/11 | 10,000-record planning test; `evidence/scale-256-candidate-5/`; `evidence/resources-candidate-4.json` | Candidate-5 actual 256-image run processed 256 images in 16 batches with 2012 objects; 4096-square image 449532 KiB process high-water RSS; million-row aggregation 52984 KiB. Real one-CPU/2-GiB Slurm resource probe. These are not scaling trials. |
| Build and cache controls; RE-02–07, A-03 | `evidence/candidate-*.json`, build logs, hashed locks, real invalidation and repeat tests | Baked scientific code, retained canonical SIF bytes, stable seeds/serialization and content identities verified. Final release commit/DOIs remain open. |
| Usability; NFR-10, M-13, A-06 | `evidence/tutorial-candidate-4/evidence.json` | Actual CLI walkthrough 612.44 s, first result 65.13 s, exact printed resume command, export verified. Script-assisted author self-test; no independent novice study. |
| M-12 coverage | `evidence/coverage-cycle-9.xml`, `scripts/check_coverage.py` | Candidate 5 passes 90.0% statement / 82.4% branch thresholds. Python coverage does not measure DSL2. |
| CI; A-05 | Three pinned workflows; real build/check scripts; Actionlint | Definitions validated; immutable-key tool cache revalidated; synthetic diagnostics retained on success/failure. No hosted run URL or measured CI duration yet. |

## Open release gates

| Requirements | Missing evidence or decision |
|---|---|
| G-03, NFR-04, T-12, M-04–08 | Otherwise idle dedicated allocation: >=8 logical CPUs, >=16 GiB RAM, >=100 GiB free storage. Three randomized uncached trials at each of 1/2/4 workers, three direct serial baselines >=120 s, qualified speedup/overhead/resource metrics and chart. Current shared Docker host has about 7.60 GiB. Harness/chart unit fixtures are not measured performance. |
| G-02/04, US-01/08, FR-14, RE-01/08, T-13, M-14, A-01/07 | Confirmed creators/rights, final dependency redistribution review, frozen source commit/tag, actual software/data version DOIs, archived artifacts/lock, fresh unauthenticated reproduction on local and Slurm. Protocol unit tests do not satisfy public download integration. |
| A-05 | Workflow-enabled GitHub access, hosted runs, required branch checks, measured 15-minute warm / 25-minute cold CI targets. Current CLI OAuth scopes omit `workflow`; no push/dispatch attempted. |

The remaining local gate is the qualified benchmark and eventual publication/reproduction. Retain each record with its actual SIF/source identity; never relabel historical observations as later candidate runs. Public artifacts exclude unreviewed operational diagnostics even though synthetic CI logs are retained for debugging.
