import { expect, test, type Locator, type Page, type Response } from '@playwright/test';
import { mkdir, readFile, stat, writeFile } from 'node:fs/promises';
import { dirname } from 'node:path';
import { assertArtifactContainsNoSecret, redactSecret } from './artifacts';

interface TestMetadata {
  actorId?: string;
  tenantId?: string;
}

interface NetworkRecord {
  method: string;
  status: number;
  url: string;
}

interface MePayload {
  actorId: string;
  tenantId: string;
}

interface JourneyResult {
  surveyId: string;
  version: number;
}

const ACTION_TIMEOUT_MS = 15_000;

const importText = [
  '1. 您的性别？[单选]',
  'A. 男',
  'B. 女',
  '这一行认不出来',
  '2. 单选却没有选项[单选]',
].join('\n');

test.afterEach(async ({ page }, testInfo) => {
  if (testInfo.status === testInfo.expectedStatus) return;
  testInfo.setTimeout(testInfo.timeout + 7_000);
  if (page.isClosed()) return;
  try {
    await withTimeout(redactSensitiveInputs(page), 1_500);
    if (page.isClosed()) return;
    await page.screenshot({
      path: testInfo.outputPath('failure.png'),
      fullPage: true,
      timeout: 2_000,
    });
  } catch {
    // A crashed or inaccessible page is safer without a screenshot than with an unredacted one.
  }
});

test('author creates, imports, approves and publishes a survey', async ({ page }) => {
  const metadata = await readMetadata();
  const token = await readToken();
  const network: NetworkRecord[] = [];
  page.on('response', (response) => network.push(networkRecord(response, token)));

  const me = await login(page, token, metadata);

  const suffix = Date.now().toString(36);
  await createResource(page, '新建项目', '创建项目', `E2E 项目 ${suffix}`);
  await createResource(page, '新建文件夹', '创建文件夹', `E2E 文件夹 ${suffix}`);
  await createResource(page, '新建问卷', '创建问卷', `E2E 问卷 ${suffix}`);
  await expect(page).toHaveURL(/\/surveys\/[0-9a-f-]{36}\/edit/);
  const surveyId = surveyIdFrom(page.url());

  await fillAction(page.getByLabel('题目文本'), '这是一份真实浏览器验收问卷');
  await fillAction(page.getByLabel('标题'), `作者工作台验收 ${suffix}`);
  const saveResponse = page.waitForResponse((response) =>
    response.url().includes(`/v1/surveys/${surveyId}/draft`) && response.request().method() === 'PUT',
  );
  await clickAction(page.getByRole('button', { name: '保存草稿' }));
  expect((await saveResponse).status()).toBe(200);
  await expect(page.getByText(/已保存版本 \d+/)).toBeVisible();

  await clickAction(page.getByRole('link', { name: '批量导入' }));
  await fillAction(page.getByLabel('待导入文本'), importText);
  await clickAction(page.getByRole('button', { name: '预览导入' }));
  await expect(page.getByText('第 4 行')).toBeVisible();
  await expect(page.getByText('您的性别？')).toBeVisible();
  await clickAction(page.getByRole('button', { name: '确认导入 1 道题' }));
  await expect(page).toHaveURL(new RegExp(`/surveys/${surveyId}/edit$`));

  await clickAction(page.getByRole('link', { name: '草稿预览' }));
  await expect(page.getByRole('heading', { name: '草稿预览' })).toBeVisible();
  await expect(page.getByText('您的性别？')).toBeVisible();
  await clickAction(page.getByRole('link', { name: '返回编辑' }));
  await expect(page).toHaveURL(new RegExp(`/surveys/${surveyId}/edit$`));

  await clickAction(page.getByRole('link', { name: '发布管理' }));
  await clickAction(page.getByRole('button', { name: '提交审批' }));
  await expect(page.getByRole('button', { name: '批准申请' })).toBeVisible();
  await clickAction(page.getByRole('button', { name: '批准申请' }));
  await expect(page.getByRole('button', { name: '发布问卷' })).toBeVisible();
  await clickAction(page.getByRole('button', { name: '发布问卷' }));
  await expect(page.getByText('发布成功')).toBeVisible({ timeout: 210_000 });

  const versionLink = page.getByRole('link', { name: /查看版本 \d+/ }).first();
  const version = Number((await versionLink.textContent())?.match(/\d+/)?.[0]);
  expect(version).toBeGreaterThan(0);
  await clickAction(versionLink);
  await expect(page.getByRole('heading', { name: `已发布版本 ${version}` })).toBeVisible();
  await expect(page.getByText('当前在线')).toBeVisible();
  await expect(page.getByText('字段映射')).toBeVisible();

  await writeResult({ network, surveyId, tenantId: me.tenantId, version }, token);
});

test('@mobile editor keeps tabs and primary actions usable without horizontal overflow', async ({ page }) => {
  const metadata = await readMetadata();
  const token = await readToken();
  await login(page, token, metadata);
  const result = await readResult();
  await navigateWithinApp(page, `/surveys/${result.surveyId}/edit`);
  await expect(page).toHaveURL(new RegExp(`/surveys/${result.surveyId}/edit$`));

  const tabs = page.getByRole('tablist', { name: '编辑区域' });
  await expect(tabs).toBeVisible();
  const panels = [
    { name: '大纲', control: page.getByRole('button', { name: /QNOTE/ }).first() },
    { name: '编辑', control: page.getByLabel('题目文本') },
    { name: '属性', control: page.getByLabel('标题') },
  ];
  for (const { name, control } of panels) {
    const tab = page.getByRole('tab', { name });
    await clickAction(tab);
    await expect(tab).toHaveAttribute('aria-selected', 'true');
    const panel = page.getByRole('tabpanel', { name });
    await expect(panel).toBeVisible();
    await assertUsable(tab);
    await assertUsable(panel);
    await assertUsable(control);
    await page.keyboard.press('Tab');
    expect(await page.locator(':focus').evaluate((element) => getComputedStyle(element).outlineStyle))
      .not.toBe('none');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  }
  await assertUsable(page.getByRole('button', { name: '保存草稿' }));
});

async function createResource(page: Page, trigger: string, submit: string, value: string) {
  await clickAction(page.getByRole('button', { name: trigger }));
  const dialog = page.getByRole('dialog', { name: trigger });
  await fillAction(dialog.getByRole('textbox'), value);
  await clickAction(dialog.getByRole('button', { name: submit }));
  await expect(dialog).toBeHidden();
}

async function login(page: Page, token: string, metadata: TestMetadata) {
  await page.goto('/dev/token');
  const tokenInput = page.getByLabel('测试令牌');
  try {
    const meResponsePromise = page.waitForResponse((response) =>
      response.url().includes('/v1/me') && response.request().method() === 'GET',
    );
    await fillAction(tokenInput, token);
    await clickAction(page.getByRole('button', { name: '登录' }));
    const meResponse = await meResponsePromise;
    expect(meResponse.status()).toBe(200);
    const payload: unknown = await meResponse.json();
    const me = parseMe(payload);
    if (metadata.actorId) expect(me.actorId).toBe(metadata.actorId);
    if (metadata.tenantId) expect(me.tenantId).toBe(metadata.tenantId);
    await expect(page).toHaveURL(/\/workspace$/);
    return me;
  } finally {
    if (!page.isClosed()) {
      await withTimeout(redactSensitiveInputs(page), 1_500).catch(() => undefined);
    }
  }
}

async function navigateWithinApp(page: Page, path: string) {
  await page.evaluate((path) => {
    window.history.pushState({}, '', path);
    window.dispatchEvent(new PopStateEvent('popstate'));
  }, path);
}

async function expectActionable(locator: Locator) {
  await expect(locator).toBeVisible({ timeout: ACTION_TIMEOUT_MS });
  await expect(locator).toBeEnabled({ timeout: ACTION_TIMEOUT_MS });
}

async function clickAction(locator: Locator) {
  await expectActionable(locator);
  await locator.click();
}

async function fillAction(locator: Locator, value: string) {
  await expectActionable(locator);
  await locator.fill(value);
}

async function readToken() {
  const path = requiredEnv('ADMIN_WEB_JWT_FILE');
  const mode = (await stat(path)).mode & 0o777;
  if (mode !== 0o600) throw new Error('ADMIN_WEB_JWT_FILE must use mode 0600');
  const token = (await readFile(path, 'utf8')).trim();
  if (!token) throw new Error('ADMIN_WEB_JWT_FILE is empty');
  return token;
}

async function readMetadata(): Promise<TestMetadata> {
  const path = process.env.ADMIN_WEB_METADATA_FILE;
  return path ? JSON.parse(await readFile(path, 'utf8')) as TestMetadata : {};
}

async function readResult(): Promise<JourneyResult> {
  const payload: unknown = JSON.parse(
    await readFile(requiredEnv('ADMIN_WEB_RESULT_FILE'), 'utf8'),
  );
  if (!isRecord(payload)
    || typeof payload.surveyId !== 'string'
    || !/^[0-9a-f-]{36}$/i.test(payload.surveyId)
    || typeof payload.version !== 'number'
    || !Number.isInteger(payload.version)
    || payload.version < 1) {
    throw new Error('ADMIN_WEB_RESULT_FILE does not contain a completed desktop journey');
  }
  return { surveyId: payload.surveyId, version: payload.version };
}

async function writeResult(result: Record<string, unknown>, token: string) {
  assertArtifactContainsNoSecret(result, token);
  const path = requiredEnv('ADMIN_WEB_RESULT_FILE');
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, `${JSON.stringify(result, null, 2)}\n`, { mode: 0o600 });
}

function networkRecord(response: Response, token: string): NetworkRecord {
  const url = new URL(response.url());
  return {
    method: response.request().method(),
    status: response.status(),
    url: redactSecret(`${url.origin}${url.pathname}${url.search}`, token),
  };
}

async function redactSensitiveInputs(page: Page) {
  await page.evaluate(() => {
    const elements = document.querySelectorAll('[data-sensitive="token"], input[type="password"]');
    for (const element of elements) {
      if (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement) {
        element.value = '';
      }
      (element as HTMLElement).style.visibility = 'hidden';
    }
  });
}

async function withTimeout<T>(operation: Promise<T>, timeoutMs: number): Promise<T> {
  let timeout: ReturnType<typeof setTimeout> | undefined;
  const deadline = new Promise<never>((_, reject) => {
    timeout = setTimeout(() => reject(new Error('Artifact sanitization timed out')), timeoutMs);
  });
  try {
    return await Promise.race([operation, deadline]);
  } finally {
    if (timeout) clearTimeout(timeout);
  }
}

async function assertUsable(locator: Locator) {
  await expect(locator).toBeVisible();
  await expect(locator).toBeInViewport();
  const box = await locator.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.width).toBeGreaterThanOrEqual(24);
  expect(box!.height).toBeGreaterThanOrEqual(24);
  expect(await locator.evaluate((element, point) => {
    const hit = document.elementFromPoint(point.x, point.y);
    return hit === element || (hit !== null && element.contains(hit));
  }, { x: box!.x + box!.width / 2, y: box!.y + box!.height / 2 })).toBe(true);
}

function surveyIdFrom(url: string) {
  const match = new URL(url).pathname.match(/^\/surveys\/([0-9a-f-]{36})\/edit$/i);
  if (!match) throw new Error('Survey id is missing from the editor URL');
  return match[1];
}

function parseMe(payload: unknown): MePayload {
  if (!isRecord(payload)
    || typeof payload.actorId !== 'string'
    || typeof payload.tenantId !== 'string') {
    throw new Error('/v1/me returned an invalid identity payload');
  }
  return { actorId: payload.actorId, tenantId: payload.tenantId };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function requiredEnv(name: string) {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required`);
  return value;
}
