"""Acceptance evidence for fsl-bet-volumetry-v1 on real Nextflow, Apptainer and Slurm.

Runs the pipeline twice locally in independent work, launch and output directories, then
once on Slurm, all with the same SIF and inputs. Each run must print `Status: success` and
pass `reprohpc verify`. Determinism is compared exactly (every scientific file byte for
byte) and by voxel content. The golden is written once, from the first verified run, and
holds hashes and numbers only; later runs are compared against it. Run inside the lab:

    python scripts/mri_acceptance.py --sif /scratch/reprohpc-fsl-candidate-3.sif \
        --output /scratch/mri-acceptance-candidate-3 --slurm [--write-golden]
"""

import argparse
import csv
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from reprohpc.config import science_params  # noqa: E402
from reprohpc.io import read_json, read_nifti_mask, sha256, write_json  # noqa: E402
from reprohpc.provenance import compare, verify_run  # noqa: E402

GOLDEN = ROOT / "tests/expected/openneuro/ds000001/1.0.0"
PARAMS = ROOT / "params/openneuro.yaml"


def pipeline(sif, digest, directory, profile):
    command = [
        sys.executable, str(ROOT / "reprohpc"), "run", "--profile", profile,
        "--params-file", str(PARAMS), "--sif", str(sif), "--sif-sha256", digest,
        "--outdir", str(directory / "run"), "--work-dir", str(directory / "work"),
        "--launch-dir", str(directory / "launch"),
    ]  # fmt: skip
    if profile == "slurm":
        command += ["--account", "research", "--partition", "demo"]
    directory.mkdir(parents=True)
    started = time.monotonic()
    with (directory / "console.log").open("w") as log:
        code = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT).returncode
    console = (directory / "console.log").read_text()
    status = re.search(r"^Status: (\S+);", console, re.M)
    if code != 0 or not status or status[1] != "success":
        raise SystemExit(f"{profile} run failed (exit {code}); see {directory / 'console.log'}")
    run = directory / "run"
    return {
        "profile": profile,
        "command": command,
        "printed_status": status[1],
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "run_id": read_json(run / "status.json")["run_id"],
        "nextflow_session_id": read_json(run / "provenance/run.json")["nextflow_session_id"],
        "verification": verify_run(run),
    }


def recorded(expected, actual, exact):
    """Compare, recording a failure as evidence instead of aborting before the voxel check."""
    from reprohpc.errors import ReproError

    try:
        return compare(expected, actual, exact=exact)
    except ReproError as exc:
        return {"equivalent": False, "mode": "exact" if exact else "tolerance", "error": str(exc)}


def subjects(run):
    table = {}
    for folder in sorted((run / "samples").iterdir()):
        metric = read_json(folder / "metrics.json")
        mask = read_nifti_mask(folder / "brain_mask.nii.gz")
        table[folder.name] = {
            "dims": metric["dims"],
            "brain_voxels": metric["brain_voxels"],
            "brain_volume_mm3": metric["brain_volume_mm3"],
            "mask_voxel_sha256": mask.voxel_sha256,
            "mask_file_sha256": sha256(folder / "brain_mask.nii.gz"),
            "qc": metric["qc"],
        }
    return table


def fsl_identity(work):
    identities = {
        json.dumps(read_json(path)["fsl"], sort_keys=True)
        for path in work.glob("*/*/result/task.json")
    }
    if len(identities) != 1:
        raise SystemExit(f"Expected one FSL identity across tasks, found {len(identities)}")
    return json.loads(identities.pop())


def accounting(run):
    """Slurm's own records for this run's MRI_BATCH jobs, from provenance/sacct.tsv."""
    sacct = run / "provenance/sacct.tsv"
    lines = [line.split("|") for line in sacct.read_text().splitlines() if line.strip()]
    with (run / "provenance/trace.tsv").open(newline="") as stream:
        jobs = {
            row["native_id"]
            for row in csv.DictReader(stream, delimiter="\t")
            if row["process"].endswith("MRI_BATCH")
        }
    allocations = [
        fields for fields in lines if fields[0].split(".")[0] in jobs and "." not in fields[0]
    ]
    return {
        "sacct_rows": len(lines),
        "mri_batch_jobs": sorted(jobs),
        "allocation_states": sorted({fields[1] for fields in allocations}),
        "allocation_exit_codes": sorted({fields[2] for fields in allocations}),
        "every_job_accounted": {f[0].split(".")[0] for f in allocations} >= jobs,
    }


def baked_source(sif):
    inspection = (
        "import hashlib,json,pathlib,reprohpc\n"
        "root=pathlib.Path(reprohpc.__file__).parent\n"
        "print(json.dumps({p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()"
        " for p in sorted(root.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}))\n"
    )
    baked = json.loads(
        subprocess.check_output(
            ["apptainer", "exec", "--cleanenv", str(sif), "python", "-c", inspection], text=True
        )
    )
    local = {
        path.relative_to(ROOT / "src/reprohpc").as_posix(): sha256(path)
        for path in sorted((ROOT / "src/reprohpc").rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }
    return baked == local


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sif", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--slurm", action="store_true")
    parser.add_argument("--write-golden", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    digest = sha256(args.sif)
    if not baked_source(args.sif):
        raise SystemExit(
            "SIF package differs from the checkout; rebuild before collecting evidence"
        )

    runs = {
        "local_a": pipeline(args.sif, digest, args.output / "local-a", "local"),
        "local_b": pipeline(args.sif, digest, args.output / "local-b", "local"),
    }
    a, b = args.output / "local-a/run", args.output / "local-b/run"
    determinism = {
        "exact_bytes": recorded(a, b, exact=True),
        "voxel_and_tolerance": recorded(a, b, exact=False),
        "per_subject": {
            sid: {
                "mask_voxel_sha256_equal": row["mask_voxel_sha256"] == other["mask_voxel_sha256"],
                "mask_file_sha256_equal": row["mask_file_sha256"] == other["mask_file_sha256"],
                "brain_voxels_equal": row["brain_voxels"] == other["brain_voxels"],
                "brain_volume_abs_difference": abs(
                    row["brain_volume_mm3"] - other["brain_volume_mm3"]
                ),
            }
            for (sid, row), other in zip(subjects(a).items(), subjects(b).values(), strict=True)
        },
    }
    fsl = fsl_identity(args.output / "local-a/work")
    if args.write_golden:
        if (GOLDEN / "expected.json").exists():
            raise SystemExit(f"Golden already exists: {GOLDEN / 'expected.json'}")
        params = read_json(a / "provenance/params.resolved.json")
        dataset = read_json(a / "provenance/dataset.json")
        write_json(
            GOLDEN / "expected.json",
            {
                "algorithm": params["algorithm"],
                "description": (
                    "Regression golden written once from a verified run. Hashes and numbers "
                    "only; no image data. It anchors reproducibility; it is not an independent "
                    "oracle of correct brain extraction."
                ),
                "source_run_id": runs["local_a"]["run_id"],
                "sif_sha256": digest,
                "fsl": fsl["version_string"],
                "parameters": science_params(params),
                "dataset": {
                    "accession": dataset["dataset_id"],
                    "version": dataset["version"],
                    "manifest_sha256": dataset["manifest_sha256"],
                },
                "subjects": {
                    sid: {
                        key: row[key]
                        for key in ("dims", "brain_voxels", "brain_volume_mm3", "mask_voxel_sha256")
                    }
                    for sid, row in subjects(a).items()
                },
            },
        )
    golden = {"local_a": recorded(GOLDEN, a, True), "local_b": recorded(GOLDEN, b, True)}

    slurm = None
    if args.slurm:
        runs["slurm"] = pipeline(args.sif, digest, args.output / "slurm", "slurm")
        s = args.output / "slurm/run"
        golden["slurm"] = recorded(GOLDEN, s, True)
        slurm = {
            "exact_bytes_vs_local_a": recorded(a, s, exact=True),
            "accounting": accounting(s),
        }

    dataset = read_json(a / "provenance/dataset.json")
    record = {
        "kind": "fsl-bet-volumetry-v1-acceptance",
        "sif_sha256": digest,
        "baked_source_verified": True,
        "fsl": fsl,
        "dataset": {
            "accession": dataset["dataset_id"],
            "version": dataset["version"],
            "doi": dataset["doi"],
            "license": dataset["license"],
            "manifest_sha256": dataset["manifest_sha256"],
        },
        "parameters": science_params(read_json(a / "provenance/params.resolved.json")),
        "runs": runs,
        "subjects": subjects(a),
        "determinism_local": determinism,
        "golden_comparisons": golden,
        "slurm": slurm,
    }
    checks = {
        "local exact bytes": determinism["exact_bytes"]["equivalent"],
        "local voxels and volumes": determinism["voxel_and_tolerance"]["equivalent"],
        **{f"golden vs {name}": result["equivalent"] for name, result in golden.items()},
    }
    if slurm:
        checks["slurm exact bytes vs local"] = slurm["exact_bytes_vs_local_a"]["equivalent"]
        checks["slurm jobs accounted"] = slurm["accounting"]["every_job_accounted"]
    record["checks"] = checks
    write_json(args.output / "evidence.json", record)
    print(json.dumps(checks, indent=2))
    if not all(checks.values()):
        raise SystemExit("Acceptance failed; details are in evidence.json")


if __name__ == "__main__":
    main()
