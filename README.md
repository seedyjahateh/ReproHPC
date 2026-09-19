# ReproHPC
A reproducible scientific imaging pipeline using Python/OpenCV, Nextflow DSL2, Apptainer, and Slurm. Versioned inputs, deterministic scientific outputs, and run records let researchers trace measurements and repeat an analysis.

See the [Product Requirements Document](PRD.md) for the MVP scope, architecture, testable requirements, acceptance criteria, and delivery plan.

This is a development candidate. [TASKS.md](TASKS.md) records verification and open release gates. No software/data DOI has been published. The supplied routine is the documented synthetic `demo-cv-v1` reference; no domain routine was supplied. Open OnDemand is an optional future integration.

## Start here

Prerequisites: a Linux x86-64 host, or Docker Desktop with Linux containers; Git; Python 3.12 for the build helpers. Allow at least 100 GiB free disk. The PRD benchmark environment requires 16 GiB RAM and eight logical CPUs. Installation/download time is separate from the tutorial.

Follow [installation](docs/installation.md) to create the disposable Linux lab and SIF. Run tutorial commands in the lab as `researcher`, from `/workspace`:

```bash
docker exec -it -u researcher reprohpc-lab bash
python reprohpc doctor --profile local
SIF=/scratch/reprohpc.sif
SIF_SHA=$(sha256sum "$SIF" | cut -d ' ' -f1)
python reprohpc run --profile local --params-file params/demo.yaml \
  --sif "$SIF" --sif-sha256 "$SIF_SHA" \
  --outdir /scratch/tutorial/first --work-dir /scratch/tutorial/work \
  --launch-dir /scratch/tutorial/launch
python reprohpc verify --run /scratch/tutorial/first
python reprohpc compare --expected tests/expected/demo --actual /scratch/tutorial/first
```

These commands execute all four workflow steps through Apptainer. A successful invocation prints `Status: success`, an output path, and a resume command. Copy the result directory to the host with `docker cp reprohpc-lab:/scratch/tutorial/first artifacts/tutorial-result` and open `artifacts/tutorial-result/report/index.html`. Keeping the directory structure preserves links to the tables and provenance.

## Tutorial: ask, predict, run, inspect

The [results workspace](docs/report.md) provides an overview, searchable image explorer, sortable measurements, and scientific provenance. Open the report directly or serve the completed run locally; all views work offline.

| Episode | Time budget after installation | Question and exercise | Check |
|---|---|---|---|
| Understand inputs | 3 min | Open `data/demo/1.0.0/samples.csv`, `dataset.json`, and the calibration reference. Which bytes are versioned? | Locate a sample SHA-256, the manifest hash, and 0.5 micrometers/pixel calibration. |
| First analysis | 7 min | Run the commands above; predict whether the blank image has objects. | Twelve sample folders; blank image has count zero, null mean area, and `NO_OBJECTS`. |
| Interpret results | 4 min | Read the report, `summary/images.csv`, and `samples/square/objects.csv`. | Explain pixel versus square-micrometer area and a border QC flag. |
| Change science | 4 min | Repeat with `--threshold 200` and a new output directory. Predict changes before comparing. | Resolved parameters contain 200; a changed scientific result is expected and must not pass the default golden comparison. |
| Recover work | 5 min | Use the exact printed resume command, retaining the launch and work directories. | Intact analysis tasks say `CACHED` in `provenance/trace.tsv`. See the interrupted-run exercise in the operations guide. |
| Preserve and cite | 3 min | Run export below; inspect the archived source and manifests. | Verification succeeds before export. Citation identifies this unpublished candidate accurately. |

```bash
python reprohpc export --run /scratch/tutorial/first --output /scratch/tutorial/first.tar.gz
```

For Slurm, first start its services as described in installation, then use `--profile slurm --account research --partition demo`. Scientific parameters and the SIF stay the same. Inspect `squeue -u researcher` while it runs and `provenance/sacct.tsv` afterwards.

The eventual published release will support `python reprohpc reproduce --release-lock release.lock.json --profile local --outdir results/reproduction`. This requires a real published lock; the repository deliberately contains no invented DOI or placeholder lock.

## Guides and contracts

- [Architecture and DAG](docs/architecture.md), [algorithm](docs/algorithm.md), [real microscopy data](docs/real-data.md), [data dictionary](docs/data-dictionary.md), [JSON schemas](schemas/).
- [Installation](docs/installation.md), [CLI/configuration](docs/interface.md), [operations and recovery](docs/operations.md), [testing](docs/testing.md).
- [Benchmark methodology](docs/benchmark.md), [release procedure](docs/release.md), [acceptance evidence](docs/acceptance.md), [contributing](CONTRIBUTING.md).
- [FAIR/NIH DMS alignment](docs/dms-alignment.md), [citation](CITATION.cff), [software license](LICENSE), [third-party notices](THIRD_PARTY_NOTICES.md).
