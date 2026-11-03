import { defineConfig } from "@playwright/test";
const externalBase = process.env.E2E_BASE_URL;
const localBase = "http://127.0.0.1:5191";
export default defineConfig({
  outputDir: "../.local/v010-verify/responsive-results",
  testDir: "./e2e",
  testMatch: "responsive.spec.ts",
  timeout: 30000,
  workers: 2,
  use: {
    baseURL: externalBase ?? localBase,
    hasTouch: true,
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "chromium", use: { browserName: "chromium" } },
    { name: "webkit", use: { browserName: "webkit" } },
  ],
  webServer: externalBase ? undefined : {
    command: "npm run dev -- --port 5191 --strictPort --configLoader runner",
    url: localBase,
    reuseExistingServer: false,
  },
});
