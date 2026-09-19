# Real microscopy data

The bundled `demo` dataset is synthetic: twelve generated shapes whose correct answers are known by construction (see [algorithm](algorithm.md)). It validates the software, not the science. This page covers running the pipeline on real images that carry published expert annotations, so the measurements can be scored against something other than the implementation's own output.

Nothing here changes the scientific contract, the default parameters, or any committed fixture. The real dataset is an additional, optional input.

## Source and terms

[BBBC039v1](https://bbbc.broadinstitute.org/BBBC039), from the Broad Institute's Bioimage Benchmark Collection.

| Property | Value |
|---|---|
| Content | 200 fields of Hoechst-stained U2OS human osteosarcoma cell nuclei |
| Acquisition | ImageXpress Micro, fluorescence microscopy, one channel |
| Format | 520 × 696, 16-bit TIFF; values observed within the 12-bit range |
| Annotation | One expert mask per field; 19,565 annotated nuclei in total |
| License | CC0 1.0, stated on the image set page |
| Archives | `images.zip` (77,915,748 bytes), `masks.zip` (2,753,811 bytes) |

Licensing in this collection is assigned **per image set**, not collection-wide. BBBC039 and [BBBC038](https://bbbc.broadinstitute.org/BBBC038) are CC0; others are CC BY or CC BY-NC-SA. Check the individual set page before adding another one, and record its terms in [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md). The collection index is at [bbbc.broadinstitute.org/image_sets](https://bbbc.broadinstitute.org/image_sets).

CC0 waives copyright and imposes no conditions, but the collection requests a citation, and scholarly practice requires attribution regardless of licence:

> We used image set BBBC039v1 Caicedo et al. 2018, available from the Broad Bioimage Benchmark Collection [Ljosa et al., Nature Methods, 2012].

## Preparation and provenance

```bash
python scripts/fetch_bbbc039.py            # downloads, verifies, converts
python reprohpc run --profile local --params-file params/bbbc039.yaml \
  --sif "$SIF" --sif-sha256 "$SIF_SHA" \
  --outdir /scratch/bbbc039-run/run \
  --work-dir /scratch/bbbc039-run/work \
  --launch-dir /scratch/bbbc039-run/launch
python scripts/evaluate_bbbc039.py --run /scratch/bbbc039-run/run
```

`scripts/fetch_bbbc039.py` pins both upstream archives by SHA-256 and refuses to proceed when the bytes differ, so an upstream change or a corrupted download fails loudly instead of silently altering the science. Downloads go to `.tools/bbbc039/` and are reused on later invocations.

**Conversion.** The pipeline's input contract is 8-bit single-channel PNG ([algorithm](algorithm.md)), so 16-bit TIFF cannot be consumed directly. Each image is converted by a fixed right shift of 4, mapping the 12-bit sensor range onto 8 bits. That mapping is identical for every image and is not derived from the data, which keeps the conversion deterministic and free of hidden tuning. The script asserts that no source pixel exceeds the declared 12-bit range, so the assumption cannot fail silently. Nothing else is altered: no cropping, denoising, contrast stretching, or per-image normalisation. Converting to 8 bits does discard detail a real analysis might need; this is a property of the demonstration, not a recommendation.

**Identifiers.** Sample IDs keep plate, well and site plus the first eight characters of the acquisition GUID, for example `IXMtest_A02_s1_051DAA7C`. Well plus site alone is not unique — two fields were acquired twice — and the full GUID exceeds the 64-character `sample_id` contract. The original filenames are retained in the ground-truth record.

**Calibration.** BBBC039 publishes no pixel size that this project has verified, so `references/bbbc039/1.0.0/` declares `pixel_size_um: null`. `area_um2` is therefore empty for every object rather than carrying an invented scale; areas are reported in pixels. Supplying a verified pixel size later is a reference change, not a code change.

**What is committed.** Only the scripts, `params/bbbc039.yaml`, and this page. The converted images, the derived reference, and the downloaded archives are gitignored: they are reproducible byte-for-byte from the pinned checksums, and the project does not redistribute a copy of someone else's dataset. Ground-truth counts are written to `data/bbbc039/ground-truth-1.0.0.json`, deliberately outside the immutable dataset directory, because they are evaluation reference data and must never become an analysis input.

## Recorded run

Candidate 7 (`0f5297bc…15ac`), local profile, real Nextflow 25.10.4 and Apptainer 1.5.3:

| | |
|---|---|
| Images | 200, in 13 batches |
| Wall time | 87 s |
| Status | success; `verify_run` passed over 907 published files |
| Report | all 607 links resolve, including every per-sample download |

Evidence: `evidence/bbbc039-run.log`, `evidence/bbbc039-trace.tsv`, `evidence/bbbc039-command.sh`. The run carries the same provenance as any other: every measurement resolves to its input SHA-256, resolved parameters, container checksum, and producing task.

## Agreement with the expert masks

`threshold: 25` and `min_area_px: 50` were selected on the **tuning half** (every second sample ID, a deterministic split). The **held-out half** informed no parameter choice. Ground truth is the number of 8-connected components in each published mask; `scripts/evaluate_bbbc039.py` recomputes the whole table from a completed run.

| | Tuning half | Held-out half |
|---|---|---|
| Images | 100 | 100 |
| Annotated nuclei | 9,923 | 9,642 |
| Objects reported | 9,286 | 8,989 |
| Median count ratio | 0.940 | 0.937 |
| Median absolute error | 6 nuclei | 5 nuclei |
| Images within 10% | 78.6% | 80.8% |
| Median foreground area ratio | 0.943 | 0.914 |
| Under-counted / over-counted | 93 / 5 | 98 / 0 |

Evidence: `evidence/bbbc039-evaluation.json`. Held-out and tuning numbers are close, so the result is not an artifact of the parameter choice.

**Interpretation.** The algorithm recovers about 94% of annotated nuclei by count on images it was not tuned on, and covers a similar fraction of the annotated foreground area. The error is almost entirely one-directional: on the held-out half, 98 images are under-counted and none are over-counted. That is the predicted consequence of the contract in [algorithm](algorithm.md) — a global threshold followed by connected components, with no watershed or other splitting step, so nuclei that touch merge into a single object. The failure mode is understood, not mysterious.

## Limits of this evidence

- **Not domain validation.** No domain expert has reviewed these outputs. G-01 and M-03 in [TASKS.md](../TASKS.md) remain tied to the synthetic reference.
- **Count agreement is a weak metric.** It cannot detect a correct count composed of the wrong objects. Instance-level matching (IoU-based precision, recall and F1, or the standard average-precision sweep used on this benchmark) is the honest next measurement, and requires comparing masks rather than totals.
- **No comparison against an accepted method.** CellProfiler or Cellpose on the same 200 images would establish whether these numbers are respectable or poor for this benchmark. Until then, 0.937 is a measurement without a reference point.
- **One dataset, one modality.** Nuclei in fluorescence images are close to the easiest real segmentation task. Phase-contrast, brightfield, or tissue images would be substantially harder.
- **The conversion is lossy.** Results describe the 8-bit conversion, not the original 16-bit data.

## Adding another dataset

The same path works for any source that can be reduced to the input contract: pin the upstream bytes by checksum, convert deterministically without fitting anything to the data, record the licence and citation, keep derived artifacts out of version control, and keep evaluation reference data outside the dataset directory. If ground truth exists, always report a held-out split; if it does not, the dataset can demonstrate execution and provenance but cannot support an accuracy claim.
