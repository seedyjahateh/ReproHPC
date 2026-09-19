# Testing

## Report browser checks

The browser suite uses pinned development-only Playwright and axe dependencies in `tests/ui/package-lock.json`. Install Node.js 22 or newer and the Python scientific/development dependencies, then run from the repository root:

```bash
python scripts/report_fixtures.py --output artifacts/ui-fixtures
npm ci --prefix tests/ui
cd tests/ui
npx playwright install --with-deps chromium
npm test
```

Fixture generation refuses to overwrite an existing directory. Use a new path and set `REPROHPC_REPORT_FIXTURES` to its absolute location when repeating after changes. On a machine with Chrome already installed, set `REPROHPC_BROWSER_CHANNEL=chrome` instead of installing Chromium. The suite opens local files, disables the network for the view audit, exercises all four views at desktop and mobile sizes, checks keyboard navigation and pagination, and runs axe WCAG A/AA checks. It retains screenshots in `evidence/ui/` and machine-readable results in `evidence/ui-browser-results.json`. Every invocation replaces that results file, including one that fails because no browser is installed; rerun the complete suite before citing it. Automated checks supplement visual inspection and do not constitute complete accessibility certification. See [Playwright browser launch](https://playwright.dev/docs/api/class-browsertype#browser-type-launch) and [accessibility testing](https://playwright.dev/docs/accessibility-testing).

Browser fixtures run the actual Python image routine. Larger fixtures alias a measured blank image solely to exercise display cardinality, long sample IDs, null means, and preview limits. They are explicitly separate from real Nextflow/Apptainer integration evidence. The CI pipeline runs the browser checks after the container and scientific checks.

## Scientific and integration checks

Unit tests use an independent scalar Gaussian convolution and flood-fill oracle. They exercise science, parameter/data contracts, aggregation, verifier tampering, archive safety, and CLI errors. They do not stand in for Nextflow, Apptainer, or Slurm.

```bash
python -m pytest tests/unit -q
python -m ruff check src tests scripts ops/slurm/init_cgroups.py
python -m ruff format --check src tests scripts ops/slurm/init_cgroups.py
python -m build
REPROHPC_SIF=/scratch/reprohpc.sif python -m pytest tests/integration -vv --basetemp=/scratch/integration
```

The configured SIF must really exist. Without `REPROHPC_SIF`, integration tests explicitly skip, so a green unit-only run cannot satisfy integration acceptance. `scripts/check.sh` runs lint, build, requirements audit, unit and integration tests with coverage, then enforces at least 85% statements and 75% branches. Keep its XML/JUnit outputs. Python coverage does not measure DSL2 correctness; real workflow acceptance is separate.

CI definitions use pinned action commits, hashed Python dependencies, an actual candidate image build, and actual Apptainer pipeline execution. The release workflow fails closed until a real release lock and all evidence are available. Writing a workflow is distinct from successfully running it on GitHub; record its URL/commit when executed.

The [pinned cache action](https://github.com/actions/cache/tree/0400d5f644dc74513175e3cd8d07132dd4860809) retains tool-download inputs under an immutable environment/dependency/OS/architecture key. Every restored tool is checksum-validated by `build_lab.py`. Workflow work/cache directories are never restored across changes. `collect_evidence.py --include-logs` preserves synthetic CI commands, stdout/stderr, traces and status on success and failure; omit that option for unreviewed operational runs. Hosted warm/cold time budgets require actual measurement.

Unit and integration tests use the synthetic fixtures only; they never download anything. Scoring the algorithm against real expert annotations is a separate, optional, network-dependent step documented in [real microscopy data](real-data.md), and its numbers are evidence about the algorithm, not a pass/fail gate in the suite.

Never regenerate goldens from the implementation to fix a mismatch. Diagnose algorithm changes, test independent fixtures, and version scientific contracts together. Fault injection belongs in test-only engine/site configuration and wrappers; production science must not contain sleep, fake success, or scheduler-mocking paths.


For prepared offline acceptance, run `python scripts/offline_acceptance.py --sif /scratch/reprohpc-candidate-5.sif --sha256 CANDIDATE_SHA256 --output /scratch/unique-offline-run` from the Docker host, replacing `CANDIDATE_SHA256` with the actual checksum from the candidate build inventory. The script isolates only the named disposable lab on a temporary internal network, verifies no default route and failed external connections, executes local and Slurm, compares outputs, then restores networking. It preserves an IPv4 interface for Slurm service discovery. See [Docker internal-network behavior](https://docs.docker.com/reference/cli/docker/network/create/#network-internal-mode---internal). This is a lab test helper, not a university-network configuration procedure.
