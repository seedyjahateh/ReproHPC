const {defineConfig} = require("@playwright/test");
module.exports = defineConfig({
  testDir: ".",
  testMatch: "report.spec.cjs",
  fullyParallel: true,
  workers: 2,
  timeout: 45000,
  reporter: [["list"], ["json", {outputFile: "../../evidence/ui-browser-results.json"}]],
  use: {
    channel: process.env.REPROHPC_BROWSER_CHANNEL || undefined,
    headless: true,
    reducedMotion: "reduce",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  projects: [
    {name: "desktop", use: {viewport: {width: 1440, height: 1080}}},
    {name: "mobile", use: {viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true}},
  ],
});
