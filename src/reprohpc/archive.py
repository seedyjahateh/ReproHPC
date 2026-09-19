"""Checksum-locked preparation and safe public exports; no publishing credentials."""

import gzip
import json
import re
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from .errors import ReproError
from .io import atomic_bytes, confined, read_json, sha256, write_json
from .metadata import write_public_metadata
from .provenance import verify_run, write_checksums
from .schema import validate


def pack(root, destination):
    """Canonical archive metadata; scientific bytes are copied unchanged."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile() as buffer:
        with gzip.GzipFile(fileobj=buffer, mode="wb", filename="", mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w|") as tar:
                for path in sorted(root.rglob("*")):
                    if not path.is_file():
                        continue
                    if path.is_symlink():
                        raise ReproError(f"Refusing to archive symlink: {path}", 5)
                    info = tarfile.TarInfo(path.relative_to(root).as_posix())
                    info.size = path.stat().st_size
                    info.mode = 0o755 if path.name == "reprohpc" or path.suffix == ".sh" else 0o644
                    with path.open("rb") as stream:
                        tar.addfile(info, stream)
        buffer.seek(0)
        # Stream large archives to disk instead of buffering their contents in RAM.
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as tmp:
            shutil.copyfileobj(buffer, tmp)
            temporary = Path(tmp.name)
        temporary.replace(destination)
    atomic_bytes(
        destination.with_suffix(destination.suffix + ".sha256"),
        f"{sha256(destination)}  {destination.name}\n".encode(),
    )


def extract(archive, destination, limit_bytes=100 * 1024**3):
    destination.mkdir(parents=True, exist_ok=False)
    total = 0
    try:
        with tarfile.open(archive, "r:gz") as tar:
            names = set()
            for member in tar:
                target = confined(destination, member.name)
                if member.name in names or not (member.isfile() or member.isdir()):
                    raise ReproError(f"Unsafe/duplicate archive member: {member.name}")
                names.add(member.name)
                total += member.size
                if total > limit_bytes:
                    raise ReproError("Archive exceeds extraction limit")
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with tar.extractfile(member) as source, target.open("xb") as output:
                        shutil.copyfileobj(source, output)
                    target.chmod(member.mode & 0o755)
    except Exception:
        # Only the new, explicitly created extraction directory is removed.
        shutil.rmtree(destination)
        raise


def validate_release(path):
    lock = validate("release", read_json(path))

    def check_values(value, name="release"):
        if isinstance(value, dict):
            for key, item in value.items():
                check_values(item, f"{name}.{key}")
        elif isinstance(value, list):
            for item in value:
                check_values(item, name)
        elif isinstance(value, str) and (
            re.search(
                r"(?i)(placeholder|todo|example\.(com|org)|sandbox\.zenodo|<[^>]+>|\bTBD\b)", value
            )
            or value in ("0" * 40, "0" * 64, "sha256:" + "0" * 64)
        ):
            raise ReproError(f"Placeholder release identity: {name}")

    check_values(lock)
    for key in ("software_doi", "data_doi"):
        if "sandbox" in lock[key] or "example" in lock[key] or lock[key].endswith("/0"):
            raise ReproError(f"Not a publication DOI: {key}")
    for key in ("source", "sif", "data", "reference", "expected"):
        if lock[key]["sha256"] == "0" * 64:
            raise ReproError(f"Placeholder artifact hash: {key}")
    from .config import resolve_params

    resolve_params(lock["params"])
    if lock["software_doi"] == lock["data_doi"]:
        raise ReproError("Software and data require separate version DOIs")
    return lock


def prepare(lock_path, cache):
    lock = validate_release(lock_path)
    prepared = {}
    for kind in ("source", "sif", "data", "reference", "expected"):
        artifact = lock[kind]
        folder = cache / artifact["sha256"]
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / ("reprohpc.sif" if kind == "sif" else "artifact.tar.gz")
        if not path.exists():
            temporary = folder / "download.partial"
            try:
                with (
                    urllib.request.urlopen(artifact["url"], timeout=60) as response,
                    temporary.open("wb") as output,
                ):
                    if not response.geturl().startswith("https://"):
                        raise ReproError("Artifact download redirected away from HTTPS")
                    size = 0
                    while block := response.read(1024 * 1024):
                        size += len(block)
                        if size > artifact["size_bytes"]:
                            raise ReproError(f"Download exceeds locked size: {kind}")
                        output.write(block)
                if (
                    temporary.stat().st_size != artifact["size_bytes"]
                    or sha256(temporary) != artifact["sha256"]
                ):
                    raise ReproError(f"Downloaded artifact checksum mismatch: {kind}")
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        if sha256(path) != artifact["sha256"] or path.stat().st_size != artifact["size_bytes"]:
            raise ReproError(f"Cached artifact checksum mismatch: {kind}")
        if kind == "sif":
            prepared[kind] = str(path.resolve())
        else:
            # Re-extract from verified archive into a unique directory on every invocation;
            # mutable expanded caches are never trusted as immutable source/data.
            target = Path(tempfile.mkdtemp(prefix="expanded-", dir=folder))
            target.rmdir()
            extract(path, target)
            prepared[kind] = str(target.resolve())
    return lock, prepared


def export_run(root, destination):
    verify_run(root)
    if destination.exists():
        raise ReproError(f"Export already exists: {destination}", 2)
    with tempfile.TemporaryDirectory() as temporary:
        public = Path(temporary)
        for name in ("samples", "summary", "report"):
            shutil.copytree(root / name, public / name)
        provenance = public / "provenance"
        provenance.mkdir()
        for name in (
            "outputs.json",
            "analysis.json",
            "dataset.json",
            "samples.csv",
            "reference.json",
            "calibration.json",
            "source.tar.gz",
            "source.tar.gz.sha256",
            "release.lock.json",
        ):
            source = root / "provenance" / name
            if source.is_file():
                shutil.copy2(source, provenance / name)
        run = read_json(root / "provenance/run.json")
        # Preserve stable task/input/software relationships without operational paths.
        tasks = []
        for line in (root / "provenance/tasks.jsonl").read_text().splitlines():
            task = json.loads(line)
            task["origin_work_dir"] = "redacted"
            task["logs"] = []
            tasks.append(task)
        from .provenance import write_tasks

        write_tasks(public, tasks)
        from importlib.resources import files

        shutil.copytree(files("reprohpc").joinpath("schemas"), public / "schemas")
        run["platform"].pop("hostname", None)
        run["command"] = ["redacted; use resolved science parameters and archived source"]
        run["execution"] = {"profile": run["execution"]["profile"], "redacted": True}
        for key in ("dataset", "input_manifest", "reference"):
            run["params"][key] = (
                f"provenance/{'samples.csv' if key == 'input_manifest' else key + '.json'}"
            )
        write_json(provenance / "run.json", run)
        write_json(provenance / "params.resolved.json", run["params"])
        write_json(
            provenance / "redactions.json",
            {
                "removed": ["command paths", "host identity", "execution paths", "raw task logs"],
                "preserved": [
                    "scientific parameters",
                    "artifact hashes",
                    "software identity",
                    "input/reference versions",
                ],
            },
        )
        shutil.copy2(root / "status.json", public / "status.json")
        write_public_metadata(public, run)
        write_checksums(public)
        verify_run(public)
        pack(public, destination)
    return {"archive": str(destination), "sha256": sha256(destination)}
