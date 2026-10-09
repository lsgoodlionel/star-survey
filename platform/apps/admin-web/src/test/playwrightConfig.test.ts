import { afterEach, describe, expect, test, vi } from 'vitest';
import { mkdtemp, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import RunRootReporter from '../../e2e/runRootReporter';

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

  test('runs the mobile project only after the desktop journey succeeds', async () => {
    process.env.ADMIN_WEB_BASE_URL = 'http://127.0.0.1:4173';

    const config = await loadConfig();
    const mobile = config.projects?.find((project) => project.name === 'chromium-mobile');

    expect(mobile?.dependencies).toEqual(['chromium-desktop']);
  });

  test('registers the run-root reporter after the console reporter', async () => {
    process.env.ADMIN_WEB_BASE_URL = 'http://127.0.0.1:4173';

    const config = await loadConfig();

    expect(config.reporter).toEqual([
      ['line'],
      ['./e2e/runRootReporter.ts'],
    ]);
  });

  test('reporter writes a success marker without archiving the validated root', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'admin-web-reporter-'));
    const rootId = '11111111-1111-4111-8111-111111111111';
    process.env.ADMIN_WEB_TEST_RESULTS_DIR = directory;
    await writeFile(join(directory, 'run-root-resource-id.txt'), `${rootId}\n`);

    const result = await new RunRootReporter().onEnd({ status: 'passed' } as never);

    expect(result).toBeUndefined();
    expect(await readFile(join(directory, 'playwright-suite-success.txt'), 'utf8')).toBe('passed\n');
    expect(await readFile(join(directory, 'run-root-resource-id.txt'), 'utf8')).toBe(`${rootId}\n`);
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
