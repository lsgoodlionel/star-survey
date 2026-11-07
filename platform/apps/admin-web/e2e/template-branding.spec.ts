import { expect, test, type Page } from '@playwright/test';
import { mkdtemp, readFile, rm, stat, unlink, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { deflateSync, inflateRawSync } from 'node:zlib';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { redactSensitiveInputs } from './redaction';
import { fieldDictionarySchema, responsePageSchema } from '../src/shared/api/responses';

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
let exportReaderDirectory = '';
let exportReaderPython = '';
const exportEvidenceRoot = join(process.cwd(), '../../tests/e2e');

test.describe.configure({ mode: 'serial' });
test.setTimeout(240_000);

test.beforeAll(async () => {
  test.setTimeout(660_000);
  const run = promisify(execFile);
  let readerSetupStage = 'environment';
  try {
    exportReaderDirectory = await mkdtemp(join(env('ADMIN_WEB_TEST_RESULTS_DIR'), 'export-readers-'));
    exportReaderPython = join(exportReaderDirectory, 'bin/python');
    await run('python3', ['-m', 'venv', exportReaderDirectory], { timeout: 30_000 });
    readerSetupStage = 'dependencies';
    await run(exportReaderPython, ['-m', 'pip', 'install', '--disable-pip-version-check', '--only-binary=:all:',
      '--index-url', 'https://pypi.org/simple', '-r', join(exportEvidenceRoot, 'export-evidence-requirements.txt')],
    { timeout: 600_000, maxBuffer: 1024 * 1024 });
    readerSetupStage = 'regressions';
    await run(exportReaderPython, ['-m', 'unittest', 'discover', '-s', exportEvidenceRoot,
      '-p', 'test_export_content_evidence.py', '-q'], { timeout: 30_000 });
    process.stdout.write('[export-content] independent-reader-regressions:ok\n');
  } catch (error) {
    if (readerSetupStage === 'regressions' && typeof error === 'object' && error !== null
        && 'stderr' in error && typeof error.stderr === 'string') {
      const failed = [...error.stderr.matchAll(/^(?:FAIL|ERROR): (test_[a-z0-9_]+)/gm)]
        .map((match) => match[1]).slice(0, 30);
      process.stdout.write(`[export-content] reader-regressions:failed tests=${[...new Set(failed)].join(',') || 'unknown'}\n`);
    }
    throw new Error(`Independent export reader ${readerSetupStage} failed`);
  }
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
  try {
    if (required.every((scenario) => scenarios.has(scenario))) {
      await writeFile(join(env('ADMIN_WEB_TEST_RESULTS_DIR'), 'wave2-scenarios.json'),
        `${JSON.stringify({ schemaVersion: 1, scenarios: [...scenarios].sort() })}\n`, { mode: 0o600 });
    }
  } finally {
    if (exportReaderDirectory) await rm(exportReaderDirectory, { recursive: true, force: true });
  }
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
  await page.getByRole('link', { name: '继续设置品牌与语言' })
    .and(page.locator(`a[href="/surveys/${copiedSurveyId}/branding"]`)).click();
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
  const firstAnswer = fresh.locator('input[type="radio"]:visible').first();
  expect(await firstAnswer.getAttribute('value')).toBe('A1');
  await firstAnswer.check();
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
  version2.setDefaultTimeout(30_000);
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
  await switchToEnglish(version2);
  await expectEnglishQuestion(version2);
  const secondAnswer = version2.locator('input[type="radio"]:visible').nth(1);
  expect(await secondAnswer.getAttribute('value')).toBe('A2');
  await secondAnswer.check();
  for (let step = 0; step < 4; step += 1) {
    const submit = version2.locator('#ls-button-submit:visible, #ls-button-next:visible, button[type="submit"]:visible, input[type="submit"]:visible').last();
    if (await submit.count() === 0) break;
    await submit.click();
    await version2.waitForLoadState('domcontentloaded');
    if (await version2.locator('input[type="radio"]:visible').count() === 0) break;
  }
  await expect.poll(async () => {
    const response = await page.request.get(`${env('ADMIN_WEB_BASE_URL')}/v1/surveys/${copiedSurveyId}/responses/summary`,
      { headers: { Authorization: `Bearer ${token}` } });
    expect(response.status()).toBe(200);
    return (await response.json() as { total: { engineCompleted: number } }).total.engineCompleted;
  }, { timeout: 60_000 }).toBe(2);
  await page.getByRole('navigation', { name: '问卷工作流' }).getByRole('link', { name: '答卷与导出' }).click();
  await expect(page.getByLabel('已完成 2')).toBeVisible();
  await expect(page.locator('.response-table-wrap tbody tr')).toHaveCount(2);
  const versionFilter = page.getByRole('combobox', { name: '发布版本', exact: true });
  await versionFilter.selectOption('1');
  await expect(page.locator('.response-table-wrap tbody tr')).toHaveCount(1);
  await expect(page.locator('.response-table-wrap tbody')).toContainText('版本 1');
  await versionFilter.selectOption('2');
  await expect(page.locator('.response-table-wrap tbody tr')).toHaveCount(1);
  await expect(page.locator('.response-table-wrap tbody')).toContainText('版本 2');
  await versionFilter.selectOption('');
  await expect(page.locator('.response-table-wrap tbody tr')).toHaveCount(2);
  const exportExpected = await syntheticExportExpectation(page, copiedSurveyId, token);
  for (const [format, label, suffix] of [
    ['csv', 'CSV 数据包', '.zip'], ['xlsx', 'Excel 工作簿', '.xlsx'],
    ['sav', 'SPSS 数据包', '.sav.zip'], ['docx', 'Word 答卷文档', '.docx'],
    ['pdf', 'PDF 答卷文档', '.pdf'], ['report-docx', 'Word 统计报告', '.report.docx'],
    ['attachments', '附件包', '.zip'],
  ]) {
    await page.getByLabel('导出格式').selectOption(format);
    const creation = page.waitForResponse((response) => response.url().endsWith(`/v1/surveys/${copiedSurveyId}/exports`)
      && response.request().method() === 'POST');
    await page.getByRole('button', { name: '创建导出任务' }).click();
    expect((await creation).status()).toBe(202);
    await expect(page.locator('.export-job')).toContainText(label);
    await expect(page.locator('.export-job')).toContainText('导出完成', { timeout: 60_000 });
    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('button', { name: '下载导出文件' }).click();
    const file = await downloadPromise;
    expect(file.suggestedFilename()).toMatch(new RegExp(`${suffix.replaceAll('.', '\\.')}$`));
    const filePath = await file.path();
    expect((await stat(filePath)).size).toBeGreaterThan(0);
    if (['xlsx', 'sav', 'docx'].includes(format)) await verifyDownloadedExport(format, filePath, exportExpected);
    if (format === 'pdf') {
      // Independent Poppler reader checks extractable Chinese from the actual downloaded file.
      const { stdout } = await promisify(execFile)('pdftotext', ['-enc', 'UTF-8', filePath, '-'], { timeout: 15_000 });
      for (const label of ['答卷信息', '版本', '答卷号', '作答状态']) expect(stdout.includes(label)).toBe(true);
    }
    if (format === 'report-docx') {
      const entries = readZipEntries(await readFile(filePath));
      const xml = entries.get('word/document.xml')?.toString('utf8') ?? '';
      // Check label and value in the same table row, not merely anywhere in the document.
      const totalRow = xml.match(/<w:tr>[^]*?<\/w:tr>/g)?.find((row) => row.includes('答卷总数')) ?? '';
      expect(/<w:t(?: [^>]*)?>2<\/w:t>/.test(totalRow)).toBe(true);
    }
  }

  await page.getByLabel('导出格式').selectOption('docx');
  const wordTemplatePanel = page.locator('.word-template-panel');
  await expect(wordTemplatePanel.getByRole('heading', { name: '自定义 Word 模板' })).toBeVisible();
  await wordTemplatePanel.getByLabel('模板名称').fill('浏览器验收自定义模板');
  await wordTemplatePanel.getByLabel('Word 模板文件').setInputFiles({
    name: 'invalid-secret.docx',
    mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    buffer: minimalDocx('E2E {{tenant.secret}}'),
  });
  const rejectedTemplate = page.waitForResponse((response) => response.request().method() === 'POST'
    && response.url().endsWith(`/v1/surveys/${copiedSurveyId}/word-templates`));
  await wordTemplatePanel.getByRole('button', { name: '上传 Word 模板' }).click();
  expect((await rejectedTemplate).status()).toBe(400);
  await expect(wordTemplatePanel.getByRole('alert')).toContainText('操作失败，请稍后重试');
  await expect(wordTemplatePanel.getByRole('alert')).not.toContainText('tenant.secret');

  await wordTemplatePanel.getByLabel('Word 模板文件').setInputFiles({
    name: 'wave3-custom.docx',
    mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    buffer: minimalDocx('E2E|{{survey.title}}|ID={{response.id}}|VERSION={{response.version}}|ANSWERS={{response.answers}}|END'),
  });
  const uploadedTemplate = page.waitForResponse((response) => response.request().method() === 'POST'
    && response.url().endsWith(`/v1/surveys/${copiedSurveyId}/word-templates`));
  await wordTemplatePanel.getByRole('button', { name: '上传 Word 模板' }).click();
  const uploadResponse = await uploadedTemplate;
  expect(uploadResponse.status()).toBe(201);
  const template = await uploadResponse.json() as {
    id: string; name: string; version: number; sha256: string;
  };
  expect(template.id).toMatch(uuid);
  expect(template.name).toBe('浏览器验收自定义模板');
  expect(template.version).toBe(1);
  expect(template.sha256).toMatch(/^[a-f0-9]{64}$/);
  await expect(wordTemplatePanel.getByText('模板已上传，可用于本次导出。')).toBeVisible();
  const customTemplateVersion = `custom:${template.id}:1`;
  const wordTemplateSelect = page.getByRole('combobox').filter({
    has: page.getByRole('option', { name: '平台默认模板' }),
  });
  await expect(wordTemplateSelect).toHaveValue(customTemplateVersion);

  const customCreation = page.waitForResponse((response) => response.url()
    .endsWith(`/v1/surveys/${copiedSurveyId}/exports`) && response.request().method() === 'POST');
  await page.getByRole('button', { name: '创建导出任务' }).click();
  const customCreationResponse = await customCreation;
  expect(customCreationResponse.status()).toBe(202);
  expect(customCreationResponse.request().postDataJSON()).toMatchObject({
    format: 'docx', templateVersion: customTemplateVersion,
  });
  await expect(page.locator('.export-job')).toContainText('导出完成', { timeout: 60_000 });
  const customDownload = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载导出文件' }).click();
  const customFile = await customDownload;
  expect(customFile.suggestedFilename()).toMatch(/\.docx\.zip$/);
  const outer = readZipEntries(await readFile((await customFile.path())!));
  expect([...outer.keys()]).toHaveLength(2);
  for (const [filename, documentBytes] of outer) {
    const identity = /^response-\d+-v(\d+)-r(\d+)\.docx$/.exec(filename);
    expect(identity).not.toBeNull();
    const document = readZipEntries(documentBytes).get('word/document.xml');
    expect(document).toBeDefined();
    const rendered = xmlText(document!.toString('utf8'));
    expect(rendered).toContain('E2E|品牌化模板副本');
    expect(rendered).toContain(`|ID=${identity![2]}|VERSION=${identity![1]}|ANSWERS=`);
    const answers = rendered.match(/\|ANSWERS=([\s\S]+)\|END$/)?.[1] ?? '';
    expect(answers.trim().length).toBeGreaterThan(0);
    expect(rendered).not.toContain('{{');
    expect(rendered).not.toContain('tenant.secret');
    expect(rendered).not.toContain('response.unknown');
  }
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
    await expect(page).toHaveURL(tokenName === 'ADMIN_WEB_OPERATOR_JWT_FILE' ? /\/operations\/tenants$/ : /\/dashboard$/);
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

async function syntheticExportExpectation(page: Page, surveyId: string, token: string) {
  const base = env('ADMIN_WEB_BASE_URL');
  const headers = { Authorization: `Bearer ${token}` };
  const [response, dictionary] = await Promise.all([
    page.request.get(`${base}/v1/surveys/${surveyId}/responses?limit=50`, { headers }),
    page.request.get(`${base}/v1/surveys/${surveyId}/fields`, { headers }),
  ]);
  if (!response.ok() || !dictionary.ok()) throw new Error('Synthetic export oracle unavailable');
  const pageData = responsePageSchema.parse(await response.json());
  const definition = fieldDictionarySchema.parse(await dictionary.json());
  const sources = [...definition.versions].sort((a, b) => a.version - b.version);
  const records = [...pageData.items].sort((a, b) => a.version - b.version || a.responseId - b.responseId);
  if (pageData.nextCursor !== null || records.length !== 2 || sources.length !== 2
      || sources[0].version !== 1 || sources[1].version !== 2
      || records[0].version !== 1 || records[1].version !== 2) {
    throw new Error('Synthetic export oracle must contain exactly both versions');
  }
  const labels: Record<string, string> = {};
  const fields = sources.flatMap((source) => {
    const seen = new Set<string>();
    return source.fields.map((field) => {
      if (!/^[A-Za-z][A-Za-z0-9_]*$/.test(field.code) || field.aid !== '' || field.scale !== 0
          || field.sensitive || seen.has(field.code)) throw new Error('Unsupported synthetic export field');
      seen.add(field.code);
      labels[field.code] ??= field.label;
      return { version: source.version, sid: source.engineSid, column: field.code,
        fieldname: field.fieldname, questioncode: field.code, type: field.type,
        aid: field.aid, scale: field.scale, label: field.label, sensitive: false, masking: '',
        options: field.options.map((option) => `${option.code}=${option.text}`).join('; ') };
    });
  });
  const rows = records.map((record) => {
    const source = sources.find((item) => item.version === record.version)!;
    const question = source.fields.find((field) => field.code === 'Q1');
    const knownAnswer = record.version === 1 ? 'A1' : 'A2';
    if (record.engineSid !== source.engineSid || record.state !== 'engine_completed'
        || record.answersStatus !== 'available' || !question || record.answers?.[question.fieldname] !== knownAnswer) {
      throw new Error('Synthetic submitted answer does not match the oracle');
    }
    const answers = Object.fromEntries(Object.keys(labels).map((code) => {
      const field = source.fields.find((item) => item.code === code);
      return [code, field ? record.answers?.[field.fieldname] ?? '' : ''];
    }));
    return { version: record.version, sid: record.engineSid, responseid: record.responseId,
      generation: record.generation, state: record.state, answersstatus: record.answersStatus, answers };
  });
  return { rows, fields, labels };
}

async function verifyDownloadedExport(format: string, download: string, expected: Awaited<ReturnType<typeof syntheticExportExpectation>>) {
  const oracle = join(exportReaderDirectory, 'expected.json');
  await writeFile(oracle, JSON.stringify(expected), { mode: 0o600, flag: 'wx' });
  try {
    const { stdout } = await promisify(execFile)(exportReaderPython,
      [join(exportEvidenceRoot, 'export_content_evidence.py'), format, download, oracle],
      { timeout: 30_000, maxBuffer: 16_384 });
    const result = JSON.parse(stdout) as { ok?: boolean; format?: string; rows?: number; fields?: number };
    if (result.ok !== true || result.format !== format || result.rows !== expected.rows.length
        || result.fields !== (format === 'docx' ? 0 : expected.fields.length)) throw new Error('Invalid reader success evidence');
    process.stdout.write(`[export-content] ${format}:ok rows=${result.rows} fields=${result.fields}\n`);
  } catch (error) {
    let category = 'reader_failure';
    const allowed = new Set(['arguments', 'expectation', 'archive', 'structure', 'columns',
      'rows', 'fields', 'mapping', 'content', 'dependency', 'reader']);
    if (typeof error === 'object' && error !== null && 'stdout' in error
        && typeof error.stdout === 'string' && error.stdout.length <= 16_384) {
      try {
        const result = JSON.parse(error.stdout) as { ok?: boolean; category?: string };
        if (result.ok === false && result.category && allowed.has(result.category)) category = result.category;
      } catch { /* Raw reader messages and paths are never emitted. */ }
    }
    throw new Error(`Downloaded export content verification failed (${format}): ${category}`);
  } finally {
    await unlink(oracle);
  }
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

function minimalDocx(text: string) {
  return createZip(new Map([
    ['[Content_Types].xml', Buffer.from('<?xml version="1.0" encoding="UTF-8"?>'
      + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
      + '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
      + '<Default Extension="xml" ContentType="application/xml"/>'
      + '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
      + '</Types>')],
    ['_rels/.rels', Buffer.from('<?xml version="1.0" encoding="UTF-8"?>'
      + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
      + '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
      + '</Relationships>')],
    ['word/document.xml', Buffer.from('<?xml version="1.0" encoding="UTF-8"?>'
      + '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
      + `<w:body><w:p><w:r><w:t>${text}</w:t></w:r></w:p></w:body></w:document>`)],
  ]));
}

function createZip(entries: Map<string, Buffer>) {
  const local: Buffer[] = [];
  const central: Buffer[] = [];
  let offset = 0;
  for (const [name, data] of entries) {
    const filename = Buffer.from(name, 'utf8');
    const checksum = crc32(data);
    const header = Buffer.alloc(30);
    header.writeUInt32LE(0x04034b50, 0);
    header.writeUInt16LE(20, 4);
    header.writeUInt16LE(0x0800, 6);
    header.writeUInt16LE(0, 8);
    header.writeUInt32LE(checksum, 14);
    header.writeUInt32LE(data.length, 18);
    header.writeUInt32LE(data.length, 22);
    header.writeUInt16LE(filename.length, 26);
    local.push(header, filename, data);

    const directory = Buffer.alloc(46);
    directory.writeUInt32LE(0x02014b50, 0);
    directory.writeUInt16LE(20, 4);
    directory.writeUInt16LE(20, 6);
    directory.writeUInt16LE(0x0800, 8);
    directory.writeUInt16LE(0, 10);
    directory.writeUInt32LE(checksum, 16);
    directory.writeUInt32LE(data.length, 20);
    directory.writeUInt32LE(data.length, 24);
    directory.writeUInt16LE(filename.length, 28);
    directory.writeUInt32LE(offset, 42);
    central.push(directory, filename);
    offset += header.length + filename.length + data.length;
  }
  const centralBytes = Buffer.concat(central);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(entries.size, 8);
  end.writeUInt16LE(entries.size, 10);
  end.writeUInt32LE(centralBytes.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...local, centralBytes, end]);
}

function readZipEntries(zip: Buffer) {
  let end = zip.length - 22;
  while (end >= 0 && zip.readUInt32LE(end) !== 0x06054b50) end -= 1;
  if (end < 0) throw new Error('ZIP end directory is missing');
  const count = zip.readUInt16LE(end + 10);
  let cursor = zip.readUInt32LE(end + 16);
  const entries = new Map<string, Buffer>();
  for (let index = 0; index < count; index += 1) {
    if (zip.readUInt32LE(cursor) !== 0x02014b50) throw new Error('ZIP central directory is invalid');
    const method = zip.readUInt16LE(cursor + 10);
    const compressedSize = zip.readUInt32LE(cursor + 20);
    const filenameLength = zip.readUInt16LE(cursor + 28);
    const extraLength = zip.readUInt16LE(cursor + 30);
    const commentLength = zip.readUInt16LE(cursor + 32);
    const localOffset = zip.readUInt32LE(cursor + 42);
    const name = zip.subarray(cursor + 46, cursor + 46 + filenameLength).toString('utf8');
    if (zip.readUInt32LE(localOffset) !== 0x04034b50) throw new Error('ZIP local entry is invalid');
    const localNameLength = zip.readUInt16LE(localOffset + 26);
    const localExtraLength = zip.readUInt16LE(localOffset + 28);
    const start = localOffset + 30 + localNameLength + localExtraLength;
    const compressed = zip.subarray(start, start + compressedSize);
    const value = method === 0 ? compressed : method === 8 ? inflateRawSync(compressed)
      : (() => { throw new Error(`unsupported ZIP method ${method}`); })();
    entries.set(name, value);
    cursor += 46 + filenameLength + extraLength + commentLength;
  }
  return entries;
}

function xmlText(xml: string) {
  return xml.replace(/<[^>]+>/g, '')
    .replaceAll('&lt;', '<').replaceAll('&gt;', '>').replaceAll('&quot;', '"')
    .replaceAll('&apos;', "'").replaceAll('&amp;', '&');
}

function crc32(data: Buffer) {
  let crc = 0xffffffff;
  for (const byte of data) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}
