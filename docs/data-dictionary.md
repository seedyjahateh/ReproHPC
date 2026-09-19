# Data dictionary — schema version 1.0.0

Published [JSON Schemas](../schemas/) are embedded in the Python distribution. Regenerate with `python scripts/generate_schemas.py`; both copies must match.

## Scientific files

| Term | Type / unit | Meaning |
|---|---|---|
| sample_id | ASCII identifier | Stable dataset-local image identity; no whitespace or path separators. |
| object_id | positive integer | Stable spatially ordered object index within an image. |
| area_px | integer pixels | Retained foreground pixel count of the object. |
| centroid_x_px, centroid_y_px | floating pixels | Zero-based image coordinate mean over object pixels. |
| mean_intensity | floating 0–255 | Mean original grayscale intensity of object pixels. |
| touches_border | boolean | Object contains a pixel at any image edge. |
| area_um2 | floating µm² or null | Calibrated area; null when pixel size is unknown. |
| foreground_fraction | fraction 0–1 | Total retained mask pixels divided by image size. |
| mean_area_px | floating pixels or null | Mean retained object area; null with NO_OBJECTS. |
| qc | list of codes | NO_OBJECTS is valid empty segmentation; BORDER_OBJECTS flags included edge objects. |
| sha256 | 64 lowercase hex digits | Byte identity; retaining bytes is also required. |

Every successfully finalized run directory and every public export carries `metadata.jsonld` and `dictionary/1.0.0.jsonld`, a versioned `DefinedTermSet`. Measurement `propertyID` values point to its term fragments, for example `dictionary/1.0.0.jsonld#area_px`. These relative identifiers resolve against the extracted package location; preserve its paths and use the public deposit location as the base when exposing the JSON-LD. The package includes the definitions themselves, so offline interpretation does not require a future Git tag or a network lookup.

`metadata.jsonld` links result, input, reference, software, and analysis entities using a [schema.org Dataset](https://schema.org/Dataset), distributions with checksums, and a [CreateAction](https://schema.org/CreateAction) with explicit inputs, software instrument, and result. Variables include units and missing-value semantics. A real release lock supplies version DOI links; development exports retain content identities. This is the project's small FAIR metadata profile; no RO-Crate compliance claim is made.

JSON records for samples, resolved parameters, per-image metrics, dataset summaries, runs and tasks declare `schema_version: 1.0.0`. CSV column contracts are versioned by the containing package and dictionary; their rows do not acquire an extra schema-version column. Unknown JSON schema versions are rejected.

CSV is UTF-8, comma-delimited, LF-separated, with one header. Empty numeric fields mean missing, not zero. Boolean CSV values are true/false; JSON uses booleans. Float serialization uses 17 significant digits. Arrays use NumPy's non-pickle format. UTC timestamps and scheduler/job IDs belong only in provenance.

Version input data and calibration independently. Never edit a released image in place; publish a new data version and manifest. Global dataset identity is not an analysis-task input: stable per-batch records allow unrelated batches to remain cached after one valid input update.
