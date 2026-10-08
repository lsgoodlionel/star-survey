import { expect, test, type Page, type Response } from '@playwright/test';
import { mkdir, readFile, stat, writeFile } from 'node:fs/promises';
import { dirname } from 'node:path';

interface TestMetadata {
  actorId?: string;
  tenantId?: string;
}

interface NetworkRecord {
  method: string;
  status: number;
  url: string;
}

const importText = [
  '1. 您的性别？[单选]',
  'A. 男',
  'B. 女',
  '这一行认不出来',
  '2. 单选却没有选项[单选]',
].join('\n');

test('author creates, imports, approves and publishes a survey', async ({ page }) => {
  const metadata = await readMetadata();
  const token = await readToken();
  const network: NetworkRecord[] = [];
  page.on('response', (response) => network.push(networkRecord(response)));

  await page.goto('/dev/token');
  const meResponse = page.waitForResponse((response) =>
    response.url().includes('/v1/me') && response.request().method() === 'GET',
  );
  const tokenInput = page.getByLabel('测试令牌');
  await tokenInput.fill(token);
  await page.getByRole('button', { name: '登录' }).click();
  await tokenInput.fill('').catch(() => undefined);
  expect((await meResponse).status()).toBe(200);
  await expect(page).toHaveURL(/\/workspace$/);
  if (metadata.actorId) await expect(page.getByText(metadata.actorId, { exact: true })).toBeVisible();

  const suffix = Date.now().toString(36);
  await createResource(page, '新建项目', '创建项目', `E2E 项目 ${suffix}`);
  await createResource(page, '新建文件夹', '创建文件夹', `E2E 文件夹 ${suffix}`);
  await createResource(page, '新建问卷', '创建问卷', `E2E 问卷 ${suffix}`);
  await expect(page).toHaveURL(/\/surveys\/[0-9a-f-]{36}\/edit/);
  const surveyId = surveyIdFrom(page.url());

  await page.getByLabel('题目文本').fill('这是一份真实浏览器验收问卷');
  await page.getByLabel('标题').fill(`作者工作台验收 ${suffix}`);
  const saveResponse = page.waitForResponse((response) =>
    response.url().includes(`/v1/surveys/${surveyId}/draft`) && response.request().method() === 'PUT',
  );
  await page.getByRole('button', { name: '保存草稿' }).click();
  expect((await saveResponse).status()).toBe(200);
  await expect(page.getByText(/已保存版本 \d+/)).toBeVisible();

  await page.goto(`/surveys/${surveyId}/import`);
  await page.getByLabel('待导入文本').fill(importText);
  await page.getByRole('button', { name: '预览导入' }).click();
  await expect(page.getByText('第 4 行')).toBeVisible();
  await expect(page.getByText('您的性别？')).toBeVisible();
  await page.getByRole('button', { name: '确认导入 1 道题' }).click();
  await expect(page).toHaveURL(new RegExp(`/surveys/${surveyId}/edit$`));

  await page.goto(`/surveys/${surveyId}/preview`);
  await expect(page.getByRole('heading', { name: '草稿预览' })).toBeVisible();
  await expect(page.getByText('您的性别？')).toBeVisible();

  await page.goto(`/surveys/${surveyId}/publish`);
  await page.getByRole('button', { name: '提交审批' }).click();
  await expect(page.getByRole('button', { name: '批准申请' })).toBeVisible();
  await page.getByRole('button', { name: '批准申请' }).click();
  await expect(page.getByRole('button', { name: '发布问卷' })).toBeVisible();
  await page.getByRole('button', { name: '发布问卷' }).click();
  await expect(page.getByText('发布成功')).toBeVisible({ timeout: 210_000 });

  const versionLink = page.getByRole('link', { name: /查看版本 \d+/ }).first();
  const version = Number((await versionLink.textContent())?.match(/\d+/)?.[0]);
  expect(version).toBeGreaterThan(0);
  await versionLink.click();
  await expect(page.getByRole('heading', { name: `已发布版本 ${version}` })).toBeVisible();
  await expect(page.getByText('当前在线')).toBeVisible();
  await expect(page.getByText('字段映射')).toBeVisible();

  await writeResult({ network, surveyId, tenantId: metadata.tenantId, version });
});

test('@mobile editor keeps tabs and primary actions usable without horizontal overflow', async ({ page }) => {
  const token = await readToken();
  await login(page, token);
  const suffix = `mobile-${Date.now().toString(36)}`;
  await createResource(page, '新建项目', '创建项目', `移动项目 ${suffix}`);
  await createResource(page, '新建文件夹', '创建文件夹', `移动文件夹 ${suffix}`);
  await createResource(page, '新建问卷', '创建问卷', `移动问卷 ${suffix}`);
  surveyIdFrom(page.url());

  const tabs = page.getByRole('tablist', { name: '编辑区域' });
  await expect(tabs).toBeVisible();
  for (const name of ['大纲', '编辑', '属性']) {
    const tab = page.getByRole('tab', { name });
    await expect(tab).toBeVisible();
    await tab.focus();
    expect(await tab.evaluate((element) => getComputedStyle(element).outlineStyle)).not.toBe('none');
  }
  await expect(page.getByRole('button', { name: '保存草稿' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

async function createResource(page: Page, trigger: string, submit: string, value: string) {
  await page.getByRole('button', { name: trigger }).click();
  const dialog = page.getByRole('dialog', { name: trigger });
  await dialog.getByRole('textbox').fill(value);
  await dialog.getByRole('button', { name: submit }).click();
  await expect(dialog).toBeHidden();
}

async function login(page: Page, token: string) {
  await page.goto('/dev/token');
  const tokenInput = page.getByLabel('测试令牌');
  await tokenInput.fill(token);
  await page.getByRole('button', { name: '登录' }).click();
  await tokenInput.fill('').catch(() => undefined);
  await expect(page).toHaveURL(/\/workspace$/);
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

async function writeResult(result: Record<string, unknown>) {
  const path = requiredEnv('ADMIN_WEB_RESULT_FILE');
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, `${JSON.stringify(result, null, 2)}\n`, { mode: 0o600 });
}

function networkRecord(response: Response): NetworkRecord {
  const url = new URL(response.url());
  return {
    method: response.request().method(),
    status: response.status(),
    url: `${url.origin}${url.pathname}${url.search}`,
  };
}

function surveyIdFrom(url: string) {
  const match = new URL(url).pathname.match(/^\/surveys\/([0-9a-f-]{36})\/edit$/i);
  if (!match) throw new Error('Survey id is missing from the editor URL');
  return match[1];
}

function requiredEnv(name: string) {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required`);
  return value;
}
