import { defineConfig, devices } from '@playwright/test';

// Explicit invocation only. Never disable TLS validation, collect traces or emit screenshots.
export default defineConfig({
  testDir: '.', testMatch: 'preview.mjs', timeout: 600_000, retries: 0, reporter: 'line',
  use: { ...devices['Desktop Chrome'], baseURL: 'https://survey-native.test',
         screenshot: 'off', trace: 'off', video: 'off', ignoreHTTPSErrors: false },
});
