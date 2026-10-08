import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { describe, expect, test } from 'vitest';

const appRoot = process.cwd();

async function source(relativePath: string) {
  return readFile(resolve(appRoot, relativePath), 'utf8');
}

describe('browser gate security contract', () => {
  test('disables automatic screenshots and sanitizes before a manual failure screenshot', async () => {
    const config = await source('playwright.config.ts');
    const spec = await source('e2e/authoring.spec.ts');

    expect(config).toContain("screenshot: 'off'");
    expect(config).not.toContain("screenshot: 'only-on-failure'");
    expect(spec).toContain('test.afterEach');
    expect(spec.indexOf('redactSensitiveInputs(page)')).toBeGreaterThan(-1);
    expect(spec.indexOf('redactSensitiveInputs(page)')).toBeLessThan(spec.indexOf('page.screenshot'));
    expect(spec).toContain('try {');
    expect(spec).toContain('finally {');
    expect(spec).toContain('assertArtifactContainsNoSecret');
  });

  test('uses a password control for defense in depth', async () => {
    const page = await source('src/features/auth/DevTokenPage.tsx');
    expect(page).toContain('type="password"');
  });

  test('proxies only exact v1 routes and leaves lookalike paths to the SPA', async () => {
    const nginx = await source('nginx.conf');
    expect(nginx).toContain('location = /v1 {');
    expect(nginx).toContain('location ^~ /v1/ {');
    expect(nginx).not.toMatch(/location \^~ \/v1\s*\{/);
  });

  test('requires real mobile tab interaction and viewport geometry assertions', async () => {
    const spec = await source('e2e/authoring.spec.ts');
    expect(spec).toContain('await tab.click()');
    expect(spec).toContain("toHaveAttribute('aria-selected', 'true')");
    expect(spec).toContain('toBeInViewport()');
    expect(spec).toContain('boundingBox()');
    expect(spec).toContain('elementFromPoint');
  });
});

test('resolves the app root used by this contract', () => {
  expect(appRoot.endsWith('/platform/apps/admin-web')).toBe(true);
});
