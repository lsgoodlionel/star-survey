import { defineConfig, devices } from '@playwright/test';

const baseURL = process.env.ADMIN_WEB_BASE_URL;
if (!baseURL) throw new Error('ADMIN_WEB_BASE_URL is required');
const outputDir = process.env.ADMIN_WEB_TEST_RESULTS_DIR || './test-results';

export default defineConfig({
  testDir: './e2e',
  outputDir,
  timeout: 240_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: [['line']],
  use: {
    baseURL,
    screenshot: 'off',
    trace: 'off',
    video: 'off',
  },
  projects: [
    {
      name: 'chromium-desktop',
      use: { ...devices['Desktop Chrome'] },
      testMatch: /authoring\.spec\.ts/,
      grepInvert: /@mobile/,
    },
    {
      name: 'chromium-mobile',
      use: { ...devices['Pixel 7'] },
      testMatch: /authoring\.spec\.ts/,
      grep: /@mobile/,
    },
  ],
});
