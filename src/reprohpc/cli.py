"""Thin validated Nextflow launcher. It does not schedule or cache tasks."""

import argparse
import ast
import json
import os
import platform
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from .archive import export_run, pack, prepare, validate_release
from .config import (
    DEFAULTS,
    EXECUTION,
    MRI_ALGORITHM,
    MRI_DEFAULTS,
    PATHS,
    load_yaml,
    resolve_params,
    science_params,
    validate_execution,
)
from .data import sample_kind, storage_estimate, validate_dataset, validate_reference
from .errors import ReproError
from .io import atomic_bytes, confined, fingerprint, read_json, sha256, write_json
from .provenance import (
    collect_accounting,
    collect_tasks,
    compare,
    contract,
    finalize_run,
    inventory,
    link_outputs,
    now,
    platform_info,
    validate_scientific,
    verify_run,
    write_checksums,
    write_tasks,
)
from .schema import validate

ROOT = Path(__file__).resolve().parents[2]


def command_output(argv, cwd=None):
    try:
        response = subprocess.run(
            argv, cwd=cwd, capture_output=True, text=True, check=False, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReproError(f"Could not execute {argv[0]}: {exc}", 4) from exc
    if response.returncode:
        raise ReproError(
            f"{argv[0]} failed: {response.stderr.strip() or response.stdout.strip()}", 4
        )
    return response.stdout.strip() or response.stderr.strip()


def doctor(profile="local"):
    found = {"python": platform.python_version(), "platform": platform.system()}
    errors = []
    if platform.system() != "Linux":
        errors.append("Execution requires Linux; use the documented Docker lab or Linux host")
    if sys.version_info < (3, 12):  # noqa: UP036 - source launcher can bypass packaging's version guard
        errors.append("Python >=3.12 is required")
    for name, argv, pattern in (
        ("nextflow", ["nextflow", "-version"], r"version 25\.10\.4"),
        ("java", ["java", "-version"], r'version "21\.'),
        ("apptainer", ["apptainer", "--version"], r"apptainer version 1\.5\.3"),
    ):
        try:
            found[name] = command_output(argv)
            if not re.search(pattern, found[name]):
                errors.append(f"Unsupported {name}: {found[name]}")
        except ReproError as exc:
            errors.append(str(exc))
    if profile.startswith("slurm"):
        for name in ("sbatch", "squeue", "sacct", "scancel", "scontrol"):
            if not shutil.which(name):
                errors.append(f"Missing Slurm command: {name}")
        if not errors:
            found["slurm"] = command_output(["scontrol", "--version"])
            found["slurm_config"] = command_output(["scontrol", "show", "config"])
    found["errors"] = errors
    found["ready"] = not errors
    return found


def source_snapshot(root, destination):
    selected = []
    generated = {
        "__pycache__",
        "node_modules",
        "test-results",
        "playwright-report",
        ".pytest_cache",
        ".ruff_cache",
    }
    for folder in (
        "src",
        "modules",
        "conf",
        "schemas",
        "scripts",
        "docs",
        "containers",
        "tests",
        "ops",
        ".github",
        "LICENSES",
        "params",
    ):
        if (root / folder).exists():
            for directory, directories, filenames in (root / folder).walk():
                directories[:] = [
                    name
                    for name in directories
                    if name not in generated and not name.endswith(".egg-info")
                ]
                selected.extend(
                    directory / name for name in filenames if (directory / name).is_file()
                )
    selected += [
        root / name
        for name in (
            "main.nf",
            "nextflow.config",
            "pyproject.toml",
            "requirements.lock",
            "requirements-host.in",
            "requirements-host.lock",
            "requirements.in",
            "requirements-dev.in",
            "requirements-dev.lock",
            "requirements-build.in",
            "requirements-build.lock",
            "requirements-benchmark.in",
            "requirements-benchmark.lock",
            "environment.lock.json",
            "reprohpc",
            "README.md",
            "PRD.md",
            "TASKS.md",
            "LICENSE",
            "THIRD_PARTY_NOTICES.md",
            "CITATION.cff",
            "CONTRIBUTING.md",
            ".dockerignore",
            ".gitignore",
            ".gitattributes",
        )
        if (root / name).is_file()
    ]
    manifest = {p.relative_to(root).as_posix(): sha256(p) for p in sorted(selected)}
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        for path in selected:
            target = folder / path.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        pack(folder, destination)
    try:
        # Trust only the explicitly selected checkout, without changing global Git settings.
        git = ["git", "-c", f"safe.directory={root}"]
        commit = command_output([*git, "rev-parse", "HEAD"], root)
        dirty = bool(command_output([*git, "status", "--porcelain"], root))
    except ReproError:
        commit = None
        dirty = None
    return {
        "source_commit": commit,
        "dirty": dirty,
        "source_tree_sha256": fingerprint(manifest),
        "source_archive_sha256": sha256(destination),
    }


def resolve_site(args, root):
    configs = [str(root / "nextflow.config")]
    if args.site_config:
        configs.append(str(args.site_config.resolve()))
    profile = args.profile + (",test" if args.test else "")
    flat = command_output(
        ["nextflow", "-C", ",".join(configs), "config", "-profile", profile, "-flat"], root
    )
    science_defaults = {"batch_size": 1} if args.test else {}
    settings = {
        **EXECUTION,
        **({"analysis_memory": "512 MB", "analysis_time": "2 min"} if args.test else {}),
    }
    for line in flat.splitlines():
        match = re.match(r"params\.([a-z_]+) = (.*)$", line)
        if match:
            key, value = match.groups()
            if key not in set(DEFAULTS) | set(MRI_DEFAULTS) | set(EXECUTION) | PATHS:
                continue
            try:
                value = ast.literal_eval(
                    {"true": "True", "false": "False", "null": "None"}.get(value, value)
                )
            except (ValueError, SyntaxError) as exc:
                raise ReproError(f"Site parameter {key} must resolve to a scalar", 2) from exc
            if key in EXECUTION:
                settings[key] = value
            else:
                science_defaults[key] = value
    overrides = {
        key: getattr(args, key)
        for key in DEFAULTS | MRI_DEFAULTS | dict.fromkeys(PATHS)
        if getattr(args, key, None) is not None
    }
    supplied = {**science_defaults, **load_yaml(args.params_file), **overrides}
    # The test overlay intentionally selects batch size 1 unless explicitly overridden by CLI.
    # A parameter file still has higher precedence; tutorial test params supplies batch_size=1.
    params = resolve_params(supplied, base=Path.cwd())
    for key in EXECUTION:
        value = getattr(args, key, None)
        if value is not None:
            settings[key] = value
    return params, validate_execution(settings), configs, profile, flat


def engine_science(params):
    """The science block Nextflow hands to every task. Tasks re-validate it with
    resolve_params, so it may carry only keys the routine's own contract accepts."""
    science = {**science_params(params), "batch_size": params["batch_size"]}
    if "write_previews" in params:
        science["write_previews"] = params["write_previews"]
    return science


def run(args, root=ROOT):
    tools = doctor(args.profile)
    if tools["errors"]:
        raise ReproError("; ".join(tools["errors"]), 4)
    params, settings, configs, profile, flat = resolve_site(args, root)
    sif = args.sif.resolve()
    if (
        not sif.is_file()
        or not re.fullmatch(r"[0-9a-f]{64}", args.sif_sha256 or "")
        or sha256(sif) != args.sif_sha256
    ):
        raise ReproError("SIF missing or checksum differs from --sif-sha256")
    for key in ("dataset", "reference"):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", Path(params[key]).name):
            raise ReproError(f"Unsafe metadata basename for {key}", 2)
    # Only metadata and fixed-size headers are read on the login node. The scheduled
    # validator hashes and decodes all image bytes before emitting analysis channels.
    dataset, samples = validate_dataset(
        Path(params["dataset"]), Path(params["input_manifest"]), preflight=True
    )
    wanted = "nifti" if params["algorithm"] == MRI_ALGORITHM else "png"
    if sample_kind(samples) != wanted:
        raise ReproError(
            f"{params['algorithm']} requires {wanted} inputs; the manifest lists "
            f"{sample_kind(samples)} files",
            2,
        )
    reference, calibration = validate_reference(Path(params["reference"]))
    out = args.outdir.resolve()
    work = args.work_dir.resolve()
    if out.exists():
        raise ReproError(f"Output already exists: {out}; use a new directory", 2)
    for path in (out, work, sif):
        if any(c in str(path) for c in "\r\n\x00"):
            raise ReproError("Invalid execution path", 2)
    out.parent.mkdir(parents=True, exist_ok=True)
    if (
        storage_estimate(samples, params.get("write_previews", False))
        > shutil.disk_usage(out.parent).free
    ):
        raise ReproError("Insufficient storage for decoded images, work retention, and outputs")
    if args.profile.startswith("slurm"):
        match = re.search(r"MaxArraySize\s*=\s*(\d+)", tools["slurm_config"])
        if match and settings["array_size"] > int(match.group(1)):
            raise ReproError("array_size exceeds Slurm MaxArraySize", 2)
        command_output(["scontrol", "show", "partition", settings["partition"]])
    launch = args.launch_dir.resolve()
    launch.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    if args.resume and not (launch / ".nextflow/cache" / args.resume).is_dir():
        raise ReproError("Resume session cache is missing from --launch-dir", 2)
    lock = launch / "active.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ReproError(
            f"Driver lock exists: {lock}; inspect the recorded PID before removing a stale lock", 4
        ) from exc
    os.write(fd, f"{os.getpid()}\n".encode())
    os.close(fd)
    try:
        return _execute(
            args,
            root,
            params,
            settings,
            configs,
            profile,
            flat,
            tools,
            dataset,
            samples,
            reference,
            calibration,
            sif,
            out,
            work,
            launch,
        )
    except Exception as exc:
        # Preparation, engine startup, and finalization failures retain a terminal
        # status even when the engine has not produced a complete run record.
        if (out / "status.json").is_file():
            status = read_json(out / "status.json")
            status.update(status="failed", exit_code=getattr(exc, "code", 4), message=str(exc))
            write_json(out / "status.json", status)
            write_checksums(out)
        raise
    finally:
        lock.unlink(missing_ok=True)


def _execute(
    args,
    root,
    params,
    settings,
    configs,
    profile,
    flat,
    tools,
    dataset,
    samples,
    reference,
    calibration,
    sif,
    out,
    work,
    launch,
):
    out.mkdir()
    (out / "provenance").mkdir()
    (out / "logs").mkdir()
    run_id = str(uuid.uuid4())
    started = now()
    status = {
        "schema_version": "1.0.0",
        "run_id": run_id,
        "status": "running",
        "exit_code": None,
        "message": "",
    }
    write_json(out / "status.json", status)
    source = source_snapshot(root, out / "provenance/source.tar.gz")
    if args.release_lock:
        release = validate_release(args.release_lock)
        if source["dirty"] or source["source_archive_sha256"] != release["source"]["sha256"]:
            raise ReproError("Release source differs from the exact archived source", 3)
        if args.sif_sha256 != release["sif"]["sha256"]:
            raise ReproError("Release SIF identity differs", 3)
        source["source_commit"] = release["source_commit"]
        source["dirty"] = False
        shutil.copy2(args.release_lock, out / "provenance/release.lock.json")
    source.update(
        sif_sha256=args.sif_sha256,
        tools={k: v for k, v in tools.items() if k not in ("errors", "slurm_config")},
    )
    specification = {
        "schema_version": "1.0.0",
        "algorithm": params["algorithm"],
        "source_tree_sha256": source["source_tree_sha256"],
        "sif_sha256": args.sif_sha256,
        "science": science_params(params),
        "samples": samples,
        "reference": reference,
        "calibration": calibration,
    }
    validate(contract("analysis", params["algorithm"]), specification)
    write_json(out / "provenance/analysis.json", specification)
    validate(contract("params", params["algorithm"]), params)
    for key, name in (
        ("dataset", "dataset.json"),
        ("input_manifest", "samples.csv"),
        ("reference", "reference.json"),
    ):
        shutil.copy2(params[key], out / "provenance" / name)
    shutil.copy2(
        confined(Path(params["reference"]).parent, reference["calibration"]["path"]),
        out / "provenance/calibration.json",
    )
    write_json(out / "provenance/params.resolved.json", params)
    atomic_bytes(out / "provenance/site.config.txt", flat.encode())
    effective = {
        **settings,
        "invocation_run_id": run_id,
        "outdir": str(out),
        "sif_path": str(sif),
        "sif_sha256": args.sif_sha256,
        "reference_sha256": fingerprint({"reference": reference, "calibration": calibration}),
        "dataset": params["dataset"],
        "input_manifest": params["input_manifest"],
        "reference": params["reference"],
        "batch_size": params["batch_size"],
        "science": engine_science(params),
    }
    effective_path = out / "provenance/engine.params.json"
    write_json(effective_path, effective)
    argv = [
        "nextflow",
        "-C",
        ",".join(configs),
        "-log",
        str(out / "logs/driver.log"),
        "run",
        str(root / "main.nf"),
        "-profile",
        profile,
        "-params-file",
        str(effective_path),
        "-work-dir",
        str(work),
        "-ansi-log",
        "false",
    ]
    if args.resume:
        argv += ["-resume", args.resume]
    print(f"Run {run_id}\nOutput: {out}", flush=True)
    env = {
        **os.environ,
        "NXF_OFFLINE": "true",
        "NXF_OPTS": "-Xmx1g",
        "PYTHONHASHSEED": "0",
        "TZ": "UTC",
    }
    with (out / "logs/console.log").open("w") as log:
        child = subprocess.Popen(
            argv, cwd=launch, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
        )
        interrupted = False
        original = {}

        def stop(signum, frame):
            nonlocal interrupted
            interrupted = True
            os.killpg(child.pid, signal.SIGTERM)

        for signum in (signal.SIGINT, signal.SIGTERM):
            original[signum] = signal.signal(signum, stop)
        try:
            exit_code = child.wait()
        finally:
            for signum, handler in original.items():
                signal.signal(signum, handler)
    engine = (
        read_json(out / "provenance/engine.json")
        if (out / "provenance/engine.json").exists()
        else {}
    )
    if not engine.get("session_id") and (out / "logs/driver.log").exists():
        match = re.search(r"Session UUID: ([0-9a-f-]{36})", (out / "logs/driver.log").read_text())
        if match:
            engine["session_id"] = match.group(1)
    code = 130 if interrupted else (0 if exit_code == 0 and engine.get("success") else 4)
    status.update(
        status="cancelled" if interrupted else ("success" if code == 0 else "failed"),
        exit_code=code,
    )
    tasks = []
    try:
        tasks = collect_tasks(out, run_id, engine.get("session_id"))
        if code == 4 and any(
            t["process"].endswith("VALIDATE_DATASET") and t["exit_code"] == 3 for t in tasks
        ):
            code = 3
            status["exit_code"] = 3
        if args.profile.startswith("slurm"):
            collect_accounting(tasks, out)
        write_tasks(out, tasks)
        if code == 0:
            validate_scientific(out)
            if not tasks or any(
                t["status"] not in ("COMPLETED", "CACHED", "FAILED") for t in tasks
            ):
                raise ReproError("Incomplete task trace", 5)
    except Exception as exc:
        if code == 0:
            code = 5
            status.update(status="finalization-failed", exit_code=5)
        status["message"] = str(exc)
    analysis_id = fingerprint(
        {
            "params": science_params(params),
            "sif_sha256": args.sif_sha256,
            "source": source["source_tree_sha256"],
            "samples": samples,
            "reference": reference,
        }
    )
    run_record = {
        "schema_version": "1.0.0",
        "run_id": run_id,
        "nextflow_session_id": engine.get("session_id"),
        "resumed_from": args.resume,
        "started": started,
        "finished": now(),
        "analysis_fingerprint": analysis_id,
        "command": argv,
        "params": params,
        "execution": {
            **settings,
            "profile": profile,
            "work_dir": str(work),
            "engine_exit_code": exit_code,
        },
        "software": source,
        "platform": platform_info(),
        "dataset": dataset,
        "reference": reference,
        "status": status,
    }
    try:
        outputs = link_outputs(out, tasks) if code == 0 else inventory(out, scientific=True)
    except Exception as exc:
        code = 5
        status.update(status="finalization-failed", exit_code=5, message=str(exc))
        outputs = inventory(out, scientific=True)
    write_json(out / "provenance/outputs.json", outputs)
    run_record["execution"]["published_bytes"] = sum(e["size_bytes"] for e in inventory(out))
    run_record["execution"]["retained_work_bytes"] = sum(
        p.stat().st_size
        for t in tasks
        for p in Path(t["origin_work_dir"]).rglob("*")
        if p.is_file() and not p.is_symlink()
    )
    validate(contract("run", params["algorithm"]), run_record)
    if code == 0:
        try:
            finalize_run(out, run_record)
        except Exception as exc:
            code = 5
            status.update(status="finalization-failed", exit_code=5, message=str(exc))
    if code != 0:
        write_json(out / "provenance/run.json", run_record)
        write_json(out / "status.json", status)
        write_checksums(out)
    session = engine.get("session_id")
    print(f"Status: {status['status']}; session: {session}; report: {out / 'report/index.html'}")
    if session:
        resume = [
            str(root / "reprohpc"),
            "run",
            "--profile",
            args.profile,
            "--params-file",
            str(out / "provenance/params.resolved.json"),
            "--sif",
            str(sif),
            "--sif-sha256",
            args.sif_sha256,
            "--resume",
            session,
            "--outdir",
            str(out) + "-resumed",
            "--work-dir",
            str(work),
            "--launch-dir",
            str(launch),
        ]
        if args.site_config:
            resume += ["--site-config", str(args.site_config.resolve())]
        if args.release_lock:
            resume += ["--release-lock", str(args.release_lock.resolve())]
        for key, value in settings.items():
            resume += ["--" + key.replace("_", "-"), str(value)]
        print("Resume: " + shlex.join(resume))
    return code


def parser():
    p = argparse.ArgumentParser(
        description="Reproducible image analysis through Nextflow and Apptainer"
    )
    subs = p.add_subparsers(dest="command", required=True)
    d = subs.add_parser("doctor")
    d.add_argument("--profile", choices=["local", "slurm", "slurm_scalar"], default="local")
    r = subs.add_parser("run")
    r.add_argument("--profile", choices=["local", "slurm", "slurm_scalar"], default="local")
    r.add_argument("--site-config", type=Path)
    r.add_argument("--params-file", type=Path, required=True)
    r.add_argument("--outdir", type=Path, required=True)
    r.add_argument("--work-dir", type=Path, default=Path("work"))
    r.add_argument("--launch-dir", type=Path, default=Path(".reprohpc/launch"))
    r.add_argument("--sif", type=Path, required=True)
    r.add_argument("--sif-sha256", required=True)
    r.add_argument("--resume")
    r.add_argument("--release-lock", type=Path)
    r.add_argument("--test", action="store_true")
    for key, value in (DEFAULTS | MRI_DEFAULTS).items():
        if key == "algorithm":
            r.add_argument("--algorithm", choices=["demo-cv-v1", MRI_ALGORITHM])
            continue
        flag = "--" + key.replace("_", "-")
        if type(value) is bool:
            r.add_argument(flag, action=argparse.BooleanOptionalAction)
        else:
            r.add_argument(flag, type=type(value))
    for key in PATHS:
        r.add_argument("--" + key.replace("_", "-"))
    for key, value in EXECUTION.items():
        r.add_argument("--" + key.replace("_", "-"), type=type(value))
    v = subs.add_parser("verify")
    v.add_argument("--run", type=Path, required=True)
    c = subs.add_parser("compare")
    c.add_argument("--expected", type=Path, required=True)
    c.add_argument("--actual", type=Path, required=True)
    c.add_argument("--exact", action="store_true")
    e = subs.add_parser("export")
    e.add_argument("--run", type=Path, required=True)
    e.add_argument("--output", type=Path, required=True)
    for name in ("prepare", "reproduce"):
        a = subs.add_parser(name)
        a.add_argument("--release-lock", type=Path, required=True)
        a.add_argument("--cache-dir", type=Path, default=Path("artifacts"))
        if name == "reproduce":
            a.add_argument("--profile", choices=["local", "slurm", "slurm_scalar"], default="local")
            a.add_argument("--outdir", type=Path, required=True)
            a.add_argument("--site-config", type=Path)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "doctor":
            result = doctor(args.profile)
            print(json.dumps(result, indent=2))
            return 0 if result["ready"] else 4
        if args.command == "run":
            return run(args)
        if args.command == "verify":
            result = verify_run(args.run.resolve())
        elif args.command == "compare":
            result = compare(args.expected.resolve(), args.actual.resolve(), exact=args.exact)
        elif args.command == "export":
            result = export_run(args.run.resolve(), args.output.resolve())
        elif args.command in ("prepare", "reproduce"):
            lock, result = prepare(args.release_lock, args.cache_dir.resolve())
            if args.command == "reproduce":
                params = lock["params"].copy()
                params.update(
                    dataset=str(Path(result["data"]) / "dataset.json"),
                    input_manifest=str(Path(result["data"]) / "samples.csv"),
                    reference=str(Path(result["reference"]) / "reference.json"),
                )
                paramfile = args.cache_dir.resolve() / "reproduction.params.json"
                write_json(paramfile, params)
                arguments = [
                    "run",
                    "--profile",
                    args.profile,
                    "--params-file",
                    str(paramfile),
                    "--outdir",
                    str(args.outdir.resolve()),
                    "--sif",
                    result["sif"],
                    "--sif-sha256",
                    lock["sif"]["sha256"],
                    "--release-lock",
                    str(args.release_lock.resolve()),
                    "--work-dir",
                    str(args.cache_dir.resolve() / "reproduction-work"),
                    "--launch-dir",
                    str(args.cache_dir.resolve() / "reproduction-launch"),
                ]
                if args.site_config:
                    arguments += ["--site-config", str(args.site_config.resolve())]
                # The verified archive's own launcher and validators run the release.
                code = subprocess.run(
                    [sys.executable, str(Path(result["source"]) / "reprohpc"), *arguments],
                    cwd=result["source"],
                    check=False,
                ).returncode
                if code:
                    return code
                result = compare(Path(result["expected"]), args.outdir.resolve())
        print(json.dumps(result, indent=2))
        return 0
    except ReproError as exc:
        print(str(exc), file=sys.stderr)
        return exc.code
    except (OSError, ValueError) as exc:
        print(f"Operation failed: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
