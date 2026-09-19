# CLI and configuration

Use `python reprohpc --help` and `python reprohpc COMMAND --help` for the authoritative flag list. The executable source launcher can also be invoked as `./reprohpc` where its executable bit is set.

| Command | Behavior |
|---|---|
| `doctor --profile local|slurm|slurm_scalar` | Report platform/tool versions and missing prerequisites; submit no jobs. |
| `run --params-file FILE --sif FILE --sif-sha256 HASH --outdir NEW_DIR` | Validate, execute Nextflow, finalize, then independently verify. |
| `verify --run DIR` | Check success status, schemas, complete inventory, hashes, and scientific consistency. |
| `compare --expected DIR --actual DIR [--exact]` | Compare independent goldens or scientific result trees. |
| `export --run DIR --output NEW_ARCHIVE` | Verify then export an allowlisted, redacted archive with a sidecar SHA-256. |
| `prepare --release-lock FILE --cache-dir DIR` | Fetch exact HTTPS artifacts, verify size/hash, safely extract. |
| `reproduce --release-lock FILE --outdir NEW_DIR` | Prepare locked source/SIF/data/reference/goldens, execute, and compare. Requires an actual release. |

Precedence is built-in defaults, selected profile, site configuration, parameter YAML, then CLI. Site files are trusted executable Nextflow configuration. Unknown scientific keys and duplicate YAML keys are errors. Relative paths in scientific parameters resolve against the invocation directory. Quote paths containing spaces. Metadata basenames and sample IDs have stricter safe naming rules.

Science defaults: algorithm `demo-cv-v1`, seed 42, odd Gaussian kernel 5, sigma 1.0, threshold 127, minimum area 20 pixels, connectivity 8. Allowed ranges and types are enforced in `src/reprohpc/config.py`; changing these requires contract tests. `batch_size` (1–64, default 16) and `write_previews` control execution/output organization. Scientific flags use hyphens; YAML keys use underscores.

Resource defaults: one CPU per analysis task, `--analysis-memory '2 GB'`, `--analysis-time '10 min'`, `--array-size 4`, `--max-inflight 8`. The test profile requests 512 MB/two minutes. Choose `--batch-size 1` explicitly for twelve demonstration tasks because a supplied YAML value takes precedence over profile defaults. Site policy may impose tighter running-job limits. `slurm_scalar` disables arrays for diagnostic comparison.

Every run needs a fresh output directory. Resume requires both `--resume SESSION_UUID` and the same `--launch-dir` and `--work-dir`; copy the printed command to retain all resolved overrides. `--sif-sha256` is mandatory for custom runs to make the selected bytes explicit.

Exit codes: 0 verified success; 2 usage/configuration; 3 invalid input/artifact; 4 engine/task/infrastructure; 5 comparison/finalization; 130 handled interruption. A task's internal exit 75 permits one retry and is retained in telemetry. See [operations](operations.md) for recovery.
