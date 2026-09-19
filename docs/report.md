# Results workspace

Open `report/index.html` inside any successfully finalized run or exported reproduction package. Acceptance checks resolve every link in the report, including each sample's downloads, in both locations. The report embeds its styles, scripts, measured image summaries, and up to 24 available previews. It uses local system fonts and makes no network requests. Keep the surrounding run directory to retain artifact links. Search and navigation also work when the HTML is opened directly with `file://`.

The four views share the same result snapshot:

- **Overview:** total images, retained objects, distinct images with QC flags, available embedded previews, and per-image object counts. For more than 12 images, the chart shows the 12 highest object counts and labels this selection. The table contains every image.
- **Image explorer:** search sample IDs without regard to case, filter quality observations, and select a preview to inspect dimensions, object count, foreground coverage, and mean object area. Download the selected sample's object CSV, metrics JSON, or binary mask. A sample URL such as `#explorer/square` opens that image directly.
- **Full-screen viewer:** select the detail image, or its **Full screen** action, to fill the window with the embedded preview and its measurements. Previous and Next, or the left and right arrow keys, move through the images that both match the current search and quality filters and have an embedded preview, in the same order, and stop at each end. The position indicator counts within that set and names it, for example `3 of 24 embedded previews`, because a report can list far more images than the 24 previews it embeds. Escape, the Close button, or a click outside the image returns to the explorer with the last viewed image selected and focus back on the control that opened it. The viewer shows the preview already embedded in the report, at the scale the window allows, so it makes no request and works from `file://`. Samples beyond the 24 embedded previews have no image to enlarge, and offer no full-screen control; open their stored artifacts instead. Previews are downscaled to at most 512 pixels on the longer side, so full screen shows the preview enlarged, not the original image: analyse the mask and the CSV rather than judging fine detail here. Printing excludes the viewer.
- **Measurements:** browse all image rows, sort by sample ID, object count, or foreground fraction, and combine search with QC filters. Pages contain 25 rows. Sample IDs open the image explorer. Downloads always contain the complete, original tables, irrespective of active filters.
- **Provenance:** inspect fixed scientific parameters and content identities captured by analysis tasks, copy their values, and follow links to the finalized run record, manifest, reference, and JSON-LD metadata. Browsers may deny clipboard access on local files; the report displays an explanation and leaves values selectable.

The interface rounds numeric values for readability. CSVs retain full precision. An em dash represents null mean area when there are no objects. Image mean area is in square pixels; object CSVs provide calibrated µm² where available. A red outline in a preview shows retained foreground boundaries. QC flags are observations, not a pass/fail scientific certification. The supplied algorithm remains an engineering demonstration without independent domain validation.

The report is generated before final provenance verification. Its “Results snapshot” label does not assert a successful final run: use `reprohpc verify --run RUN_DIRECTORY` and inspect `provenance/run.json` for final status. Read-only interactions never change scientific outputs, parameters, checksums, or provenance.

## Open a local preview

Opening the HTML directly requires no server. Alternatively, from a host with Python, serve the completed result directory on loopback:

```bash
python -m http.server 8765 --bind 127.0.0.1 --directory path/to/completed-run
```

Then open `http://127.0.0.1:8765/report/index.html`. The server exposes only the selected local directory; stop it with Ctrl+C when finished.

## Accessibility and maintenance

Navigation, filters, table links, pagination, and the full-screen viewer support keyboards and visible focus. The viewer is a native modal dialog, so the browser confines focus to it while open and Escape closes it; automated WCAG A/AA checks run against the report with the viewer open as well as on each view. The interface respects reduced-motion settings. Small screens use horizontal navigation and a scrollable measurements table. Without JavaScript, the overview counts, scientific parameters, QC counts, and table links remain available. Printing includes the current pages of the report views; export CSVs for complete datasets.

The implementation is `src/reprohpc/reporting.py` with packaged assets under `src/reprohpc/report_assets/`. The generator validates sample identities, parameters, and aggregate agreement, embeds safe JSON, and selects at most 24 available previews in canonical sample-ID order. Python unit tests cover those contracts; [browser tests](testing.md) exercise behavior, offline operation, responsive layout, and automated accessibility checks. No external UI framework or production JavaScript dependency is required.
