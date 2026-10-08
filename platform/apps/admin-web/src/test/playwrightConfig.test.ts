import { afterEach, describe, expect, test, vi } from 'vitest';

const originalBaseUrl = process.env.ADMIN_WEB_BASE_URL;
const originalResultsDir = process.env.ADMIN_WEB_TEST_RESULTS_DIR;

afterEach(() => {
  restoreEnv('ADMIN_WEB_BASE_URL', originalBaseUrl);
  restoreEnv('ADMIN_WEB_TEST_RESULTS_DIR', originalResultsDir);
  vi.resetModules();
});

describe('Playwright result artifact location', () => {
  test('uses the runner-provided isolated results directory unchanged', async () => {
    process.env.ADMIN_WEB_BASE_URL = 'http://127.0.0.1:4173';
    process.env.ADMIN_WEB_TEST_RESULTS_DIR = '/private/tmp/admin-web-run-42/test-results';

    const config = await loadConfig();

    expect(config.outputDir).toBe('/private/tmp/admin-web-run-42/test-results');
  });

  test('keeps the local default when the runner does not provide a directory', async () => {
    process.env.ADMIN_WEB_BASE_URL = 'http://127.0.0.1:4173';
    delete process.env.ADMIN_WEB_TEST_RESULTS_DIR;

    const config = await loadConfig();

    expect(config.outputDir).toBe('./test-results');
  });
});

async function loadConfig() {
  vi.resetModules();
  return (await import('../../playwright.config')).default;
}

function restoreEnv(name: string, value: string | undefined) {
  if (value === undefined) delete process.env[name];
  else process.env[name] = value;
}
