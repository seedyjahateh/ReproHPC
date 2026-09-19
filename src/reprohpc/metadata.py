"""Offline schema.org metadata with package-relative, versioned term identifiers."""

from urllib.parse import quote

from .io import fingerprint, read_json, write_json

DICTIONARY = "dictionary/1.0.0.jsonld"
VARIABLES = {
    "object_count": ("objects", "Number of retained connected components."),
    "area_px": ("pixels", "Number of foreground pixels in one retained object."),
    "centroid_x_px": ("pixels", "Zero-based mean column coordinate, increasing rightward."),
    "centroid_y_px": ("pixels", "Zero-based mean row coordinate, increasing downward."),
    "mean_intensity": (
        "grayscale level (0-255)",
        "Mean original image intensity over object pixels.",
    ),
    "area_um2": (
        "square micrometers",
        "Pixel area times pixel_size_um squared; null without calibration.",
    ),
    "foreground_fraction": (
        "dimensionless",
        "Retained foreground pixel count divided by image size.",
    ),
    "mean_area_px": ("pixels", "Mean retained object area; null when there are no objects."),
}


def license_url(identifier):
    return (
        identifier
        if identifier.startswith("https://")
        else f"https://spdx.org/licenses/{quote(identifier, safe='')}.html"
    )


def identity(value):
    return {"@type": "PropertyValue", "propertyID": "SHA-256", "value": value}


def write_public_metadata(root, run):
    """Relative URLs resolve within the extracted package; no draft DOI is invented.

    The same term definitions travel with every export. The release publisher
    preserves these paths, or supplies the deposit URL as the JSON-LD base.
    """
    terms = [
        {
            "@type": "DefinedTerm",
            "@id": f"#{name}",
            "termCode": name,
            "name": name,
            "description": description,
            "inDefinedTermSet": {"@id": "#dictionary"},
        }
        for name, (_, description) in VARIABLES.items()
    ]
    write_json(
        root / DICTIONARY,
        {
            "@context": "https://schema.org/",
            "@type": "DefinedTermSet",
            "@id": "#dictionary",
            "name": "ReproHPC measurement dictionary",
            "version": "1.0.0",
            "license": "https://creativecommons.org/licenses/by/4.0/",
            "hasDefinedTerm": terms,
        },
    )
    data = run["dataset"]
    reference = run["reference"]
    software = run["software"]
    lock_path = root / "provenance/release.lock.json"
    lock = read_json(lock_path) if lock_path.is_file() else {}
    input_data = {
        "@id": "#input-data",
        "@type": "Dataset",
        "name": data["title"],
        "version": data["version"],
        "url": data["source"],
        "license": license_url(data["license"]),
        "identifier": identity(data["manifest_sha256"]),
    }
    data_doi = lock.get("data_doi") or data.get("doi")
    if data_doi:
        input_data["sameAs"] = f"https://doi.org/{data_doi}"
    code = {
        "@id": "#software",
        "@type": "SoftwareSourceCode",
        "name": "ReproHPC",
        "codeRepository": "https://github.com/seedyjahateh/ReproHPC",
        "programmingLanguage": ["Python", "Nextflow DSL2"],
        "version": lock.get("version") or f"source-sha256:{software['source_tree_sha256']}",
        "identifier": [identity(software["source_tree_sha256"]), identity(software["sif_sha256"])],
        "license": "https://spdx.org/licenses/MIT.html",
    }
    if lock.get("software_doi"):
        code["sameAs"] = f"https://doi.org/{lock['software_doi']}"
    distributions = [
        {
            "@type": "DataDownload",
            "contentUrl": entry["path"],
            "encodingFormat": entry["media_type"],
            "contentSize": f"{entry['size_bytes']} bytes",
            "sha256": entry["sha256"],
        }
        for entry in read_json(root / "provenance/outputs.json")
    ]
    dataset = {
        "@id": "#results",
        "@type": "Dataset",
        "name": "ReproHPC reproducibility package",
        "description": f"Scientific results derived from {data['title']} using {run['params']['algorithm']}.",
        "identifier": identity(run["analysis_fingerprint"]),
        "license": license_url(data["license"]),
        "version": run["analysis_fingerprint"],
        "creator": [{"name": name} for name in data["creators"]],
        "isBasedOn": [{"@id": "#input-data"}, {"@id": "#reference"}],
        "citation": {"@id": "#software"},
        "distribution": distributions,
        "variableMeasured": [
            {
                "@type": "PropertyValue",
                "propertyID": f"{DICTIONARY}#{name}",
                "name": name,
                "unitText": unit,
                "description": description,
            }
            for name, (unit, description) in VARIABLES.items()
        ],
    }
    write_json(
        root / "metadata.jsonld",
        {
            "@context": "https://schema.org/",
            "@graph": [
                dataset,
                input_data,
                code,
                {
                    "@id": "#reference",
                    "@type": "Dataset",
                    "name": reference["reference_id"],
                    "version": reference["version"],
                    "identifier": identity(fingerprint(reference)),
                    "license": license_url(reference["license"]),
                    "url": reference["source"],
                },
                {
                    "@type": "CreateAction",
                    "@id": "#analysis",
                    "name": run["params"]["algorithm"],
                    "object": [{"@id": "#input-data"}, {"@id": "#reference"}],
                    "instrument": {"@id": "#software"},
                    "result": {"@id": "#results"},
                },
            ],
        },
    )
