import { expect, test, type Page } from '@playwright/test';
import { readFile, stat, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { deflateSync } from 'node:zlib';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { redactSensitiveInputs } from './redaction';

const scenarios = new Set<string>();
const required = [
  'tenant-template-lifecycle', 'platform-template-copy', 'brand-save-refresh',
  'preview-branding', 'respondent-language-switch', 'no-third-party-url',
  'asset-version-pinning', 'version-conflict', 'forbidden-actions',
  'keyboard-access', 'responsive-widths',
];
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
let copiedSurveyId = '';
let templateId = '';
let platformTemplateId = '';

test.describe.configure({ mode: 'serial' });
test.setTimeout(240_000);

test.beforeAll(async () => {
  const run = promisify(execFile);
  const gate = join(process.cwd(), '../../tests/e2e/admin_web_gate.py');
  for (const [actor, file] of [
    ['admin-web-e2e-owner', 'ADMIN_WEB_JWT_FILE'],
    ['admin-web-e2e-operator', 'ADMIN_WEB_OPERATOR_JWT_FILE'],
  ]) {
    await run('python3', [gate, '--base-url', env('ADMIN_WEB_BASE_URL'), 'issue-browser-token',
      '--metadata-file', env('ADMIN_WEB_METADATA_FILE'), '--jwt-file', env(file), '--actor-id', actor],
    { timeout: 10_000 });
  }
});

test.afterAll(async () => {
  if (required.some((scenario) => !scenarios.has(scenario))) return;
  await writeFile(join(env('ADMIN_WEB_TEST_RESULTS_DIR'), 'wave2-scenarios.json'),
    `${JSON.stringify({ schemaVersion: 1, scenarios: [...scenarios].sort() })}\n`, { mode: 0o600 });
});

test('tenant template lifecycle copies a real survey and preserves brand draft context', async ({ page }) => {
  const result = await readResult();
  await login(page, 'ADMIN_WEB_JWT_FILE');
  await navigate(page, `/surveys/${result.surveyId}/edit`);
  await page.getByRole('button', { name: '保存为模板' }).click();
  await page.getByRole('textbox', { name: '模板名称' }).fill('浏览器验收租户模板');
  await page.getByRole('button', { name: '创建模板' }).click();
  const detail = page.getByRole('link', { name: '查看新模板' });
  await expect(detail).toBeVisible();
  templateId = new URL((await detail.getAttribute('href'))!, 'http://local').pathname.split('/').at(-1)!;
  expect(templateId).toMatch(uuid);
  await detail.click();
  await expect(page.getByRole('heading', { name: '浏览器验收租户模板' })).toBeVisible();
  await page.getByRole('button', { name: '提交审核' }).click();
  await expect(page.getByText('待审核', { exact: true }).first()).toBeVisible();
  await page.getByRole('button', { name: '批准' }).click();
  await expect(page.getByText('已发布', { exact: true }).first()).toBeVisible();
  scenarios.add('tenant-template-lifecycle');

  await page.getByRole('combobox', { name: '目标项目或文件夹' }).selectOption(result.rootProjectId);
  await page.getByRole('textbox', { name: '新问卷标题' }).fill('品牌化模板副本');
  await page.getByRole('button', { name: '从模板创建问卷' }).click();
  await expect(page).toHaveURL(/\/surveys\/[0-9a-f-]+\/edit\?from=/);
  copiedSurveyId = new URL(page.url()).pathname.split('/')[2];
  expect(copiedSurveyId).toMatch(uuid);
  await page.getByRole('navigation', { name: '问卷工作流' })
    .getByRole('link', { name: '品牌与语言' }).click();
  await expect(page.getByRole('heading', { name: '品牌与语言' })).toBeVisible();
  await page.getByRole('textbox', { name: '主色', exact: true }).fill('#1464A0');
  await page.getByRole('textbox', { name: '页面标题' }).fill('品牌问卷');
  await page.getByRole('textbox', { name: '页脚文本' }).fill('感谢参与');
  await page.getByRole('textbox', { name: '页面水印' }).fill('内部资料');
  await page.getByRole('textbox', { name: '新增语言代码' }).fill('en');
  await page.getByRole('button', { name: '添加语言' }).click();
  await page.getByRole('textbox', { name: 'en 问卷标题' }).fill('Branded survey');
  await page.getByRole('textbox', { name: 'en 问卷说明' }).fill('English brand introduction');
  await page.getByRole('textbox', { name: 'en 题目 Q1 文本', exact: true }).fill('Which option describes you?');
  await page.getByRole('button', { name: '保存品牌与语言' }).click();
  await expect(page.getByText(/已保存，草稿版本/)).toBeVisible();
  await page.getByRole('link', { name: '工作台', exact: true }).click();
  await page.getByRole('link', { name: '继续设置品牌与语言' }).click();
  await expect(page.getByRole('textbox', { name: '主色', exact: true })).toHaveValue('#1464A0');
  await expect(page.getByRole('textbox', { name: 'en 问卷标题' })).toHaveValue('Branded survey');
  scenarios.add('brand-save-refresh');

  const token = await readPrivateToken('ADMIN_WEB_JWT_FILE');
  const base = env('ADMIN_WEB_BASE_URL');
  const draftResponse = await page.request.get(`${base}/v1/surveys/${copiedSurveyId}/draft`,
    { headers: { Authorization: `Bearer ${token}` } });
  expect(draftResponse.status()).toBe(200);
  const current = await draftResponse.json() as { version: number; definition: unknown };
  await page.getByRole('textbox', { name: '页脚文本' }).fill('本地未保存页脚');
  const external = await page.request.put(`${base}/v1/surveys/${copiedSurveyId}/draft`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { expectedVersion: current.version, definition: current.definition },
  });
  expect(external.status()).toBe(200);
  await page.getByRole('button', { name: '保存品牌与语言' }).click();
  await expect(page.getByText('草稿版本已变化，请重新加载后再保存')).toBeVisible();
  await expect(page.getByRole('textbox', { name: '页脚文本' })).toHaveValue('本地未保存页脚');
  scenarios.add('version-conflict');
});

test('platform operator imports a selected version; tenant can copy without managing it', async ({ browser, page }) => {
  const result = await readResult();
  const token = await readPrivateToken('ADMIN_WEB_JWT_FILE');
  const draft = await page.request.get(`${env('ADMIN_WEB_BASE_URL')}/v1/surveys/${result.surveyId}/draft`,
    { headers: { Authorization: `Bearer ${token}` } });
  expect(draft.status()).toBe(200);
  const definition = (await draft.json() as { definition: unknown }).definition;
  const operator = await browser.newPage();
  try {
    await login(operator, 'ADMIN_WEB_OPERATOR_JWT_FILE');
    await operator.getByRole('link', { name: '模板中心' }).click();
    await operator.getByRole('link', { name: '平台模板管理' }).click();
    await operator.getByRole('textbox', { name: '平台模板名称' }).fill('浏览器验收平台模板');
    await operator.getByLabel('定义 JSON 文件').setInputFiles({
      name: 'platform-template.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(definition)),
    });
    await operator.getByRole('button', { name: '导入平台模板' }).click();
    const link = operator.getByRole('link', { name: '查看平台模板' });
    await expect(link).toBeVisible();
    platformTemplateId = new URL((await link.getAttribute('href'))!, 'http://local').pathname.split('/').at(-1)!;
    expect(platformTemplateId).toMatch(uuid);
    await link.click();
    await operator.getByRole('combobox', { name: '上架版本' }).selectOption('1');
    await operator.getByRole('button', { name: '上架所选版本' }).click();
    await operator.getByRole('button', { name: '确认上架版本 1' }).click();
    await expect(operator.getByText('平台模板已上架')).toBeVisible();
  } finally {
    await operator.context().close();
  }
  await login(page, 'ADMIN_WEB_JWT_FILE');
  await page.getByRole('link', { name: '模板中心' }).click();
  await expect(page.getByRole('link', { name: '平台模板管理' })).toHaveCount(0);
  await navigate(page, `/templates/${platformTemplateId}`);
  await expect(page.getByRole('heading', { name: '浏览器验收平台模板' })).toBeVisible();
  await expect(page.getByRole('button', { name: '下架平台模板' })).toHaveCount(0);
  await page.getByRole('combobox', { name: '目标项目或文件夹' }).selectOption(result.rootProjectId);
  await page.getByRole('button', { name: '从模板创建问卷' }).click();
  await expect(page).toHaveURL(/\/surveys\/[0-9a-f-]+\/edit\?from=/);
  scenarios.add('platform-template-copy');
  scenarios.add('forbidden-actions');
});

test('branded copy previews and publishes with language and pinned image bytes', async ({ page }) => {
  test.setTimeout(600_000);
  page.setDefaultTimeout(30_000);
  expect(copiedSurveyId).toMatch(uuid);
  const token = await readPrivateToken('ADMIN_WEB_JWT_FILE');
  const thirdPartyOrigins = new Set<string>();
  page.context().on('request', (request) => {
    const url = new URL(request.url());
    if (/^https?:$/.test(url.protocol) && url.origin !== new URL(env('ADMIN_WEB_BASE_URL')).origin) {
      thirdPartyOrigins.add(url.origin);
    }
  });
  await login(page, 'ADMIN_WEB_JWT_FILE');
  await navigate(page, `/surveys/${copiedSurveyId}/branding`);
  await expect(page.getByRole('heading', { name: '品牌与语言' })).toBeVisible();
  const logoV1 = png([190, 42, 50]);
  const logoV2 = png([42, 70, 190]);
  await page.getByLabel('上传Logo').setInputFiles({ name: 'brand.png', mimeType: 'image/png', buffer: logoV1 });
  const logoOption = page.getByRole('combobox', { name: 'Logo' }).getByRole('option', { name: 'brand.png' });
  await expect(logoOption).toHaveCount(1);
  const logoId = await logoOption.getAttribute('value');
  expect(logoId).toMatch(uuid);
  await expect(page.getByRole('combobox', { name: 'Logo' })).toHaveValue(logoId!);
  await page.getByLabel('上传网站图标').setInputFiles({ name: 'favicon.png', mimeType: 'image/png', buffer: logoV1 });
  await expect(page.getByRole('combobox', { name: '网站图标', exact: true })
    .getByRole('option', { name: 'favicon.png' })).toHaveCount(1);
  await page.getByLabel('上传问卷封面').setInputFiles({ name: 'cover.png', mimeType: 'image/png', buffer: png([30, 110, 80]) });
  await expect(page.getByRole('combobox', { name: '问卷封面', exact: true })
    .getByRole('option', { name: 'cover.png' })).toHaveCount(1);
  await page.getByLabel('上传页面背景').setInputFiles({ name: 'background.png', mimeType: 'image/png', buffer: png([245, 245, 240]) });
  await expect(page.getByRole('combobox', { name: '页面背景', exact: true })
    .getByRole('option', { name: 'background.png' })).toHaveCount(1);
  await page.getByRole('textbox', { name: 'Logo 替代文本' }).fill('验收Logo');
  await page.getByRole('button', { name: '保存品牌与语言' }).click();
  await expect(page.getByText(/已保存，草稿版本/)).toBeVisible();

  await page.getByRole('navigation', { name: '问卷工作流' })
    .getByRole('link', { name: '快速预览' }).click();
  const previewResponse = page.waitForResponse((response) => response.request().method() === 'POST'
    && response.url().endsWith(`/v1/surveys/${copiedSurveyId}/preview-sessions`));
  await page.getByRole('button', { name: '创建真实预览' }).click();
  const creation = await previewResponse;
  if (creation.status() >= 400) {
    const payload = await creation.json() as { error?: string };
    throw new Error(`preview HTTP ${creation.status()}: ${payload.error ?? 'unknown_error'}`);
  }
  await expect(page.getByText('真实预览已就绪')).toBeVisible({ timeout: 210_000 });
  const previewPromise = page.context().waitForEvent('page');
  await page.getByRole('button', { name: '在新窗口打开真实预览' }).click();
  const preview = await previewPromise;
  await expect(preview).toHaveTitle(/品牌问卷/);
  await expect(preview.getByText('感谢参与')).toBeVisible();
  await expect(preview.locator('#mjy-brand-cover img')).toBeVisible();
  await expect(preview.getByText('内部资料')).toBeVisible();
  await expect(preview.locator('#mjy-brand-background')).toHaveCount(1);
  scenarios.add('preview-branding');
  await assertNoThirdPartyUrls(preview);
  scenarios.add('no-third-party-url');
  await switchToEnglish(preview);
  await expectEnglishQuestion(preview);
  scenarios.add('respondent-language-switch');
  await preview.close();
  await page.getByRole('button', { name: '结束真实预览' }).click();
  await expect(page.getByText('真实预览已结束')).toBeVisible({ timeout: 210_000 });

  await page.getByRole('navigation', { name: '问卷工作流' })
    .getByRole('link', { name: '发布与版本' }).click();
  await page.getByRole('button', { name: '提交审批' }).click();
  await page.getByRole('button', { name: '批准申请' }).click();
  const publishV1 = page.waitForResponse((response) => response.request().method() === 'POST'
    && response.url().endsWith(`/v1/surveys/${copiedSurveyId}/publish`), { timeout: 210_000 });
  await page.getByRole('button', { name: '发布问卷' }).click();
  expect((await publishV1).ok()).toBe(true);
  await expect(page.getByText('发布成功')).toBeVisible({ timeout: 210_000 });
  await page.getByLabel('链接名称').fill('品牌验收链接');
  await page.getByRole('button', { name: '创建链接' }).click();
  const delivery = page.getByRole('list', { name: '投放链接' }).getByRole('link').first();
  await expect(delivery).toBeVisible();
  const deliveryUrl = await delivery.getAttribute('href');
  expect(deliveryUrl).toMatch(/^http:\/\/127\.0\.0\.1:/);
  const respondent = await page.context().newPage();
  await respondent.goto(deliveryUrl!);
  const logoImage = respondent.getByRole('img', { name: '验收Logo' });
  await expect(logoImage).toBeVisible();
  await expect(respondent.locator('#mjy-brand-cover img')).toBeVisible();
  await expect(respondent.getByText('内部资料')).toBeVisible();
  await expect(respondent.locator('#mjy-brand-background')).toHaveCount(1);
  const imageUrl = new URL((await logoImage.getAttribute('src'))!, respondent.url()).href;
  const originalImage = await respondent.request.get(imageUrl);
  expect(originalImage.status()).toBe(200);
  const beforeHash = createHash('sha256').update(await originalImage.body()).digest('hex');
  expect(beforeHash).toBe(createHash('sha256').update(logoV1).digest('hex'));

  const update = await page.request.post(`${env('ADMIN_WEB_BASE_URL')}/v1/assets/${logoId}/versions`, {
    headers: { Authorization: `Bearer ${token}` },
    multipart: { file: { name: 'brand.png', mimeType: 'image/png', buffer: logoV2 } },
  });
  expect(update.status()).toBe(200);
  expect((await update.json() as { currentVersion: number }).currentVersion).toBe(2);
  const fresh = await page.context().newPage();
  await fresh.goto(deliveryUrl!);
  const pinnedImage = await fresh.request.get(new URL((await fresh.getByRole('img', { name: '验收Logo' })
    .getAttribute('src'))!, fresh.url()).href, { headers: { 'Cache-Control': 'no-cache' } });
  expect(pinnedImage.status()).toBe(200);
  expect(createHash('sha256').update(await pinnedImage.body()).digest('hex')).toBe(beforeHash);
  await assertNoThirdPartyUrls(fresh);
  await switchToEnglish(fresh);
  await expectEnglishQuestion(fresh);
  await fresh.locator('input[type="radio"]:visible').first().check();
  for (let step = 0; step < 4; step += 1) {
    const submit = fresh.locator('#ls-button-submit:visible, #ls-button-next:visible, button[type="submit"]:visible, input[type="submit"]:visible').last();
    if (await submit.count() === 0) break;
    await submit.click();
    await fresh.waitForLoadState('domcontentloaded');
    if (await fresh.locator('input[type="radio"]:visible').count() === 0) break;
  }
  await expect.poll(async () => {
    const response = await page.request.get(`${env('ADMIN_WEB_BASE_URL')}/v1/surveys/${copiedSurveyId}/responses/summary`,
      { headers: { Authorization: `Bearer ${token}` } });
    expect(response.status()).toBe(200);
    return (await response.json() as { total: { engineCompleted: number } }).total.engineCompleted;
  }, { timeout: 60_000 }).toBe(1);
  await respondent.close(); await fresh.close();

  await page.getByRole('navigation', { name: '问卷工作流' }).getByRole('link', { name: '品牌与语言' }).click();
  await page.getByRole('textbox', { name: '页脚文本' }).fill('感谢参与第二版');
  await page.getByRole('button', { name: '保存品牌与语言' }).click();
  await expect(page.getByText(/已保存，草稿版本/)).toBeVisible();
  await page.getByRole('navigation', { name: '问卷工作流' }).getByRole('link', { name: '发布与版本' }).click();
  await page.getByRole('button', { name: '提交审批' }).click();
  await page.getByRole('button', { name: '批准申请' }).click();
  const publishV2 = page.waitForResponse((response) => response.request().method() === 'POST'
    && response.url().endsWith(`/v1/surveys/${copiedSurveyId}/publish`), { timeout: 210_000 });
  await page.getByRole('button', { name: '发布问卷' }).click();
  expect((await publishV2).ok()).toBe(true);
  await expect(page.getByText('发布成功')).toBeVisible({ timeout: 210_000 });
  const versions = await page.request.get(`${env('ADMIN_WEB_BASE_URL')}/v1/surveys/${copiedSurveyId}/versions`,
    { headers: { Authorization: `Bearer ${token}` } });
  expect(versions.status()).toBe(200);
  const published = await versions.json() as { version: number; live: boolean }[];
  expect(published.map((item) => item.version).sort()).toEqual([1, 2]);
  expect(published.find((item) => item.live)?.version).toBe(2);
  const version2 = await page.context().newPage();
  await version2.goto(deliveryUrl!);
  await expect(version2.getByText('感谢参与第二版')).toBeVisible();
  const version2Image = await version2.request.get(new URL((await version2.getByRole('img', { name: '验收Logo' })
    .getAttribute('src'))!, version2.url()).href);
  expect(version2Image.status()).toBe(200);
  expect(createHash('sha256').update(await version2Image.body()).digest('hex'))
    .toBe(createHash('sha256').update(logoV2).digest('hex'));
  const version1Image = await version2.request.get(imageUrl, { headers: { 'Cache-Control': 'no-cache' } });
  expect(version1Image.status()).toBe(200);
  expect(createHash('sha256').update(await version1Image.body()).digest('hex')).toBe(beforeHash);
  scenarios.add('asset-version-pinning');
  await assertNoThirdPartyUrls(version2);
  await version2.close();
  expect([...thirdPartyOrigins]).toEqual([]);
});

test('brand editor remains keyboard reachable and fits four product widths', async ({ page }) => {
  await login(page, 'ADMIN_WEB_JWT_FILE');
  await navigate(page, `/surveys/${copiedSurveyId}/branding`);
  await expect(page.getByRole('heading', { name: '品牌与语言' })).toBeVisible();
  await page.getByRole('textbox', { name: '主色', exact: true }).focus();
  await page.keyboard.press('Tab');
  const focused = await page.evaluate(() => document.activeElement?.getAttribute('aria-label'));
  expect(focused).toBe('主色选择器');
  scenarios.add('keyboard-access');
  for (const width of [819, 820, 1179, 1180]) {
    await page.setViewportSize({ width, height: 900 });
    const geometry = await page.evaluate(() => ({
      document: document.documentElement.scrollWidth,
      viewport: window.innerWidth,
      formRight: document.querySelector('.branding-page form')?.getBoundingClientRect().right ?? Infinity,
    }));
    expect(geometry.document).toBeLessThanOrEqual(geometry.viewport + 1);
    expect(geometry.formRight).toBeLessThanOrEqual(geometry.viewport + 1);
    await page.screenshot({ path: join(env('ADMIN_WEB_TEST_RESULTS_DIR'), `branding-${width}.png`), fullPage: true });
  }
  scenarios.add('responsive-widths');
});

async function login(page: Page, tokenName: string) {
  const token = await readPrivateToken(tokenName);
  await page.goto('/dev/token');
  try {
    await page.getByLabel('测试令牌').fill(token);
    await page.getByRole('button', { name: '登录' }).click();
    await expect(page).toHaveURL(/\/dashboard$/);
  } finally {
    if (!page.isClosed()) await redactSensitiveInputs(page);
  }
}

async function navigate(page: Page, path: string) {
  await page.evaluate((target) => {
    window.history.pushState({}, '', target);
    window.dispatchEvent(new PopStateEvent('popstate'));
  }, path);
}

async function readPrivateToken(name: string) {
  const path = env(name);
  if (((await stat(path)).mode & 0o777) !== 0o600) throw new Error(`${name} is not mode 0600`);
  return (await readFile(path, 'utf8')).trim();
}

async function readResult(): Promise<{ rootProjectId: string; surveyId: string }> {
  const result = JSON.parse(await readFile(env('ADMIN_WEB_RESULT_FILE'), 'utf8')) as
    { rootProjectId: string; surveyId: string };
  expect(result.rootProjectId).toMatch(uuid);
  expect(result.surveyId).toMatch(uuid);
  return result;
}

function env(name: string) {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required`);
  return value;
}

async function assertNoThirdPartyUrls(page: Page) {
  const external = await page.evaluate(() => {
    const urls = [...document.querySelectorAll<HTMLElement>('[src],[href]')].flatMap((element) =>
      [element.getAttribute('src'), element.getAttribute('href')].filter((value): value is string => Boolean(value)));
    const styles = [...document.querySelectorAll<HTMLElement>('*')].flatMap((element) =>
      [...getComputedStyle(element).cssText.matchAll(/url\(["']?([^"')]+)["']?\)/g)].map((match) => match[1]));
    return [...urls, ...styles].filter((url) => {
      if (!/^https?:\/\//i.test(url)) return false;
      return new URL(url).origin !== location.origin;
    });
  });
  expect(external).toEqual([]);
}

async function switchToEnglish(page: Page) {
  const url = new URL(page.url());
  url.searchParams.set('lang', 'en');
  await page.goto(url.href);
}

async function expectEnglishQuestion(page: Page) {
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  const translated = page.getByText('Which option describes you?', { exact: true });
  for (let step = 0; step < 4 && !await translated.isVisible(); step += 1) {
    const next = page.locator(
      '#ls-button-submit:visible, #ls-button-next:visible, button[type="submit"]:visible, input[type="submit"]:visible',
    ).last();
    await expect(next).toBeVisible();
    await next.click();
    await page.waitForLoadState('domcontentloaded');
  }
  await expect(translated).toBeVisible();
}

function png(rgb: [number, number, number]) {
  const signature = Buffer.from('89504e470d0a1a0a', 'hex');
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(1, 0); ihdr.writeUInt32BE(1, 4); ihdr[8] = 8; ihdr[9] = 2;
  const raw = Buffer.from([0, ...rgb]);
  return Buffer.concat([signature, chunk('IHDR', ihdr), chunk('IDAT', deflateSync(raw)), chunk('IEND', Buffer.alloc(0))]);
}

function chunk(type: string, data: Buffer) {
  const name = Buffer.from(type, 'ascii');
  const length = Buffer.alloc(4); length.writeUInt32BE(data.length);
  const body = Buffer.concat([name, data]);
  const checksum = Buffer.alloc(4);
  let crc = 0xffffffff;
  for (const byte of body) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
  }
  checksum.writeUInt32BE((crc ^ 0xffffffff) >>> 0);
  return Buffer.concat([length, body, checksum]);
}
