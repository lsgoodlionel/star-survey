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

  test('writes only an explicitly sanitized screenshot and allowlisted trace summary', async () => {
    const config = await source('playwright.config.ts');
    const spec = await source('e2e/authoring.spec.ts');

    expect(config).toContain("trace: 'off'");
    expect(config).toContain("video: 'off'");
    expect(spec).toContain("testInfo.outputPath('sanitized-failure.png')");
    expect(spec).toContain("testInfo.outputPath('sanitized-trace-summary.json')");
    expect(spec).toContain("kind: 'sanitized-playwright-trace-summary'");
    expect(spec).not.toContain('testInfo.error');
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
    expect(spec).toContain('await clickAction(tab)');
    expect(spec).toContain("toHaveAttribute('aria-selected', 'true')");
    expect(spec).toContain('toBeInViewport()');
    expect(spec).toContain('boundingBox()');
    expect(spec).toContain('elementFromPoint');
  });

  test('validates the authenticated API identity instead of relying on shell text', async () => {
    const spec = await source('e2e/authoring.spec.ts');
    expect(spec).toContain('await meResponse.json()');
    expect(spec).toContain('expect(me.actorId).toBe(metadata.actorId)');
    expect(spec).toContain('expect(me.tenantId).toBe(metadata.tenantId)');
    expect(spec).not.toContain('getByText(metadata.actorId');
  });

  test('reuses the desktop survey for mobile and bounds failure artifact work', async () => {
    const spec = await source('e2e/authoring.spec.ts');
    expect(spec).toContain('const result = await readResult()');
    expect(spec.match(/await createResource\(page/g)).toHaveLength(3);
    expect(spec).toContain('if (page.isClosed()) return');
    expect(spec).toContain('testInfo.setTimeout(testInfo.timeout + 7_000)');
    expect(spec).toContain('withTimeout(redactSensitiveInputs(page), 1_500)');
    expect(spec).toContain("timeout: 2_000");
  });

  test('keeps authenticated authoring navigation inside the SPA', async () => {
    const spec = await source('e2e/authoring.spec.ts');

    expect(spec).not.toMatch(/page\.goto\(`\/surveys\//);
    expect(spec).toContain("getByRole('link', { name: '批量导入' })");
    expect(spec).toContain("getByRole('link', { name: '草稿预览' })");
    expect(spec).toContain("getByRole('link', { name: '返回编辑' })");
    expect(spec).toContain("getByRole('link', { name: '发布管理' })");
    expect(spec).toContain('await navigateWithinApp(page, `/surveys/${result.surveyId}/edit`)');
    expect(spec).toContain("window.history.pushState({}, '', path)");
    expect(spec).toContain("window.dispatchEvent(new PopStateEvent('popstate'))");
  });

  test('checks every browser action before interacting', async () => {
    const spec = await source('e2e/authoring.spec.ts');

    expect(spec).toContain('async function expectActionable(locator: Locator)');
    expect(spec).toContain('toBeVisible({ timeout: ACTION_TIMEOUT_MS })');
    expect(spec).toContain('toBeEnabled({ timeout: ACTION_TIMEOUT_MS })');
    expect(spec).toContain('const ACTION_TIMEOUT_MS = 15_000');
    expect(spec).not.toMatch(/await page\.getBy[^;]+\.(?:click|fill)\(/);
  });

  test('scopes the imported question assertion to the preview results', async () => {
    const spec = await source('e2e/authoring.spec.ts');

    expect(spec).toContain("const importPreview = page.getByRole('region', { name: '解析结果' })");
    expect(spec).toContain("importPreview.getByText('您的性别？', { exact: true })");
    expect(spec).not.toContain("page.getByText('您的性别？')).toBeVisible()\n  await clickAction");
  });
});

test('resolves the app root used by this contract', () => {
  expect(appRoot.endsWith('/platform/apps/admin-web')).toBe(true);
});
