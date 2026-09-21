"""Regenerate the versioned contracts; CI checks that checked-in files match."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STR = {"type": "string", "minLength": 1}
HASH = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
ID = {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"}
INT = {"type": "integer", "minimum": 0}
NUM = {"type": "number"}
NULLNUM = {"type": ["number", "null"]}
VERSION = {"const": "1.0.0"}
DOI = {"type": ["string", "null"], "pattern": "^10\\.[0-9]{4,9}/[^ ]+$"}


def obj(properties, optional=()):
    return {
        "type": "object",
        "properties": properties,
        "required": [key for key in properties if key not in optional],
        "additionalProperties": False,
    }


def array(items):
    return {"type": "array", "items": items}


def contracts():
    artifact = obj({"path": STR, "sha256": HASH, "size_bytes": INT})
    dataset = obj(
        {
            "schema_version": VERSION,
            "dataset_id": ID,
            "version": STR,
            "title": STR,
            "description": STR,
            "creators": {**array(STR), "minItems": 1},
            "license": STR,
            "source": {"type": "string", "format": "uri"},
            "created": {"type": "string", "format": "date"},
            "manifest_sha256": HASH,
            "modality": STR,
            "species": STR,
            "access": {"const": "public"},
            "doi": DOI,
        }
    )
    reference = obj(
        {
            "schema_version": VERSION,
            "reference_id": ID,
            "version": STR,
            "source": {"type": "string", "format": "uri"},
            "license": STR,
            "calibration": artifact,
        }
    )
    calibration = obj(
        {
            "schema_version": VERSION,
            "pixel_size_um": {"type": ["number", "null"], "exclusiveMinimum": 0},
            "unit": {"const": "um"},
        }
    )
    sample = obj(
        {
            "schema_version": VERSION,
            "sample_id": ID,
            "path": STR,
            "sha256": HASH,
            "size_bytes": INT,
            "width": {"type": "integer", "minimum": 1, "maximum": 4096},
            "height": {"type": "integer", "minimum": 1, "maximum": 4096},
            "dtype": {"const": "uint8"},
            "channels": {"const": 1},
        }
    )
    metrics = obj(
        {
            "schema_version": VERSION,
            "sample_id": ID,
            "width": INT,
            "height": INT,
            "object_count": INT,
            "foreground_fraction": {"type": "number", "minimum": 0, "maximum": 1},
            "mean_area_px": NULLNUM,
            "qc": array({"enum": ["NO_OBJECTS", "BORDER_OBJECTS"]}),
            "parameter_sha256": HASH,
        }
    )
    params = obj(
        {
            "schema_version": VERSION,
            "algorithm": {"const": "demo-cv-v1"},
            "seed": {"type": "integer", "minimum": 0, "maximum": 2147483647},
            "gaussian_kernel": {"enum": list(range(1, 32, 2))},
            "gaussian_sigma": {"type": "number", "exclusiveMinimum": 0, "maximum": 10},
            "threshold": {"type": "integer", "minimum": 0, "maximum": 255},
            "min_area_px": {"type": "integer", "minimum": 1, "maximum": 16777216},
            "connectivity": {"enum": [4, 8]},
            "batch_size": {"type": "integer", "minimum": 1, "maximum": 64},
            "write_previews": {"type": "boolean"},
            "dataset": STR,
            "input_manifest": STR,
            "reference": STR,
        },
        optional=("dataset", "input_manifest", "reference"),
    )
    analysis = obj(
        {
            "schema_version": VERSION,
            "algorithm": {"const": "demo-cv-v1"},
            "source_tree_sha256": HASH,
            "sif_sha256": HASH,
            "science": {"type": "object"},
            "samples": array(sample),
            "reference": reference,
            "calibration": calibration,
        }
    )
    status = obj(
        {
            "schema_version": VERSION,
            "run_id": STR,
            "status": {
                "enum": ["running", "success", "failed", "cancelled", "finalization-failed"]
            },
            "exit_code": {"type": ["integer", "null"]},
            "message": {"type": "string"},
        }
    )
    run = obj(
        {
            "schema_version": VERSION,
            "run_id": STR,
            "nextflow_session_id": {"type": ["string", "null"]},
            "resumed_from": {"type": ["string", "null"]},
            "started": STR,
            "finished": STR,
            "analysis_fingerprint": HASH,
            "command": array({"type": "string"}),
            "params": params,
            "execution": {"type": "object"},
            "software": {"type": "object"},
            "platform": {"type": "object"},
            "dataset": dataset,
            "reference": reference,
            "status": status,
        }
    )
    task = obj(
        {
            "schema_version": VERSION,
            "process": STR,
            "task_id": STR,
            "hash": STR,
            "status": STR,
            "attempt": INT,
            "native_id": {"type": ["string", "null"]},
            "sample_ids": array(ID),
            "requested": {"type": "object"},
            "usage": {"type": "object"},
            "exit_code": {"type": ["integer", "null"]},
            "origin_work_dir": STR,
            "scientific_metadata": {"type": ["object", "null"]},
            "origin_run_id": {"type": ["string", "null"]},
            "origin_session_id": {"type": ["string", "null"]},
            "origin_task_id": {"type": ["string", "null"]},
            "logs": array(STR),
        }
    )
    release_artifact = obj(
        {
            "url": {"type": "string", "pattern": "^https://"},
            "sha256": HASH,
            "size_bytes": {"type": "integer", "minimum": 1},
        }
    )
    release = obj(
        {
            "schema_version": VERSION,
            "version": STR,
            "source_commit": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
            "software_doi": {"type": "string", "pattern": "^10\\.[0-9]{4,9}/[^ ]+$"},
            "data_doi": {"type": "string", "pattern": "^10\\.[0-9]{4,9}/[^ ]+$"},
            "nextflow_version": {"const": "25.10.4"},
            "nextflow_sha256": HASH,
            "oci_digest": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
            "platform": {"const": "linux/amd64"},
            "environment": obj(
                {
                    "python": {"const": "3.12.3"},
                    "java_vendor": STR,
                    "java_build": STR,
                    "apptainer": {"const": "1.5.3"},
                    "slurm": {"const": "23.11.4"},
                    "docker_base_digest": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
                    "requirements_sha256": HASH,
                    "host_requirements_sha256": HASH,
                    "build_requirements_sha256": HASH,
                    "python_packages": {**array(obj({"name": STR, "version": STR})), "minItems": 1},
                    "build_tools": obj(
                        {
                            "pip": {"const": "25.2"},
                            "setuptools": {"const": "80.9.0"},
                            "wheel": {"const": "0.45.1"},
                        }
                    ),
                }
            ),
            "source": release_artifact,
            "sif": release_artifact,
            "data": release_artifact,
            "reference": release_artifact,
            "expected": release_artifact,
            "params": {"type": "object"},
        }
    )
    # fsl-bet-volumetry-v1: separate contracts, so every demo-cv-v1 contract above is unchanged.
    triple = {"minItems": 3, "maxItems": 3}
    dims = {**array({"type": "integer", "minimum": 1, "maximum": 2048}), **triple}
    voxel_size = {**array({"type": "number", "exclusiveMinimum": 0}), **triple}
    mri_qc = ["EMPTY_MASK", "VOLUME_OUT_OF_RANGE", "MASK_AT_FOV_EDGE"]
    mri_algorithm = {"const": "fsl-bet-volumetry-v1"}
    mri_sample = obj(
        {
            "schema_version": VERSION,
            "sample_id": ID,
            "path": STR,
            "sha256": HASH,
            "size_bytes": INT,
            "format": {"const": "nifti1"},
            "dims": dims,
            "voxel_size_mm": voxel_size,
            "datatype": {
                "enum": [
                    "uint8",
                    "int8",
                    "int16",
                    "uint16",
                    "int32",
                    "uint32",
                    "float32",
                    "float64",
                ]
            },
        }
    )
    mri_params = obj(
        {
            "schema_version": VERSION,
            "algorithm": mri_algorithm,
            "bet_frac": {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1},
            "batch_size": {"type": "integer", "minimum": 1, "maximum": 64},
            "dataset": STR,
            "input_manifest": STR,
            "reference": STR,
        },
        optional=("dataset", "input_manifest", "reference"),
    )
    mri_metrics = obj(
        {
            "schema_version": VERSION,
            "sample_id": ID,
            "dims": dims,
            "voxel_size_mm": voxel_size,
            "brain_voxels": INT,
            "brain_volume_mm3": {"type": "number", "minimum": 0},
            "qc": array({"enum": mri_qc}),
            "parameter_sha256": HASH,
        }
    )
    mri_summary = obj(
        {
            "schema_version": VERSION,
            "algorithm": mri_algorithm,
            "expected_samples": INT,
            "processed_samples": INT,
            "brain_voxels": INT,
            "qc": {
                "type": "object",
                "propertyNames": {"enum": mri_qc},
                "additionalProperties": INT,
            },
        }
    )
    mri_analysis = obj(
        {
            **analysis["properties"],
            "algorithm": mri_algorithm,
            "samples": array(mri_sample),
        }
    )
    mri_run = obj({**run["properties"], "params": mri_params})
    return {
        "mri_sample": mri_sample,
        "mri_params": mri_params,
        "mri_metrics": mri_metrics,
        "mri_summary": mri_summary,
        "mri_analysis": mri_analysis,
        "mri_run": mri_run,
        "dataset": dataset,
        "reference": reference,
        "calibration": calibration,
        "sample": sample,
        "metrics": metrics,
        "summary": obj(
            {
                "schema_version": VERSION,
                "expected_samples": INT,
                "processed_samples": INT,
                "object_count": INT,
                "qc": {
                    "type": "object",
                    "propertyNames": {"enum": ["NO_OBJECTS", "BORDER_OBJECTS"]},
                    "additionalProperties": INT,
                },
            }
        ),
        "params": params,
        "analysis": analysis,
        "status": status,
        "run": run,
        "task": task,
        "output": obj(
            {
                "schema_version": VERSION,
                "path": STR,
                "sha256": HASH,
                "size_bytes": INT,
                "media_type": STR,
                "scope": {"type": "object"},
                "role": STR,
                "schema": STR,
                "producing_task": {"type": "object"},
            }
        ),
        "release": release,
    }


if __name__ == "__main__":
    for name, schema in contracts().items():
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"https://github.com/seedyjahateh/ReproHPC/blob/main/schemas/{name}.json",
            **schema,
        }
        content = json.dumps(schema, indent=2) + "\n"
        for folder in (ROOT / "schemas", ROOT / "src/reprohpc/schemas"):
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{name}.json").write_text(content, encoding="utf-8")
