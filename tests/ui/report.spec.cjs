const {test, expect} = require("@playwright/test");
const {default: AxeBuilder} = require("@axe-core/playwright");
const {pathToFileURL} = require("node:url");
const path = require("node:path");
const fs = require("node:fs");
const root = path.resolve(__dirname, "../..");
const fixtures = path.resolve(process.env.REPROHPC_REPORT_FIXTURES || path.join(root, "artifacts/ui-fixtures"));
const url = (variant = "demo") => pathToFileURL(path.join(fixtures, variant, "report/index.html")).href;

async function noOverflow(page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
}

for (const view of ["overview", "explorer", "measurements", "provenance"]) {
test(`${view} renders offline, stays within viewport, and passes automated accessibility checks`, async ({page, context}, info) => {
  const errors = [], requests = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("request", request => { if (/^https?:/.test(request.url())) requests.push(request.url()); });
  await context.setOffline(true);
  await page.goto(url());
  await expect(page.locator(".stat-value")).toHaveText(["12images", "9objects", "8images", "12previews"]);
    await page.locator(`[data-nav="${view}"]`).click();
    await expect(page.locator(`#${view}`)).toBeVisible();
    await expect(page.locator(".view:visible")).toHaveCount(1);
    await noOverflow(page);
    await page.screenshot({path: path.join(root, `evidence/ui/${info.project.name}-${view}.png`), fullPage: true});
    const audit = await new AxeBuilder({page}).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
    expect(audit.violations.map(v => ({id: v.id, nodes: v.nodes.map(n => ({target: n.target, reason: n.failureSummary}))}))).toEqual([]);
  expect(requests).toEqual([]);
  expect(errors).toEqual([]);
});
}

test("image search, quality filters, selection, empty state and reset reflect scientific results", async ({page}) => {
  await page.goto(url());
  await page.locator("#review-flags").click();
  await expect(page.locator("#explorer-count")).toContainText("8 of 12 images");
  await page.locator("#explorer-filter").selectOption("NO_OBJECTS");
  await expect(page.locator("#explorer-count")).toContainText("4 of 12 images");
  await page.locator("#explorer-reset").click();
  await page.locator("#explorer-search").fill("SQUARE");
  await expect(page.locator("#explorer-gallery .preview-card")).toHaveCount(1);
  await page.locator("#explorer-gallery .preview-card").click();
  await expect(page.locator("#sample-detail h2")).toHaveText("square");
  await expect(page.locator("#sample-detail")).toContainText("252 px²");
  await expect(page.locator("#sample-detail")).toContainText("6.15 %");
  await expect(page.locator("#sample-detail a").first()).toHaveAttribute("href", "../samples/square/objects.csv");
  await page.locator("#explorer-search").fill("no-such-sample");
  await expect(page.locator("#explorer-gallery")).toContainText("No matching images");
  await expect(page.locator("#sample-detail")).toContainText("No image selected");
  await page.locator("#explorer-reset").click();
  await expect(page.locator("#explorer-gallery .preview-card")).toHaveCount(12);
  await expect(page.locator("#explorer-search")).toBeFocused();
});

test("measurement sorting, null values, quality filtering and keyboard navigation work", async ({page}) => {
  await page.goto(url() + "#measurements");
  await expect(page.locator("#measurement-rows tr")).toHaveCount(12);
  await page.locator("#table-sort").selectOption("objects");
  await expect(page.locator("#measurement-rows tr").first()).toContainText("two");
  await page.locator("#table-filter").selectOption("clear");
  await expect(page.locator("#table-count")).toHaveText("4 of 12 images");
  await page.locator("#table-reset").click();
  await page.locator("#table-search").fill("blank");
  await expect(page.locator("#measurement-rows tr")).toHaveCount(1);
  await expect(page.locator("#measurement-rows tr td").nth(4)).toHaveText("—");
  const sample = page.locator("#measurement-rows .sample-link");
  await sample.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("#sample-detail h2")).toHaveText("blank");
  await expect(page.locator("#sample-detail")).toBeFocused();
  await page.goBack();
  await expect(page.locator("#measurements")).toBeVisible();
  await expect(page.locator("#table-search")).toHaveValue("blank");
});

test("large collections paginate completely, cap previews and handle long IDs", async ({page}) => {
  await page.goto(url("large") + "#measurements");
  await noOverflow(page);
  await expect(page.locator("#measurement-rows tr")).toHaveCount(25);
  await page.locator("#table-pagination [data-page='1']").click();
  await expect(page.locator("#measurement-rows tr")).toHaveCount(25);
  await page.locator("#table-pagination [data-page='1']").click();
  await expect(page.locator("#measurement-rows tr")).toHaveCount(15);
  await expect(page.locator("#table-pagination")).toContainText("51–65 of 65 images");
  await expect(page.locator("#table-pagination [data-page='1']")).toBeDisabled();
  await page.locator("#measurement-rows .sample-link").last().click();
  await expect(page.locator("#sample-detail h2")).toContainText("sample-064-");
  await expect(page.locator("#sample-detail")).toContainText("Preview not embedded");
  await expect(page.locator("#explorer-pagination")).toContainText("61–65 of 65 images");
  await expect(page.locator("#explorer-gallery .preview-card")).toHaveCount(5);
  await noOverflow(page);
  const data = JSON.parse(await page.locator("#report-data").textContent());
  expect(Object.keys(data.previews)).toHaveLength(24);
  expect(data.samples).toHaveLength(65);
  await page.locator("#explorer-search").fill("sample-003");
  await expect(page.locator("#explorer-pagination")).toContainText("1–1 of 1 images");
  await expect(page.locator("#sample-detail img")).toBeVisible();
});

test("zero-object datasets and disabled previews remain useful without NaN or broken images", async ({page}) => {
  await page.goto(url("no-previews"));
  await expect(page.locator("#overview-gallery")).toContainText("No previews embedded");
  await expect(page.locator(".stat-value").nth(1)).toHaveText("0objects");
  await page.locator("[data-nav=explorer]").click();
  await expect(page.locator("#sample-detail")).toContainText("mean area is undefined");
  await expect(page.locator("#sample-detail img")).toHaveCount(0);
  await page.locator("#explorer-search").fill('</script><img src=x onerror="window.injected=1">');
  await expect(page.locator("#explorer-gallery")).toContainText("No matching images");
  expect(await page.evaluate(() => window.injected)).toBeUndefined();
  expect(await page.locator("main").innerText()).not.toContain("NaN");
  await noOverflow(page);
});

test("deep links, malformed hashes and clipboard denial have explicit behavior", async ({page, context}) => {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(url() + "#explorer/square");
  await expect(page.locator("#sample-detail h2")).toHaveText("square");
  await page.goto(url() + "#explorer/%E0%A4%A");
  await expect(page.locator("#explorer")).toBeVisible();
  await page.goto(url() + "#__proto__");
  await expect(page.locator("#overview")).toBeVisible();
  await page.locator("[data-nav=provenance]").click();
  await context.grantPermissions([]);
  // Browsers can deny clipboard APIs on file origins. Both real outcomes are supported.
  await page.locator("#copy-parameters").click();
  await expect(page.locator("#toast")).toContainText(/Copied to clipboard|Clipboard access is unavailable/);
  expect(errors).toEqual([]);
});

test("full-screen viewer opens, navigates, closes and restores focus", async ({page}) => {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(url() + "#explorer/square");
  const viewer = page.locator("#viewer");
  await expect(viewer).toBeHidden();

  // The detail image and the explicit action both open the same viewer.
  await page.locator("#sample-detail .preview-expand").click();
  await expect(viewer).toBeVisible();
  await expect(page.locator("#viewer-title")).toHaveText("square");
  await expect(page.locator("#viewer-image")).toHaveAttribute("src", /^data:image\/png;base64,/);
  await expect(page.locator("#viewer-caption")).toContainText("252 px²");
  await expect(page.locator("#viewer-close")).toBeFocused();
  const audit = await new AxeBuilder({page}).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(audit.violations.map(v => v.id)).toEqual([]);

  // Position and navigation follow the filtered explorer order.
  await expect(page.locator("#viewer-position")).toHaveText("10 of 12 embedded previews");
  await page.locator("#viewer-previous").click();
  await expect(page.locator("#viewer-title")).toHaveText("small");
  await page.keyboard.press("ArrowRight");
  await expect(page.locator("#viewer-title")).toHaveText("square");
  await page.keyboard.press("Escape");
  await expect(viewer).toBeHidden();
  await expect(page.locator("#sample-detail .preview-expand")).toBeFocused();

  // The selection made in the viewer is kept by the explorer behind it.
  await page.locator("#sample-detail .detail-actions [data-expand]").click();
  await page.locator("#viewer-previous").click();
  await page.locator("#viewer-close").click();
  await expect(page.locator("#sample-detail h2")).toHaveText("small");
  expect(errors).toEqual([]);
});

test("full-screen viewer ends at collection boundaries and is absent without a preview", async ({page}) => {
  await page.goto(url("large") + "#explorer");
  await page.locator("#explorer-search").fill("sample-000");
  await page.locator("#explorer-gallery .preview-card").first().click();
  await page.locator("#sample-detail .preview-expand").click();
  await expect(page.locator("#viewer-previous")).toBeDisabled();
  await expect(page.locator("#viewer-next")).toBeDisabled();
  await expect(page.locator("#viewer-position")).toHaveText("1 of 1 embedded preview");
  await page.keyboard.press("ArrowLeft");
  await expect(page.locator("#viewer-title")).toHaveText(/^sample-000/);
  await page.locator("#viewer-close").click();

  // Samples beyond the 24-preview cap have no image to enlarge.
  await page.locator("#explorer-reset").click();
  await page.locator("#explorer-search").fill("sample-064");
  await page.locator("#explorer-gallery .preview-card").first().click();
  await expect(page.locator("#sample-detail")).toContainText("Preview not embedded");
  await expect(page.locator("#sample-detail [data-expand]")).toHaveCount(0);
  await expect(page.locator("#viewer")).toBeHidden();
});

test("without JavaScript, counts, scientific parameters and CSV links stay available", async ({browser}) => {
  const context = await browser.newContext({javaScriptEnabled: false});
  const page = await context.newPage();
  await page.goto(url());
  await expect(page.locator("#overview-title")).toBeVisible();
  await expect(page.locator(".no-script")).toContainText('"threshold": 127');
  await expect(page.locator(".no-script")).toContainText('"NO_OBJECTS": 4');
  await expect(page.locator("#overview-gallery .preview-card")).toHaveCount(0);
  await expect(page.locator("a[href='../summary/images.csv']").first()).toBeVisible();
  expect(fs.existsSync(path.join(fixtures, "demo/summary/images.csv"))).toBe(true);
  await context.close();
});
