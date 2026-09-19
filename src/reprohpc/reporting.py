"""Deterministic, self-contained results workspace. No network or science dependencies."""

import base64
import html
import json
import re
from importlib.resources import files

from .config import science_params
from .errors import ReproError
from .io import atomic_bytes, confined, fingerprint, read_json
from .schema import validate

PREVIEW_LIMIT = 24
PACKAGE_LINK = re.compile(r'href="\.\./([^"#?]*)"')
SAMPLE_LINK = re.compile(r"\$\{base\}/([A-Za-z0-9_.-]+)")
REPORT_DATA = re.compile(r'<script type="application/json" id="report-data">(.*?)</script>', re.S)


def embedded_json(value):
    """Keep data inert inside an HTML script element, even with hostile strings."""
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )


def report(summary, params, batch_dirs, destination):
    data = read_json(summary / "dataset.json")
    locations = {}
    identities = {"reference_sha256": set(), "sif_sha256": set()}
    for batch in batch_dirs:
        task = read_json(batch / "task.json")
        for key, values in identities.items():
            values.add(task[key])
        for sample in (batch / "samples").iterdir():
            if sample.name in locations:
                raise ReproError(f"Duplicate report sample: {sample.name}", 4)
            locations[sample.name] = sample
    parameters = science_params(params)
    parameter_hash = fingerprint(parameters)
    samples = []
    previews = {}
    qc = {}
    for sid, folder in sorted(locations.items()):
        metric = validate("metrics", read_json(folder / "metrics.json"))
        if metric["sample_id"] != sid or metric["parameter_sha256"] != parameter_hash:
            raise ReproError(f"Report identity or parameters differ for {sid}", 4)
        samples.append(metric)
        for code in metric["qc"]:
            qc[code] = qc.get(code, 0) + 1
        preview = folder / "preview.png"
        if len(previews) < PREVIEW_LIMIT and preview.is_file():
            previews[sid] = (
                "data:image/png;base64," + base64.b64encode(preview.read_bytes()).decode()
            )
    if (
        len(samples) != data["expected_samples"]
        or len(samples) != data["processed_samples"]
        or sum(s["object_count"] for s in samples) != data["object_count"]
        or qc != data["qc"]
    ):
        raise ReproError("Report samples disagree with the aggregate summary", 4)
    payload = {
        "summary": data,
        "samples": samples,
        "previews": previews,
        "preview_limit": PREVIEW_LIMIT,
        "parameters": parameters,
        "parameter_sha256": parameter_hash,
        "identities": {key: sorted(values) for key, values in identities.items()},
    }
    assets = files("reprohpc").joinpath("report_assets")
    replacements = {
        "CSS": assets.joinpath("report.css").read_text(encoding="utf-8"),
        "JS": assets.joinpath("report.js").read_text(encoding="utf-8"),
        "DATA": embedded_json(payload),
        "IMAGE_COUNT": f"{len(samples):,}",
        "OBJECT_COUNT": f"{data['object_count']:,}",
        "FLAGGED_COUNT": f"{sum(bool(s['qc']) for s in samples):,}",
        "PREVIEW_COUNT": str(len(previews)),
        "PARAMETERS": html.escape(json.dumps(parameters, indent=2)),
        "QC": html.escape(json.dumps(qc, indent=2)),
    }
    document = re.sub(
        r"@@([A-Z_]+)@@",
        lambda match: replacements[match[1]],
        assets.joinpath("index.html").read_text(encoding="utf-8"),
    )
    atomic_bytes(destination, document.encode("utf-8"))


def check_report_links(root):
    """Resolve every package artifact the rendered report links, including per-sample downloads.

    Targets are read from the report itself: static `../` links and the sample artifact
    names used by its script, expanded for every embedded sample.
    """
    document = (root / "report/index.html").read_text(encoding="utf-8")
    data = REPORT_DATA.search(document)
    names = set(SAMPLE_LINK.findall(document))
    if not data or not names:
        raise ReproError("Report lacks embedded data or sample artifact links", 5)
    targets = set(PACKAGE_LINK.findall(document)) | {
        f"samples/{sample['sample_id']}/{name}"
        for sample in json.loads(data[1])["samples"]
        for name in names
    }
    missing = sorted(target for target in targets if not confined(root, target).is_file())
    if missing:
        raise ReproError(f"Report links unavailable artifacts: {missing}", 5)
    return sorted(targets)
