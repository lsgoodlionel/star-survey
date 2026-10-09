import { chromium, type Locator, type Page } from '@playwright/test';
import { readFile, stat } from 'node:fs/promises';
import { assertNoTimestampResources, stableDemoHierarchy } from './demoDataEvidence.ts';

interface DemoAccess {
  adminWebUrl: string;
  platformAccessToken: string;
}

const accessFile = requiredEnv('ADMIN_WEB_DEMO_ACCESS_FILE');
const access = await readAccess(accessFile);
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1024, height: 900 } });
page.setDefaultTimeout(10_000);

try {
  await login(page, access);
  await inspectAllLoadedResources(page);
  await selectCurrentResource(page, stableDemoHierarchy[0]);
  await inspectAllLoadedResources(page);
  await selectCurrentResource(page, stableDemoHierarchy[1]);
  await inspectAllLoadedResources(page);
  await selectSurvey(page, stableDemoHierarchy[2]);
  process.stdout.write('adminweb-demo browser data gate passed\n');
} finally {
  if (!page.isClosed() && await page.getByLabel('测试令牌').isVisible().catch(() => false)) {
    await page.getByLabel('测试令牌').fill('').catch(() => undefined);
  }
  await browser.close();
}

async function readAccess(path: string): Promise<DemoAccess> {
  if (((await stat(path)).mode & 0o777) !== 0o600) {
    throw new Error('demo access file must use mode 0600');
  }
  const payload: unknown = JSON.parse(await readFile(path, 'utf8'));
  if (!isRecord(payload)
    || typeof payload.adminWebUrl !== 'string'
    || typeof payload.platformAccessToken !== 'string'
    || !payload.adminWebUrl
    || !payload.platformAccessToken) {
    throw new Error('demo access file is missing required fields');
  }
  return {
    adminWebUrl: payload.adminWebUrl,
    platformAccessToken: payload.platformAccessToken,
  };
}

async function login(page: Page, access: DemoAccess) {
  await page.goto(new URL('/dev/token', access.adminWebUrl).toString());
  const tokenInput = page.getByLabel('测试令牌');
  const identityResponse = page.waitForResponse((response) =>
    new URL(response.url()).pathname === '/v1/me'
      && response.request().method() === 'GET',
  );
  try {
    await tokenInput.fill(access.platformAccessToken);
    await page.getByRole('button', { name: '登录' }).click();
    if (!(await identityResponse).ok()) throw new Error('demo browser identity check failed');
    await page.getByRole('heading', { name: '资源工作台' }).waitFor();
  } finally {
    if (await tokenInput.isVisible().catch(() => false)) {
      await tokenInput.fill('').catch(() => undefined);
    }
  }
}

async function inspectAllLoadedResources(page: Page) {
  await loadAll(page, page.getByRole('button', { name: '加载更多项目' }));
  await loadAll(page, page.getByRole('button', { name: '加载更多', exact: true }));
  const names = await page.locator('.project-nav-item, .resource-name').allTextContents();
  assertNoTimestampResources(names.map((name) => name.trim()));
}

async function loadAll(page: Page, button: Locator) {
  for (let pageNumber = 0; pageNumber < 100; pageNumber += 1) {
    if (!await button.isVisible().catch(() => false)) return;
    const response = page.waitForResponse((candidate) =>
      new URL(candidate.url()).pathname === '/v1/resources'
        && candidate.request().method() === 'GET',
    );
    await button.click();
    if (!(await response).ok()) throw new Error('demo resource pagination failed');
  }
  throw new Error('demo resource pagination exceeded 100 pages');
}

async function selectCurrentResource(page: Page, name: string) {
  const resource = currentResource(page, name);
  await resource.waitFor();
  await resource.click();
  await page.getByRole('navigation', { name: '当前位置' }).getByText(name, { exact: true }).waitFor();
}

async function selectSurvey(page: Page, name: string) {
  const resource = currentResource(page, name);
  await resource.waitFor();
  await resource.click();
  await page.getByLabel('所选资源操作').waitFor();
  await page.getByRole('link', { name: /^(?:编辑|查看)问卷$/ }).click();
  await page.waitForURL(/\/surveys\/[0-9a-f-]{36}\/edit$/i);
  await page.getByRole('navigation', { name: '面包屑' }).getByText(name, { exact: true }).waitFor();
}

function currentResource(page: Page, name: string) {
  return page.getByRole('list', { name: '当前位置资源' })
    .getByRole('button', { name, exact: true });
}

function requiredEnv(name: string) {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required`);
  return value;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
