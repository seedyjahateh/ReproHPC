"""Deterministic, self-contained results workspace. No network or science dependencies."""

import base64
import html
import json
import re
from importlib.resources import files

from .config import MRI_ALGORITHM, science_params
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
    if params.get("algorithm") == MRI_ALGORITHM:
        report_mri(summary, params, batch_dirs, destination)
        return
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


QC_LABELS = {
    "EMPTY_MASK": "Empty mask",
    "VOLUME_OUT_OF_RANGE": "Volume outside adult range",
    "MASK_AT_FOV_EDGE": "Mask at field-of-view edge",
}


def report_mri(summary, params, batch_dirs, destination):
    """Static brain-volume report. Same stylesheet as the image workspace, no script."""
    data = validate("mri_summary", read_json(summary / "dataset.json"))
    parameters = science_params(params)
    parameter_hash = fingerprint(parameters)
    subjects, identities, fsl = {}, {"reference_sha256": set(), "sif_sha256": set()}, set()
    for batch in batch_dirs:
        task = read_json(batch / "task.json")
        for key, values in identities.items():
            values.add(task[key])
        fsl.add(json.dumps(task["fsl"], sort_keys=True))
        for folder in (batch / "samples").iterdir():
            if folder.name in subjects:
                raise ReproError(f"Duplicate report sample: {folder.name}", 4)
            metric = validate("mri_metrics", read_json(folder / "metrics.json"))
            if metric["sample_id"] != folder.name or metric["parameter_sha256"] != parameter_hash:
                raise ReproError(f"Report identity or parameters differ for {folder.name}", 4)
            subjects[folder.name] = metric
    if len(fsl) != 1:
        raise ReproError("Batches report different FSL builds", 4)
    qc = {}
    for metric in subjects.values():
        for code in metric["qc"]:
            qc[code] = qc.get(code, 0) + 1
    if (
        len(subjects) != data["expected_samples"]
        or len(subjects) != data["processed_samples"]
        or sum(m["brain_voxels"] for m in subjects.values()) != data["brain_voxels"]
        or qc != data["qc"]
    ):
        raise ReproError("Report subjects disagree with the aggregate summary", 4)
    identity = json.loads(fsl.pop())
    volumes = sorted(m["brain_volume_mm3"] for m in subjects.values())
    median = (volumes[(len(volumes) - 1) // 2] + volumes[len(volumes) // 2]) / 2
    escape = html.escape

    def row(sid, m):
        base = f"../samples/{sid}"
        flags = (
            "".join(f'<span class="badge flag">{escape(QC_LABELS[c])}</span>' for c in m["qc"])
            or '<span class="badge">No QC flags</span>'
        )
        size = " × ".join(f"{v:g}" for v in m["voxel_size_mm"])
        return (
            f"<tr><td><strong>{escape(sid)}</strong></td>"
            f"<td>{' × '.join(str(v) for v in m['dims'])}</td><td>{escape(size)}</td>"
            f'<td class="numeric">{m["brain_voxels"]:,}</td>'
            f'<td class="numeric">{m["brain_volume_mm3"] / 1000:,.1f}</td><td>{flags}</td>'
            f'<td><a class="text-link" href="{base}/brain_mask.nii.gz" download>Mask</a> · '
            f'<a class="text-link" href="{base}/metrics.json">Metrics</a></td></tr>'
        )

    anchors = [("Scientific parameters", parameter_hash)] + [
        (label, value)
        for key, label in (("reference_sha256", "Reference"), ("sif_sha256", "Container (SIF)"))
        for value in sorted(identities[key])
    ]
    stylesheet = files("reprohpc").joinpath("report_assets/report.css").read_text(encoding="utf-8")
    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>Brain volumetry · ReproHPC</title>
<style>{stylesheet}</style>
<style>.static-main {{ max-width: 1180px; margin: 0 auto; padding: 44px 32px 30px; }} .static-main > * + * {{ margin-top: 22px; }} .static-main .table-scroll td {{ vertical-align: top; }} .static-main dd code {{ overflow-wrap: anywhere; font-size: 11px; }} @media (max-width: 600px) {{ .static-main {{ padding: 24px 16px; }} }}</style>
</head>
<body class="static-report">
<main id="main" class="static-main">
<p class="eyebrow">FSL BRAIN EXTRACTION AND VOLUMETRY</p>
<h1>Brain volumes<span class="accent-period">.</span></h1>
<p class="page-description">Brain masks from FSL BET and volumes from fslstats, one row per subject. Download the tables for full precision.</p>
<div class="stats-grid">
<article class="stat-card"><div class="stat-label">Subjects analysed</div><div class="stat-value">{len(subjects)}</div></article>
<article class="stat-card"><div class="stat-label">Median brain volume</div><div class="stat-value">{median / 1000:,.1f}<span class="stat-unit">mL</span></div></article>
<article class="stat-card"><div class="stat-label">Subjects with QC flags</div><div class="stat-value">{sum(bool(m["qc"]) for m in subjects.values())}</div></article>
<article class="stat-card"><div class="stat-label">BET fractional threshold</div><div class="stat-value">{parameters["bet_frac"]:g}</div></article>
</div>
<section class="panel table-panel" aria-labelledby="volumes-title">
<div class="table-topline"><strong id="volumes-title">Brain volume by subject</strong><span><a class="text-link" href="../summary/subjects.csv" download>subjects.csv</a></span></div>
<div class="table-scroll" tabindex="0" role="region" aria-label="Brain volume by subject">
<table><thead><tr><th scope="col">Subject</th><th scope="col">Dimensions <span>voxels</span></th><th scope="col">Voxel size <span>mm</span></th><th scope="col" class="numeric">Brain voxels</th><th scope="col" class="numeric">Brain volume <span>mL</span></th><th scope="col">Quality observations</th><th scope="col">Files</th></tr></thead>
<tbody>{"".join(row(sid, subjects[sid]) for sid in sorted(subjects))}</tbody></table>
</div></section>
<div class="measurement-notes">
<p><strong>What the numbers are.</strong> Brain volume is the non-zero voxel count of each BET mask multiplied by that image's voxel size, exactly as <code>fslstats -V</code> reports it; the table shows millilitres (mm³ / 1000) and the CSV keeps cubic millimetres. BET is an automated skull strip and is not validated here against manual segmentation.</p>
<p><strong>Quality observations.</strong> Flags are heuristics for review: a volume outside 800–2,000 mL, or a mask touching the image boundary, which can indicate retained neck or clipped anatomy. They are not diagnoses.</p>
</div>
<section class="panel" aria-labelledby="provenance-title">
<div class="panel-heading"><div><p class="eyebrow">PROVENANCE</p><h2 id="provenance-title">Software and identities</h2></div></div>
<dl class="parameter-list">
<div class="parameter-row"><dt>Algorithm</dt><dd>{escape(parameters["algorithm"])}</dd></div>
<div class="parameter-row"><dt>FSL</dt><dd>{escape(identity["version_string"])}</dd></div>
<div class="parameter-row"><dt>bet2 reports</dt><dd>{escape(" / ".join(identity["bet2_banner"]))}</dd></div>
{"".join(f'<div class="parameter-row"><dt>{escape(label)} <small>SHA-256</small></dt><dd><code>{escape(value)}</code></dd></div>' for label, value in anchors)}
</dl>
<p class="identity-note"><a class="text-link" href="../provenance/run.json">Run record</a> · <a class="text-link" href="../provenance/samples.csv">Input manifest</a> · <a class="text-link" href="../provenance/params.resolved.json">Resolved parameters</a> · <a class="text-link" href="../metadata.jsonld">Dataset metadata</a></p>
</section>
<footer class="main-footer"><span><strong>ReproHPC</strong> / fsl-bet-volumetry-v1</span><span>Public, defaced research data. Engineering validation, not clinical use.</span></footer>
</main>
</body>
</html>
"""
    atomic_bytes(destination, document.encode("utf-8"))


def check_report_links(root):
    """Resolve every package artifact the rendered report links, including per-sample downloads.

    Targets are read from the report itself: static `../` links and, for the scripted image
    workspace, the sample artifact names its script uses, expanded for every embedded sample.
    """
    document = (root / "report/index.html").read_text(encoding="utf-8")
    data = REPORT_DATA.search(document)
    names = set(SAMPLE_LINK.findall(document))
    targets = set(PACKAGE_LINK.findall(document))
    if data:
        if not names:
            raise ReproError("Report lacks sample artifact links", 5)
        targets |= {
            f"samples/{sample['sample_id']}/{name}"
            for sample in json.loads(data[1])["samples"]
            for name in names
        }
    elif not any(target.startswith("samples/") for target in targets):
        raise ReproError("Report lacks embedded data or sample artifact links", 5)
    missing = sorted(target for target in targets if not confined(root, target).is_file())
    if missing:
        raise ReproError(f"Report links unavailable artifacts: {missing}", 5)
    return sorted(targets)
