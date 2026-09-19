/* Offline progressive enhancement; scientific values are never recalculated or persisted. */
(() => {
  "use strict";
  const data = JSON.parse(document.getElementById("report-data").textContent);
  const samples = data.samples;
  const byId = new Map(samples.map(sample => [sample.sample_id, sample]));
  const $ = id => document.getElementById(id);
  const escape = value => String(value).replace(/[&<>"']/g, char => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"})[char]);
  const icon = name => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
  const number = (value, digits = 0) => value === null ? "—" : value.toLocaleString("en-US", {maximumFractionDigits: digits});
  const titles = {overview: "Analysis overview", explorer: "Image explorer", measurements: "Measurements", provenance: "Scientific provenance"};
  const shortTitles = {overview: "Overview", explorer: "Image explorer", measurements: "Measurements", provenance: "Provenance"};
  const qcNames = {NO_OBJECTS: "No objects", BORDER_OBJECTS: "Border objects"};
  const state = {
    explorer: {query: "", filter: "all", page: 0, size: 12},
    table: {query: "", filter: "all", page: 0, size: 25, sort: "sample"},
    selected: samples[0]?.sample_id ?? null,
  };
  let toastTimer;
  function toast(message) {
    clearTimeout(toastTimer);
    $("toast").textContent = message;
    $("toast").hidden = false;
    toastTimer = setTimeout(() => { $("toast").hidden = true; }, 4500);
  }
  async function copy(value) {
    try {
      await navigator.clipboard.writeText(value);
      toast("Copied to clipboard.");
    } catch {
      // file:// and restrictive browsers may deny clipboard access. The values remain selectable.
      toast("Clipboard access is unavailable. Select and copy the displayed value.");
    }
  }
  function badges(sample) {
    return sample.qc.length
      ? sample.qc.map(code => `<span class="badge ${code === "NO_OBJECTS" ? "empty" : "flag"}">${escape(qcNames[code])}</span>`).join("")
      : '<span class="badge">No QC flags</span>';
  }
  function preview(sample, detail = false) {
    const src = data.previews[sample.sample_id];
    return `<div class="preview-image${detail ? " detail-preview" : ""}">${src
      ? `<img src="${escape(src)}" alt="Segmentation overlay for ${escape(sample.sample_id)}"${detail ? "" : ' loading="lazy"'}>`
      : `<div class="missing-preview">${icon("image")}Preview not embedded</div>`}</div>`;
  }
  function card(sample, index, selected = false) {
    const image = preview(sample).replace(/<\/div>$/, `<span class="preview-sequence">IMG ${String(index + 1).padStart(3, "0")}</span><span class="preview-open" aria-hidden="true">↗</span></div>`);
    return `<button type="button" class="preview-card" data-sample="${escape(sample.sample_id)}"${selected ? ` aria-pressed="${state.selected === sample.sample_id}"` : ""} aria-label="Inspect ${escape(sample.sample_id)}, ${sample.object_count} objects">${image}<div class="preview-caption"><strong>${escape(sample.sample_id)}</strong><small><span>${number(sample.object_count)} ${sample.object_count === 1 ? "object" : "objects"} · ${sample.width} × ${sample.height} px</span>${sample.qc.length ? '<span class="small-flag" aria-hidden="true"></span>' : ""}</small></div></button>`;
  }
  function matching(view) {
    return samples.filter(sample => {
      const nameMatches = sample.sample_id.toLowerCase().includes(view.query.trim().toLowerCase());
      const qualityMatches = view.filter === "all" || (view.filter === "flagged" && sample.qc.length > 0)
        || (view.filter === "clear" && sample.qc.length === 0) || sample.qc.includes(view.filter);
      return nameMatches && qualityMatches;
    });
  }
  function paginate(target, view, results, render) {
    const pages = Math.ceil(results.length / view.size);
    view.page = Math.max(0, Math.min(view.page, pages - 1));
    const start = view.page * view.size;
    $(target).innerHTML = `<span>${results.length ? `${number(start + 1)}–${number(Math.min(start + view.size, results.length))} of ${number(results.length)} images` : "0 images"}</span><button type="button" class="button button-outline" data-page="-1" ${view.page === 0 ? "disabled" : ""}>Previous</button><button type="button" class="button button-outline" data-page="1" ${view.page + 1 >= pages ? "disabled" : ""}>Next</button>`;
    $(target).querySelectorAll("button").forEach(button => button.addEventListener("click", () => {
      const direction = button.dataset.page;
      view.page += Number(direction);
      render();
      // Preserve focus when the pagination DOM is replaced, including its final page.
      const next = $(target).querySelector(`[data-page="${direction}"]:not(:disabled)`)
        || $(target).querySelector("button:not(:disabled)");
      next?.focus();
    }));
    return results.slice(start, start + view.size);
  }
  const empty = '<div class="empty-state"><strong>No matching images</strong><p>Try another sample ID or reset the quality filter.</p></div>';
  function renderDetail() {
    const sample = byId.get(state.selected);
    if (!sample) {
      $("sample-detail").innerHTML = '<div class="empty-state"><strong>No image selected</strong><p>Choose an image from the collection to inspect its results.</p></div>';
      return;
    }
    const base = `../samples/${encodeURIComponent(sample.sample_id)}`;
    const expandable = Boolean(data.previews[sample.sample_id]);
    $("sample-detail").innerHTML = `<div class="detail-header"><p class="eyebrow">SELECTED IMAGE</p><h2>${escape(sample.sample_id)}</h2></div>${
      expandable
        ? `<button type="button" class="preview-expand" data-expand="${escape(sample.sample_id)}" aria-label="View ${escape(sample.sample_id)} full screen">${preview(sample, true)}<span class="preview-expand-hint" aria-hidden="true">${icon("expand")}Full screen</span></button>`
        : preview(sample, true)
    }<div class="detail-caption">${data.previews[sample.sample_id] ? '<span class="overlay-key"></span>Red overlay · retained foreground' : "Preview not embedded · original artifacts linked below"}</div><dl class="detail-metrics"><div><dt>Objects detected</dt><dd>${number(sample.object_count)}</dd></div><div><dt>Foreground coverage</dt><dd>${number(sample.foreground_fraction * 100, 2)} <span>%</span></dd></div><div><dt>Mean object area</dt><dd>${number(sample.mean_area_px, 2)} <span>px²</span></dd></div><div><dt>Image dimensions</dt><dd>${sample.width} × ${sample.height} <span>px</span></dd></div></dl><div class="detail-qc">${badges(sample)}<p>${sample.qc.includes("NO_OBJECTS") ? "No components passed the segmentation and size thresholds. Empty segmentation is a valid result; mean area is undefined." : sample.qc.includes("BORDER_OBJECTS") ? "One or more retained objects touch the image border. These objects remain included in the measurements." : "No border-object or empty-segmentation flags were recorded. This is not a domain quality certification."}</p></div><div class="detail-actions">${expandable ? `<button type="button" class="button button-outline button-small" data-expand="${escape(sample.sample_id)}">${icon("expand")}Full screen</button>` : ""}<a class="button button-primary button-small" href="${base}/objects.csv" download>${icon("download")}Object CSV</a><a class="button button-outline button-small" href="${base}/metrics.json">Metrics JSON ↗</a><a class="button button-outline button-small" href="${base}/mask.npy" download>Binary mask ↓</a></div>`;
  }
  function renderExplorer() {
    const results = matching(state.explorer);
    // Detail always belongs to the current result set; no stale selection after filtering.
    if (!results.some(sample => sample.sample_id === state.selected)) state.selected = results[0]?.sample_id ?? null;
    $("explorer-count").textContent = `${number(results.length)} of ${number(samples.length)} images · ${Object.keys(data.previews).length} embedded previews in this report`;
    const page = paginate("explorer-pagination", state.explorer, results, renderExplorer);
    $("explorer-gallery").innerHTML = page.length ? page.map((sample, index) => card(sample, state.explorer.page * state.explorer.size + index, true)).join("") : empty;
    renderDetail();
  }
  function renderTable() {
    const results = matching(state.table);
    if (state.table.sort !== "sample") {
      const key = state.table.sort === "objects" ? "object_count" : "foreground_fraction";
      // Stable sorting retains canonical sample ID order for ties.
      results.sort((a, b) => b[key] - a[key]);
    }
    $("table-count").textContent = `${number(results.length)} of ${number(samples.length)} images`;
    const page = paginate("table-pagination", state.table, results, renderTable);
    $("measurement-rows").innerHTML = page.length ? page.map(sample => `<tr><td><button type="button" class="sample-link" data-sample="${escape(sample.sample_id)}">${escape(sample.sample_id)}</button></td><td>${sample.width} × ${sample.height}</td><td class="numeric">${number(sample.object_count)}</td><td class="numeric">${number(sample.foreground_fraction * 100, 2)}</td><td class="numeric">${number(sample.mean_area_px, 2)}</td><td>${badges(sample)}</td><td class="row-arrow" aria-hidden="true">↗</td></tr>`).join("") : `<tr><td colspan="7">${empty}</td></tr>`;
  }
  function openSample(id) {
    if (!byId.has(id)) return;
    state.selected = id;
    if (!matching(state.explorer).some(sample => sample.sample_id === id)) reset("explorer");
    const index = matching(state.explorer).findIndex(sample => sample.sample_id === id);
    state.explorer.page = Math.floor(index / state.explorer.size);
    const next = `#explorer/${encodeURIComponent(id)}`;
    if (location.hash === next) showView(true);
    else location.hash = next;
  }
  function showView(focus = false) {
    const [requested, encodedId] = location.hash.slice(1).split("/");
    const view = Object.hasOwn(titles, requested) ? requested : "overview";
    if (view === "explorer" && encodedId) {
      let id;
      try { id = decodeURIComponent(encodedId); } catch { id = null; }
      if (byId.has(id)) {
        if (!matching(state.explorer).some(sample => sample.sample_id === id)) reset("explorer");
        state.selected = id;
        state.explorer.page = Math.floor(matching(state.explorer).findIndex(sample => sample.sample_id === id) / state.explorer.size);
      }
    }
    document.querySelectorAll(".view").forEach(section => { section.hidden = section.id !== view; });
    document.querySelectorAll("[data-nav]").forEach(link => {
      if (link.dataset.nav === view) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
    $("breadcrumb-view").textContent = shortTitles[view];
    document.title = `${titles[view]} · ReproHPC`;
    if (view === "explorer") renderExplorer();
    if (view === "measurements") renderTable();
    if (focus) {
      const target = view === "explorer" && encodedId ? $("sample-detail") : $(`${view}-title`);
      target.setAttribute("tabindex", "-1");
      target.focus({preventScroll: true});
      if (view === "explorer" && encodedId && window.matchMedia("(max-width: 600px)").matches) {
        target.scrollIntoView({block: "start"});
      } else {
        window.scrollTo({top: 0, left: 0});
      }
    }
  }
  function reset(name) {
    state[name].query = ""; state[name].filter = "all"; state[name].page = 0;
    $(`${name}-search`).value = ""; $(`${name}-filter`).value = "all";
    if (name === "table") { state.table.sort = "sample"; $("table-sort").value = "sample"; }
  }
  function bindControls(name, render) {
    $(`${name}-search`).addEventListener("input", event => { state[name].query = event.target.value; state[name].page = 0; render(); });
    $(`${name}-filter`).addEventListener("change", event => { state[name].filter = event.target.value; state[name].page = 0; render(); });
    $(`${name}-reset`).addEventListener("click", () => { reset(name); render(); $(`${name}-search`).focus(); });
  }
  function renderOverview() {
    const shown = samples.length <= 12 ? samples : [...samples].sort((a, b) => b.object_count - a.object_count).slice(0, 12);
    const maximum = Math.max(1, ...shown.map(sample => sample.object_count));
    $("distribution").innerHTML = shown.length ? `<div class="chart-grid">${shown.map(sample => `<button class="bar-column" type="button" data-sample="${escape(sample.sample_id)}" aria-label="${escape(sample.sample_id)}: ${sample.object_count} objects. Inspect image."><span class="bar-value">${number(sample.object_count)}</span><span class="bar-fill" style="height:${Math.max(1, sample.object_count / maximum * 75)}%"></span><span class="bar-label">${escape(sample.sample_id)}</span></button>`).join("")}</div>` : '<div class="empty-state">No images in this aggregate.</div>';
    $("distribution-note").textContent = samples.length > 12 ? "12 images with the most objects · full dataset in table" : "All images · sorted by sample ID";
    const clear = samples.filter(sample => sample.qc.length === 0).length;
    $("quality-summary").innerHTML = [["No QC flags", clear, ""], ["Border objects", data.summary.qc.BORDER_OBJECTS || 0, "border"], ["No objects", data.summary.qc.NO_OBJECTS || 0, "empty"]].map(([label, count, type]) => `<div class="quality-row"><span class="quality-label"><span class="quality-indicator ${type}"></span>${label}</span><span class="quality-number">${number(count)} <span class="sr-only">images</span></span></div>`).join("");
    const available = samples.filter(sample => data.previews[sample.sample_id]);
    $("overview-gallery").innerHTML = available.length ? available.slice(0, 6).map((sample, index) => card(sample, index)).join("") : '<div class="empty-state"><strong>No previews embedded</strong><p>Image measurements and stored masks are available in the image explorer.</p></div>';
  }
  function renderProvenance() {
    const p = data.parameters;
    const rows = [["Algorithm", p.algorithm], ["Gaussian kernel", `${p.gaussian_kernel} × ${p.gaussian_kernel} px`], ["Gaussian sigma", p.gaussian_sigma], ["Foreground threshold", `> ${p.threshold} / 255`], ["Minimum component area", `${p.min_area_px} px²`], ["Connectivity", `${p.connectivity}-connected`], ["Deterministic seed", p.seed]];
    $("parameter-list").innerHTML = rows.map(([label, value]) => `<div class="parameter-row"><dt>${escape(label)}</dt><dd>${escape(value)}</dd></div>`).join("");
    const identities = [["Scientific parameters", data.parameter_sha256], ...data.identities.reference_sha256.map(value => ["Reference data", value]), ...data.identities.sif_sha256.map(value => ["Execution container · SIF", value])];
    $("identity-list").innerHTML = identities.map(([label, value], index) => `<div class="identity"><div class="identity-heading"><span>${escape(label)} <small>SHA-256</small></span><button type="button" class="button button-icon" data-copy="${index}" aria-label="Copy ${escape(label)} checksum">${icon("copy")}</button></div><code>${escape(value)}</code></div>`).join("");
    $("identity-list").querySelectorAll("[data-copy]").forEach(button => button.addEventListener("click", () => copy(identities[Number(button.dataset.copy)][1])));
    $("copy-parameters").addEventListener("click", () => copy(JSON.stringify(data.parameters, null, 2)));
  }
  // Full-screen viewer. Shows the same embedded preview at display size; it never
  // loads anything further, so it works identically offline and from file://.
  let viewerOpener = null;
  function viewable() {
    return matching(state.explorer).filter(sample => data.previews[sample.sample_id]);
  }
  function renderViewer() {
    const sample = byId.get(state.selected);
    const list = viewable();
    const index = list.findIndex(item => item.sample_id === sample.sample_id);
    $("viewer-title").textContent = sample.sample_id;
    $("viewer-image").src = data.previews[sample.sample_id];
    $("viewer-image").alt = `Segmentation overlay for ${sample.sample_id}, ${sample.object_count} objects`;
    // Navigation covers embedded previews only, so the count says so: the explorer can
    // list far more images than the report embeds.
    $("viewer-position").textContent =
      index < 0
        ? ""
        : `${number(index + 1)} of ${number(list.length)} embedded preview${list.length === 1 ? "" : "s"}`;
    $("viewer-caption").innerHTML = `<span><strong>${number(sample.object_count)}</strong> ${sample.object_count === 1 ? "object" : "objects"}</span><span>${sample.width} × ${sample.height} px</span><span>${number(sample.foreground_fraction * 100, 2)}% foreground</span><span>Mean area ${number(sample.mean_area_px, 2)} px²</span><span class="viewer-key">Red overlay · retained foreground</span>`;
    $("viewer-previous").disabled = index <= 0;
    $("viewer-next").disabled = index < 0 || index >= list.length - 1;
  }
  function openViewer(id, opener) {
    if (!data.previews[id]) return;
    state.selected = id;
    viewerOpener = opener ?? null;
    renderViewer();
    $("viewer").showModal();
    $("viewer-close").focus();
  }
  function stepViewer(direction) {
    const list = viewable();
    const index = list.findIndex(item => item.sample_id === state.selected);
    const next = list[index + direction];
    if (!next) return;
    state.selected = next.sample_id;
    renderViewer();
    renderExplorer();
  }
  $("viewer-close").addEventListener("click", () => $("viewer").close());
  $("viewer-previous").addEventListener("click", () => stepViewer(-1));
  $("viewer-next").addEventListener("click", () => stepViewer(1));
  $("viewer").addEventListener("keydown", event => {
    if (event.key === "ArrowLeft") stepViewer(-1);
    if (event.key === "ArrowRight") stepViewer(1);
  });
  // A click on the dialog itself is the backdrop; the bar and stage stop above it.
  $("viewer").addEventListener("click", event => {
    if (event.target === $("viewer")) $("viewer").close();
  });
  $("viewer").addEventListener("close", () => {
    // Escape and the close button both land here; return focus where it came from.
    renderExplorer();
    if (viewerOpener?.isConnected) viewerOpener.focus();
    else document.querySelector(`#sample-detail [data-expand]`)?.focus();
    viewerOpener = null;
  });
  bindControls("explorer", renderExplorer);
  bindControls("table", renderTable);
  $("table-sort").addEventListener("change", event => { state.table.sort = event.target.value; state.table.page = 0; renderTable(); });
  $("review-flags").addEventListener("click", () => { reset("explorer"); state.explorer.filter = "flagged"; $("explorer-filter").value = "flagged"; });
  document.addEventListener("click", event => {
    const expand = event.target.closest("[data-expand]");
    if (expand) {
      openViewer(expand.dataset.expand, expand);
      return;
    }
    const button = event.target.closest("[data-sample]");
    if (button) openSample(button.dataset.sample);
  });
  window.addEventListener("hashchange", () => showView(true));
  renderOverview();
  renderProvenance();
  renderTable();
  renderExplorer();
  showView();
})();
