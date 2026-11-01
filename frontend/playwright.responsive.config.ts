import { defineConfig } from "@playwright/test";
export default defineConfig({
  outputDir: "../.local/v004/responsive-results",
  testDir: "./e2e",
  testMatch: "responsive.spec.ts",
  timeout: 30000,
  workers: 2,
  use: {
    baseURL: "http://127.0.0.1:5175",
    hasTouch: true,
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "chromium", use: { browserName: "chromium" } },
    { name: "webkit", use: { browserName: "webkit" } },
  ],
  webServer: {
    command: "npm run dev -- --port 5175 --strictPort",
    url: "http://127.0.0.1:5175",
    reuseExistingServer: true,
  },
});
