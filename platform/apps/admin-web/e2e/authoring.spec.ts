import { approvalJourney } from './approvalJourney';
import { expect, test, type Download, type Locator, type Page, type Response, type TestInfo } from '@playwright/test';
import { execFile } from 'node:child_process';
import { mkdir, readFile, stat, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import jsQR from 'jsqr';
import { assertArtifactContainsNoSecret, redactSecret } from './artifacts';
import { createResourceEndpoint } from './createResourceEvidence';
import { recentSanitizedNetworkEvents, trackSanitizedNetworkEvents } from './networkEvidence';
import { redactSensitiveInputs } from './redaction';
import { recordRunRootId } from './runRootEvidence';

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
  rootProjectId: string;
  folderId: string;
  surveyId: string;
  version: number;
}

interface OutlineGroupOrder {
  title: string;
  questions: string[];
}

interface PreviewIsolationSnapshot {
  publishedVersions: number;
  publishedBindings: number;
  officialRoutes: number;
  engineOutbox: number;
  responses: number;
}

interface CreatedDeliveryLink {
  id: string;
  url: string;
  shortUrl: string | null;
}

const ACTION_TIMEOUT_MS = 15_000;
const RUN_PROJECT_NAME = '浏览器验收项目';
const RUN_FOLDER_NAME = '产品验收资料';
const RUN_SURVEY_NAME = '管理端产品对齐验收问卷';
const SAVED_SURVEY_NAME = '管理端产品对齐验收问卷（已更新）';

const importText = [
  '1. 您的性别？[单选]',
  'A. 男',
  'B. 女',
  '这一行认不出来',
  '2. 单选却没有选项[单选]',
].join('\n');

test.beforeEach(async ({ page }) => {
  trackSanitizedNetworkEvents(page);
});

test.afterEach(async ({ page }, testInfo) => {
  if (testInfo.status === testInfo.expectedStatus) return;
  testInfo.setTimeout(testInfo.timeout + 7_000);
  await writeSanitizedTraceSummary(page, testInfo);
  if (page.isClosed()) return;
  try {
    await withTimeout(redactSensitiveInputs(page), 1_500);
    if (page.isClosed()) return;
    await page.screenshot({
      path: testInfo.outputPath('sanitized-failure.png'),
      fullPage: true,
      timeout: 2_000,
    });
  } catch {
    // A crashed or inaccessible page is safer without a screenshot than with an unredacted one.
  }
});

async function writeSanitizedTraceSummary(page: Page, testInfo: TestInfo) {
  const testId = testInfo.project.name === 'chromium-mobile'
    ? 'mobile-responsive-editor'
    : 'desktop-authoring';
  let lastPath = '/';
  if (!page.isClosed()) {
    try {
      const url = new URL(page.url());
      lastPath = url.protocol === 'http:' || url.protocol === 'https:' ? url.pathname : '/';
    } catch {
      lastPath = '/';
    }
  }
  const summary = {
    schemaVersion: 1,
    kind: 'sanitized-playwright-trace-summary',
    project: testInfo.project.name,
    testId,
    status: testInfo.status,
    durationMs: Math.max(0, Math.round(testInfo.duration)),
    lastPath,
    network: recentSanitizedNetworkEvents(page),
  };
  await writeFile(
    testInfo.outputPath('sanitized-trace-summary.json'),
    `${JSON.stringify(summary, null, 2)}\n`,
    { mode: 0o600 },
  );
}

test('author filters stable resources, restores archive, and publishes the same survey', async ({ page }, testInfo) => {
  if (process.env.ADMIN_WEB_RELAY_DIAGNOSTIC === '1') testInfo.setTimeout(360_000);
  const metadata = await readMetadata();
  const token = await readToken();
  const network: NetworkRecord[] = [];
  page.on('response', (response) => network.push(networkRecord(response, token)));

  const me = await login(page, token, metadata);

  await exerciseDeliveryWorkspace(page);

  await clickAction(page.getByRole('link', { name: '项目与问卷' }));
  await expect(page).toHaveURL(/\/workspace$/);
  await expect(page.getByText(/^E2E(?:项目|文件夹|问卷).*[0-9a-z]{6,}$/i)).toHaveCount(0);
  const { rootProjectId, folderId, surveyId } = await createSurveyResourceTree(page);

  await fillAction(page.getByLabel('题目文本'), '这是一份真实浏览器验收问卷');
  await fillAction(page.getByLabel('标题'), SAVED_SURVEY_NAME);
  const saveResponse = page.waitForResponse((response) =>
    response.url().includes(`/v1/surveys/${surveyId}/draft`) && response.request().method() === 'PUT',
  );
  await clickAction(page.getByRole('button', { name: '保存草稿' }));
  expect((await saveResponse).status()).toBe(200);
  await expect(page.getByText(/已保存版本 \d+/)).toBeVisible();
  await expect(page.getByRole('heading', { name: SAVED_SURVEY_NAME })).toBeVisible();
  await expect(page.getByRole('navigation', { name: '面包屑' })).toContainText(SAVED_SURVEY_NAME);

  await clickAction(page.getByRole('link', { name: '批量导入' }));
  await fillAction(page.getByLabel('待导入文本'), importText);
  await clickAction(page.getByRole('button', { name: '预览导入' }));
  await expect(page.getByText('第 4 行')).toBeVisible();
  const importPreview = page.getByRole('region', { name: '解析结果' });
  await expect(importPreview.getByText('您的性别？', { exact: true })).toBeVisible();
  await clickAction(page.getByRole('button', { name: '确认导入 1 道题' }));
  await expect(page).toHaveURL(new RegExp(`/surveys/${surveyId}/edit$`));

  await clickAction(page.getByRole('link', { name: '快速预览' }));
  await expect(page.getByRole('heading', { name: '快速预览' })).toBeVisible();
  await expect(page.getByText('您的性别？')).toBeVisible();
  await clickAction(page.getByRole('link', { name: '编辑' }));
  await expect(page).toHaveURL(new RegExp(`/surveys/${surveyId}/edit$`));
  await clickAction(page.getByRole('link', { name: '快速预览' }));
  await expect(page.getByRole('heading', { name: '快速预览' })).toBeVisible();

  const isolationBefore = await readPreviewIsolationSnapshot(surveyId, me.tenantId);
  const previewPagePromise = page.context().waitForEvent('page');
  const previewCreate = page.waitForResponse((response) =>
    response.url().includes(`/v1/surveys/${surveyId}/preview-sessions`)
      && response.request().method() === 'POST');
  await clickAction(page.getByRole('button', { name: '创建真实预览' }));
  const createdPreview: unknown = await (await previewCreate).json();
  if (typeof createdPreview !== 'object' || createdPreview === null || !('id' in createdPreview)
      || typeof createdPreview.id !== 'string') throw new Error('Preview session identity is invalid');
  assertCanonicalUuid(createdPreview.id);
  await expect(page.getByText('真实预览已就绪')).toBeVisible({ timeout: 210_000 });
  const previewIdentity = await readPreviewIdentity(me.tenantId, surveyId, createdPreview.id);
  const deliveredBefore = await readDeliveredEngineEvents(previewIdentity.sid, previewIdentity.generation);
  await captureEvidenceScreenshot(page, 'real-preview-desktop.png');
  await clickAction(page.getByRole('button', { name: '在新窗口打开真实预览' }));
  const previewPage = await previewPagePromise;
  await completeLimeSurvey(previewPage);
  await previewPage.close();
  await expect.poll(() => readDeliveredEngineEvents(previewIdentity.sid, previewIdentity.generation), {
    timeout: 60_000,
    intervals: [500, 1_000, 2_000],
  }).toBeGreaterThan(deliveredBefore);
  await clickAction(page.getByRole('button', { name: '结束真实预览' }));
  await expect(page.getByText('真实预览已结束')).toBeVisible({ timeout: 210_000 });

  const isolationAfter = await readPreviewIsolationSnapshot(surveyId, me.tenantId);
  expect(isolationAfter).toEqual(isolationBefore);
  expect(isolationAfter).toEqual({
    publishedVersions: 0,
    publishedBindings: 0,
    officialRoutes: 0,
    engineOutbox: 0,
    responses: 0,
  });

  await clickAction(page.getByRole('navigation', { name: '问卷工作流' }).getByRole('link', {
    name: '答卷与导出',
  }));
  await expect(page.getByRole('heading', { name: '答卷与导出' })).toBeVisible();
  await expect(page.getByLabel('已完成 0')).toBeVisible();

  const approvedMarker = await approvalJourney(page, surveyId, rootProjectId, me.tenantId, login, runText);

  await fillAction(page.getByLabel('链接名称'), '正式验收链接');
  const createLinkResponse = page.waitForResponse((response) =>
    response.url().includes(`/v1/delivery/surveys/${surveyId}/links`)
      && response.request().method() === 'POST',
  );
  await clickAction(page.getByRole('button', { name: '创建链接' }));
  const createdLink = await readCreatedDeliveryLink(await createLinkResponse);
  const deliveryList = page.getByRole('list', { name: '投放链接' });
  const deliveryLink = deliveryList.getByRole('link').first();
  await expect(deliveryLink).toBeVisible();
  const deliveryQr = deliveryList.getByRole('img', { name: '正式验收链接二维码' });
  await expect(deliveryQr).toBeVisible();
  await expect.poll(() => deliveryQr.evaluate((image: HTMLImageElement) =>
    image.complete && image.naturalWidth > 0 && image.naturalHeight > 0)).toBe(true);
  expect(await decodeQrImage(deliveryQr)).toBe(createdLink.url);
  const deliveryUrl = await deliveryLink.getAttribute('href');
  expect(deliveryUrl).toBe(createdLink.shortUrl ?? createdLink.url);
  expect(deliveryUrl).toMatch(/^http:\/\/127\.0\.0\.1:/);

  if (testInfo.project.name !== 'chromium-wave3-seed') {
    await clickAction(page.getByRole('link', { name: '受众与投放' }));
    await expect(page.getByRole('heading', { name: '受众与投放' })).toBeVisible();
    await fillAction(page.getByLabel('新名单名称'), '浏览器验收受众');
    await clickAction(page.getByRole('button', { name: '创建名单' }));
    await expect(page.getByRole('combobox', { name: '用于当前问卷的名单' })).not.toHaveValue('');
    await fillAction(page.getByLabel('导入内容'), 'name,email\n批量验收联系人,bulk-recipient@example.test\n');
    const importSubmission = page.waitForResponse((response) =>
      response.url().includes('/v1/contact-lists/')
        && response.url().endsWith('/imports')
        && response.request().method() === 'POST',
    );
    await clickAction(page.getByRole('button', { name: '提交批量导入' }));
    expect((await importSubmission).status()).toBe(202);
    await expect(page.getByLabel('联系人导入状态')).toContainText('已处理 1/1 行', { timeout: 60_000 });
    await expect(page.getByLabel('导入行结果')).toContainText('第 1 行：已新增');
    await fillAction(page.getByLabel('联系人姓名'), '浏览器验收联系人');
    await fillAction(page.getByLabel('联系人邮箱'), 'browser-recipient@example.test');
    await clickAction(page.getByRole('button', { name: '新增联系人' }));
    await clickAction(page.getByRole('button', { name: '保存当前受众' }));
    await expect(page.getByRole('status')).toContainText('受众已保存，当前可邀请 2 人。');
    const unavailableTask = page.waitForResponse((response) =>
      response.url().includes(`/v1/surveys/${surveyId}/delivery-tasks`)
        && response.request().method() === 'POST',
    );
    await clickAction(page.getByRole('button', { name: '从受众创建 Email 任务' }));
    expect((await unavailableTask).status()).toBe(503);
    await expect(page.getByRole('alert')).toContainText('渠道未配置，未创建任务');
    await expect(page.getByLabel('投放任务状态')).toHaveCount(0);
  }

  const respondentPage = await page.context().newPage();
  await respondentPage.goto(deliveryUrl!);
  await expect(respondentPage.getByText(approvedMarker)).toBeVisible();
  const relayDiagnostic = process.env.ADMIN_WEB_RELAY_DIAGNOSTIC === '1';
  const sid = relayDiagnostic ? await publishedEngineSid(surveyId, me.tenantId) : 0;
  if (relayDiagnostic) {
    await exerciseAutomaticRelayOutage(respondentPage, sid, me.tenantId);
  } else {
    await completeLimeSurvey(respondentPage);
  }
  await respondentPage.close();

  await clickAction(page.getByRole('navigation', { name: '问卷工作流' }).getByRole('link', {
    name: '答卷与导出',
  }));
  await expect(page.getByRole('heading', { name: '答卷与导出' })).toBeVisible();
  await expect(async () => {
    await clickAction(page.getByRole('button', { name: '刷新答卷数据' }));
    await expect(page.getByLabel('已完成 1')).toBeVisible();
  }).toPass({ timeout: 60_000, intervals: [500, 1_000, 2_000, 5_000] });
  // Positive control: the same tenant-bound snapshot must see formal records.
  // Without this check RLS misconfiguration could make both preview snapshots zero.
  const formalSnapshot = await readPreviewIsolationSnapshot(surveyId, me.tenantId);
  expect(formalSnapshot.publishedVersions).toBeGreaterThan(0);
  expect(formalSnapshot.publishedBindings).toBeGreaterThan(0);
  expect(formalSnapshot.officialRoutes).toBeGreaterThan(0);
  expect(formalSnapshot.responses).toBeGreaterThan(0);
  const formalAnswer = page.locator('.response-answers > div').filter({
    has: page.locator('dd', { hasText: /^A1$/ }),
  }).first();
  await expect(formalAnswer).toBeVisible();
  const formalQuestionCode = (await formalAnswer.locator('dt').textContent())?.trim() ?? '';
  expect(formalQuestionCode).not.toBe('');
  await clickAction(page.getByRole('button', { name: '创建导出任务' }));
  const exportJob = page.locator('.export-job');
  await expect(exportJob).toContainText('导出完成', { timeout: 60_000 });
  const downloadPromise = page.waitForEvent('download');
  await clickAction(page.getByRole('button', { name: '下载导出文件' }));
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/\.(?:csv|zip)$/i);
  await assertDownloadedCsvContainsFormalResponse(download, formalQuestionCode);

  const filteredRequest = page.waitForRequest((request) => request.url().includes('/responses?')
    && request.url().includes('startedFrom='));
  await fillAction(page.getByLabel('开始时间起'), '2020-01-01T00:00');
  await filteredRequest;
  await expect(formalAnswer).toBeVisible();
  await clickAction(page.getByRole('button', { name: '选择显示字段' }));
  await expect(page.getByRole('group', { name: '显示字段' }).getByRole('checkbox').first()).toBeVisible();
  await clickAction(page.getByRole('link', { name: '发布与版本' }));
  await clickAction(page.getByRole('navigation', { name: '问卷工作流' }).getByRole('link', { name: '答卷与导出' }));
  await expect(page.locator('.export-job')).toContainText('导出完成');

  const versionLink = page.getByRole('link', { name: /查看版本 \d+/ }).first();
  await clickAction(page.getByRole('link', { name: '发布与版本' }));
  const version = Number((await versionLink.textContent())?.match(/\d+/)?.[0]);
  expect(version).toBeGreaterThan(0);
  await clickAction(versionLink);
  await expect(page.getByRole('heading', { name: `已发布版本 ${version}` })).toBeVisible();
  await expect(page.getByText('当前在线')).toBeVisible();
  await expect(page.getByText('字段映射')).toBeVisible();

  await clickAction(page.getByRole('link', { name: '返回工作区' }));
  await expect(page.getByRole('list', { name: '当前位置资源' }).getByRole('button', {
    name: SAVED_SURVEY_NAME,
  })).toBeVisible();

  await writeResult({
    rootProjectId,
    folderId,
    network,
    surveyId,
    tenantId: me.tenantId,
    version,
  }, token);
});

test('Wave 5 advanced questions survive authoring, isolated preview and published response', async ({ page, request }) => {
  test.setTimeout(480_000);
  const metadata = await readMetadata();
  const owner = await readToken();
  const operator = (await readFile(requiredEnv('ADMIN_WEB_OPERATOR_JWT_FILE'), 'utf8')).trim();
  const dictionary = `wave5-${Date.now().toString(36)}`;
  const auth = { Authorization: `Bearer ${operator}` };
  for (const [path, data] of [
    ['/v1/platform/dictionaries', { code: dictionary, name: 'Wave 5 测试字典', maxDepth: 2 }],
    [`/v1/platform/dictionaries/${dictionary}/versions`, { version: 'v1' }],
    [`/v1/platform/dictionaries/${dictionary}/versions/v1/nodes`, { nodes: [
      { code: 'R1', parentCode: null, label: '甲地区' }, { code: 'C1', parentCode: 'R1', label: '甲下级' },
      { code: 'R2', parentCode: null, label: '乙地区' }, { code: 'C2', parentCode: 'R2', label: '乙下级' },
    ] }],
    [`/v1/platform/dictionaries/${dictionary}/versions/v1/publish`, {}],
  ] as const) {
    const response = await request.post(path, { headers: auth, data });
    expect(response.ok(), `dictionary setup ${path}: ${response.status()}`).toBeTruthy();
  }
  const result = await readResult();
  const me = await login(page, owner, metadata);
  await navigateWithinApp(page, `/workspace?project=${result.rootProjectId}&parent=${result.folderId}`);
  await createResource(page, '新建问卷', '创建问卷', 'Wave 5 高级题型验收');
  const surveyId = surveyIdFrom(page.url());
  await page.getByLabel('添加题型').selectOption('mjy-collapsible');
  await clickAction(page.getByRole('button', { name: '添加题目' }));
  await expect(page.getByRole('group', { name: '折叠栏目' })).toBeVisible();
  await fillAction(page.getByLabel('栏目标题'), '栏目标题验收');
  await fillAction(page.getByLabel('题目文本'), '栏目正文验收');
  await page.getByLabel('添加题型').selectOption('mjy-cascading-select');
  await clickAction(page.getByRole('button', { name: '添加题目' }));
  await expect(page.getByRole('group', { name: '多级下拉' })).toBeVisible();
  await fillAction(page.getByLabel('题目文本'), '请选择地区');
  await expect(page.getByRole('option', { name: 'Wave 5 测试字典' })).toBeAttached();
  await page.getByLabel('字典').selectOption(dictionary);
  await fillAction(page.getByLabel('第 1 级标题'), '地区');
  await clickAction(page.getByRole('button', { name: '添加级别' }));
  await fillAction(page.getByLabel('第 2 级标题'), '下级地区');
  await page.getByLabel('添加题型').selectOption('mjy-carousel');
  await clickAction(page.getByRole('button', { name: '添加题目' }));
  await expect(page.getByRole('group', { name: '轮播图' })).toBeVisible();
  await fillAction(page.getByLabel('题目文本'), '请选择图片');
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/WZkAAAAASUVORK5CYII=', 'base64');
  await page.getByLabel('上传图片 1').setInputFiles({ name: 'wave5.png', mimeType: 'image/png', buffer: png });
  await expect(page.getByLabel('图片 1', { exact: true })).not.toHaveValue('');
  await fillAction(page.getByLabel('替代文本 1'), '验收图片');
  const imageId = await page.getByLabel('图片 1', { exact: true }).inputValue();
  await clickAction(page.getByRole('button', { name: '添加选项' }));
  await page.getByLabel('图片 2', { exact: true }).selectOption(imageId);
  await fillAction(page.getByLabel('替代文本 2'), '第二张验收图片');
  await saveAndReloadDraft(page, surveyId);
  await clickAction(page.getByRole('button', { name: /请选择图片$/ }));
  await expect(page.getByLabel('替代文本 1')).toHaveValue('验收图片');

  const before = await readPreviewIsolationSnapshot(surveyId, me.tenantId);
  await clickAction(page.getByRole('link', { name: '快速预览' }));
  await clickAction(page.getByRole('button', { name: '创建真实预览' }));
  await expect(page.getByText('真实预览已就绪')).toBeVisible({ timeout: 210_000 });
  const popup = page.context().waitForEvent('page');
  await clickAction(page.getByRole('button', { name: '在新窗口打开真实预览' }));
  const preview = await popup;
  await answerAdvancedQuestions(preview);
  await preview.close();
  await clickAction(page.getByRole('button', { name: '结束真实预览' }));
  await expect(page.getByText('真实预览已结束')).toBeVisible({ timeout: 210_000 });
  expect(await readPreviewIsolationSnapshot(surveyId, me.tenantId)).toEqual(before);

  await clickAction(page.getByRole('link', { name: '发布与版本' }));
  await clickAction(page.getByRole('button', { name: '提交审批' }));
  await clickAction(page.getByRole('button', { name: '批准申请' }));
  await clickAction(page.getByRole('button', { name: '发布问卷' }));
  await expect(page.getByText('发布成功')).toBeVisible({ timeout: 210_000 });
  await fillAction(page.getByLabel('链接名称'), 'Wave 5 正式链接');
  const linkResponse = page.waitForResponse((response) => response.url().includes(`/v1/delivery/surveys/${surveyId}/links`) && response.request().method() === 'POST');
  await clickAction(page.getByRole('button', { name: '创建链接' }));
  const link = await readCreatedDeliveryLink(await linkResponse);
  const respondent = await page.context().newPage();
  await respondent.goto(link.url);
  await answerAdvancedQuestions(respondent);
  await respondent.close();
  await clickAction(page.getByRole('navigation', { name: '问卷工作流' }).getByRole('link', { name: '答卷与导出' }));
  await expect(async () => {
    await clickAction(page.getByRole('button', { name: '刷新答卷数据' }));
    await expect(page.getByLabel('已完成 1')).toBeVisible();
  }).toPass({ timeout: 60_000, intervals: [1000, 2000, 5000] });
  await expect(page.locator('.response-answers')).toContainText('A2');
  await expect(page.locator('.response-answers')).toContainText('R2');
  await expect(page.locator('.response-answers')).toContainText('C2');
});

test('a single-choice display condition is authored and works in isolated preview', async ({ page }) => {
  test.setTimeout(360_000);
  const metadata = await readMetadata();
  const owner = await readToken();
  const result = await readResult();
  await login(page, owner, metadata);
  await navigateWithinApp(page, `/workspace?project=${result.rootProjectId}&parent=${result.folderId}`);
  await createResource(page, '新建问卷', '创建问卷', '条件显示验收');
  const surveyId = surveyIdFrom(page.url());

  await page.getByLabel('添加题型').selectOption('L');
  await clickAction(page.getByRole('button', { name: '添加题目' }));
  await expect(page.getByLabel('题目编码')).toHaveValue('Q1');
  await fillAction(page.getByLabel('题目编码'), 'QTRIGGER');
  await fillAction(page.getByLabel('题目文本'), '是否继续');
  await page.getByLabel('添加题型').selectOption('S');
  await clickAction(page.getByRole('button', { name: '添加题目' }));
  await expect(page.getByLabel('题目编码')).toHaveValue('Q1');
  await fillAction(page.getByLabel('题目文本'), '请说明原因');
  await expect(page.getByLabel('前置题目').locator('option[value="QTRIGGER"]')).toHaveCount(1);
  await page.getByLabel('前置题目').selectOption('QTRIGGER');
  await expect(page.getByLabel('触发选项')).toHaveValue('A1');
  const saveRequest = page.waitForRequest((request) => request.url().includes(`/v1/surveys/${surveyId}/draft`)
    && request.method() === 'PUT');
  const reloadResponse = page.waitForResponse((response) => response.url().includes(`/v1/surveys/${surveyId}/draft`)
    && response.request().method() === 'GET');
  await saveAndReloadDraft(page, surveyId);
  const submitted = (await saveRequest).postDataJSON() as { definition: { groups: Array<{ questions: Array<{ uuid: string; code: string; condition?: string }> }> } };
  const loaded = await (await reloadResponse).json() as { definition: { groups: Array<{ questions: Array<{ uuid: string; code: string; condition?: string }> }> } };
  expect(submitted.definition.groups[0].questions[1].code).toBe('QTRIGGER');
  const expectedCondition = `q("${submitted.definition.groups[0].questions[1].uuid}") == "A1"`;
  expect(submitted.definition.groups[0].questions[2].condition).toBe(expectedCondition);
  expect(loaded.definition.groups[0].questions[1].code).toBe('QTRIGGER');
  expect(loaded.definition.groups[0].questions[2].condition).toBe(expectedCondition);
  await clickAction(page.getByRole('button', { name: /请说明原因/ }));
  await expect(page.getByLabel('前置题目')).toHaveValue('QTRIGGER');

  await clickAction(page.getByRole('link', { name: '快速预览' }));
  await clickAction(page.getByRole('button', { name: '创建真实预览' }));
  await expect(page.getByText('真实预览已就绪')).toBeVisible({ timeout: 210_000 });
  const popup = page.context().waitForEvent('page');
  await clickAction(page.getByRole('button', { name: '在新窗口打开真实预览' }));
  const preview = await popup;
  await preview.waitForLoadState('domcontentloaded');
  for (let step = 0; step < 4 && await preview.locator('input[type="radio"]:visible').count() === 0; step += 1) {
    await limeSurveyAdvanceButton(preview).click();
    await preview.waitForLoadState('domcontentloaded');
  }
  await expect(preview.getByText('请说明原因')).toBeHidden();
  await preview.locator('input[type="radio"]:visible').first().check();
  await expect(preview.getByText('请说明原因')).toBeVisible();
  await preview.close();
  await clickAction(page.getByRole('button', { name: '结束真实预览' }));
  await expect(page.getByText('真实预览已结束')).toBeVisible({ timeout: 210_000 });

  await clickAction(page.getByRole('link', { name: '发布与版本' }));
  await clickAction(page.getByRole('button', { name: '提交审批' }));
  await clickAction(page.getByRole('button', { name: '批准申请' }));
  await clickAction(page.getByRole('button', { name: '发布问卷' }));
  await expect(page.getByText('发布成功')).toBeVisible({ timeout: 210_000 });
  await fillAction(page.getByLabel('链接名称'), '条件显示正式链接');
  const linkResponse = page.waitForResponse((response) => response.url().includes(`/v1/delivery/surveys/${surveyId}/links`) && response.request().method() === 'POST');
  await clickAction(page.getByRole('button', { name: '创建链接' }));
  const link = await readCreatedDeliveryLink(await linkResponse);
  const respondent = await page.context().newPage();
  await respondent.goto(link.url);
  await respondent.waitForLoadState('domcontentloaded');
  for (let step = 0; step < 4 && await respondent.locator('input[type="radio"]:visible').count() === 0; step += 1) {
    await limeSurveyAdvanceButton(respondent).click();
    await respondent.waitForLoadState('domcontentloaded');
  }
  await expect(respondent.getByText('请说明原因')).toBeHidden();
  await respondent.locator('input[type="radio"]:visible').first().check();
  await expect(respondent.getByText('请说明原因')).toBeVisible();
  await completeLimeSurvey(respondent);
  await respondent.close();
  await clickAction(page.getByRole('navigation', { name: '问卷工作流' }).getByRole('link', { name: '答卷与导出' }));
  await expect(async () => {
    await clickAction(page.getByRole('button', { name: '刷新答卷数据' }));
    await expect(page.getByLabel('已完成 1')).toBeVisible();
  }).toPass({ timeout: 60_000, intervals: [1000, 2000, 5000] });
});

test('W04 time windows persist through authoring and govern published responses', async ({ page }) => {
  test.setTimeout(720_000);
  const metadata = await readMetadata();
  const owner = await readToken();
  const result = await readResult();
  const me = await login(page, owner, metadata);
  const scenarios = [
    { name: '当前开放', opensAt: shanghaiLocalTime(-1440), closesAt: shanghaiLocalTime(1440), outcome: 'allow' },
    { name: '未来开放', opensAt: shanghaiLocalTime(1440), closesAt: '', outcome: 'future' },
    { name: '已经截止', opensAt: '', closesAt: shanghaiLocalTime(-1440), outcome: 'closed' },
  ] as const;
  for (const scenario of scenarios) {
    await navigateWithinApp(page, `/workspace?project=${result.rootProjectId}&parent=${result.folderId}`);
    await createResource(page, '新建问卷', '创建问卷', `时间窗${scenario.name}验收`);
    const surveyId = surveyIdFrom(page.url());
    await page.getByLabel('添加题型').selectOption('L');
    await clickAction(page.getByRole('button', { name: '添加题目' }));
    await expect(page.getByLabel('题目编码')).toHaveValue('Q1');
    await fillAction(page.getByLabel('题目文本'), `时间窗${scenario.name}题目`);
    await clickAction(page.getByLabel('启用开放时间窗'));
    if (scenario.opensAt) await fillAction(page.getByLabel('开放时间', { exact: true }), scenario.opensAt);
    if (scenario.closesAt) await fillAction(page.getByLabel('截止时间', { exact: true }), scenario.closesAt);
    await fillAction(page.getByLabel('时区', { exact: true }), 'Asia/Shanghai');
    const reloaded = page.waitForResponse((response) => response.url().includes(`/v1/surveys/${surveyId}/draft`)
      && response.request().method() === 'GET');
    await saveAndReloadDraft(page, surveyId);
    const draft = await (await reloaded).json() as { definition: { policy: { window: { opensAt?: string; closesAt?: string; timezone: string } } } };
    expect(draft.definition.policy.window).toEqual({
      ...(scenario.opensAt ? { opensAt: scenario.opensAt } : {}),
      ...(scenario.closesAt ? { closesAt: scenario.closesAt } : {}),
      timezone: 'Asia/Shanghai',
    });
    await expect(page.getByLabel('启用开放时间窗')).toBeChecked();
    await expect(page.getByLabel('时区', { exact: true })).toHaveValue('Asia/Shanghai');
    await clickAction(page.getByRole('link', { name: '发布与版本' }));
    await clickAction(page.getByRole('button', { name: '提交审批' }));
    await clickAction(page.getByRole('button', { name: '批准申请' }));
    await clickAction(page.getByRole('button', { name: '发布问卷' }));
    await expect(page.getByText('发布成功')).toBeVisible({ timeout: 210_000 });
    await fillAction(page.getByLabel('链接名称'), `时间窗${scenario.name}链接`);
    const linkResponse = page.waitForResponse((response) => response.url().includes(`/v1/delivery/surveys/${surveyId}/links`) && response.request().method() === 'POST');
    await clickAction(page.getByRole('button', { name: '创建链接' }));
    const link = await readCreatedDeliveryLink(await linkResponse);
    const engineSid = await publishedEngineSid(surveyId, me.tenantId);
    const responseCount = async () => Number(await platformQuery(me.tenantId,
      `SELECT count(*) FROM response_projection WHERE tenant_id='${me.tenantId}' AND survey_id=${engineSid};`));
    const responsesBefore = await responseCount();
    const respondent = await page.context().newPage();
    await respondent.goto(link.url);
    if (scenario.outcome === 'allow') {
      await completeLimeSurvey(respondent);
      await expect.poll(responseCount, { timeout: 60_000 }).toBe(responsesBefore + 1);
    } else {
      await expect(respondent.locator('body')).toContainText(scenario.outcome === 'future' ? '尚未开放' : '截止');
      expect(await responseCount()).toBe(responsesBefore);
    }
    await respondent.close();
  }
});

function shanghaiLocalTime(minutesFromNow: number): string {
  const parts = new Intl.DateTimeFormat('en-US', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit',
    day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
    .formatToParts(new Date(Date.now() + minutesFromNow * 60_000));
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}T${values.hour}:${values.minute}`;
}

async function answerAdvancedQuestions(page: Page) {
  await expect(page.locator('[data-mjy-collapsible]')).toBeAttached({ timeout: 30_000 });
  await clickAction(page.getByText('栏目标题验收', { exact: true }));
  const cascading = page.locator('[data-mjy-cascading-select]');
  await expect(cascading).toBeVisible();
  const probe = await cascading.evaluate(async (root) => {
    const form = root.closest('form');
    const sid = root.getAttribute('data-mjy-sid') || form?.querySelector<HTMLInputElement>('input[name="sid"]')?.value || '';
    const query = new URLSearchParams({ plugin: 'MjyQuestionExtensions', function: 'dictionaryNodes', sid,
      dictionary: root.getAttribute('data-mjy-dictionary') ?? '', version: root.getAttribute('data-mjy-version') ?? '', parent: '', limit: '200' });
    const match = location.pathname.match(/^(.*\/)index\.php(?:\/|$)/);
    const base = match ? match[1] : location.pathname.slice(0, location.pathname.lastIndexOf('/') + 1);
    const url = new URL(`${base}index.php/plugins/direct?${query}`, location.origin);
    const response = await fetch(url);
    const contentType = response.headers.get('content-type') ?? '';
    const body = contentType.includes('json') ? await response.json() : null;
    return { path: url.pathname, status: response.status, contentType, nodes: Array.isArray(body?.nodes) ? body.nodes.length : -1 };
  });
  expect(probe, `dictionary endpoint probe ${JSON.stringify(probe)}`).toMatchObject({ status: 200, nodes: 2 });
  const select = cascading.locator('select').first();
  await expect(select.locator('option[value="R1"]')).toBeAttached({ timeout: 30_000 });
  await select.selectOption('R1');
  const child = cascading.locator('select').nth(1);
  await expect(child.locator('option[value="C1"]')).toBeAttached({ timeout: 30_000 });
  await child.selectOption('C1');
  await select.selectOption('R2');
  await expect(child).toHaveValue('');
  await expect(child.locator('option[value="C2"]')).toBeAttached({ timeout: 30_000 });
  await child.selectOption('C2');
  const carousel = page.locator('[data-mjy-carousel]');
  await expect(carousel.getByAltText('验收图片', { exact: true })).toBeVisible();
  await expect.poll(() => carousel.getByAltText('验收图片', { exact: true }).evaluate((image: HTMLImageElement) => image.complete && image.naturalWidth > 0)).toBe(true);
  await clickAction(carousel.getByRole('button', { name: '下一张' }));
  await expect(carousel.getByAltText('第二张验收图片')).toBeVisible();
  await carousel.locator('input[type="radio"][value="A2"]').check();
  const advance = limeSurveyAdvanceButton(page);
  await advance.click();
  await expect(carousel).toHaveCount(0, { timeout: 30_000 });
}

async function completeLimeSurvey(enginePage: Page) {
  await enginePage.waitForLoadState('domcontentloaded');
  const visibleChoices = enginePage.locator('input[type="radio"]:visible');
  for (let step = 0; step < 4 && await visibleChoices.count() === 0; step += 1) {
    const advance = limeSurveyAdvanceButton(enginePage);
    await expect(advance).toBeVisible({ timeout: 30_000 });
    await advance.click();
    await enginePage.waitForLoadState('domcontentloaded');
  }
  const firstChoice = visibleChoices.first();
  await expect(firstChoice).toBeVisible();
  await firstChoice.check();

  for (let step = 0; step < 4; step += 1) {
    const submit = limeSurveyAdvanceButton(enginePage);
    if (await submit.count() === 0) break;
    await submit.click();
    await enginePage.waitForLoadState('domcontentloaded');
    if (await visibleChoices.count() === 0) break;
  }
  await expect(visibleChoices).toHaveCount(0, { timeout: 30_000 });
}

async function exerciseDeliveryWorkspace(page: Page) {
  const suffix = Date.now().toString(36);
  await clickAction(page.getByRole('link', { name: '投放', exact: true }));
  await expect(page).toHaveURL(/\/delivery$/);
  await expect(page.getByRole('heading', { name: '通讯录与投放' })).toBeVisible();

  await fillAction(page.getByLabel('投放工作区新名单名称'), `全链路名单-${suffix}`);
  await clickAction(page.getByRole('button', { name: '创建名单' }));
  await expect(page.getByLabel('当前联系人名单')).not.toHaveValue('');

  await fillAction(page.getByLabel('部门名称'), `华东区-${suffix}`);
  await clickAction(page.getByRole('button', { name: '创建部门' }));
  await expect(page.getByText(`华东区-${suffix}（顶级部门）`)).toBeVisible();

  await fillAction(page.getByLabel('标签名称'), `重点客户-${suffix}`);
  await clickAction(page.getByRole('button', { name: '创建标签' }));
  await expect(page.getByText(new RegExp(`现有标签：.*重点客户-${suffix}`))).toBeVisible();

  await fillAction(page.getByLabel('工作区联系人姓名'), `目录联系人-${suffix}`);
  await fillAction(page.getByLabel('工作区联系人邮箱'), `directory-${suffix}@example.test`);
  await page.getByLabel('联系人部门').selectOption({ label: `华东区-${suffix}` });
  await clickAction(page.getByRole('button', { name: '新增联系人' }));
  const row = page.getByRole('row').filter({ hasText: `目录联系人-${suffix}` });
  await expect(row).toContainText(`directory-${suffix}@example.test`);
  await row.getByRole('combobox').selectOption({ label: `重点客户-${suffix}` });
  await clickAction(row.getByRole('button', { name: '添加标签' }));
  await expect(row).toContainText(`重点客户-${suffix}`);

  await fillAction(page.getByLabel('投放工作区导入内容'), `name,email\n导入联系人-${suffix},import-${suffix}@example.test\n`);
  await clickAction(page.getByRole('button', { name: '提交导入任务' }));
  await expect(page.getByRole('status')).toContainText('已处理 1/1 行', { timeout: 60_000 });
}

async function readCreatedDeliveryLink(response: Response): Promise<CreatedDeliveryLink> {
  expect(response.status()).toBe(201);
  const payload: unknown = await response.json();
  expect(payload).toEqual(expect.objectContaining({
    id: expect.stringMatching(/^[0-9a-f-]{36}$/i),
    url: expect.stringMatching(/^http:\/\/127\.0\.0\.1:/),
  }));
  const link = payload as CreatedDeliveryLink;
  expect(link.shortUrl === null || /^http:\/\/127\.0\.0\.1:/.test(link.shortUrl)).toBe(true);
  return link;
}

async function decodeQrImage(image: Locator) {
  const pixels = await image.evaluate((element: HTMLImageElement) => {
    const canvas = document.createElement('canvas');
    canvas.width = element.naturalWidth;
    canvas.height = element.naturalHeight;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('QR canvas is unavailable');
    context.drawImage(element, 0, 0);
    return {
      data: Array.from(context.getImageData(0, 0, canvas.width, canvas.height).data),
      height: canvas.height,
      width: canvas.width,
    };
  });
  return jsQR(Uint8ClampedArray.from(pixels.data), pixels.width, pixels.height)?.data ?? null;
}

async function readPreviewIsolationSnapshot(
  surveyId: string,
  tenantId: string,
): Promise<PreviewIsolationSnapshot> {
  assertCanonicalUuid(surveyId);
  assertCanonicalUuid(tenantId);
  const sql = `SELECT json_build_object(
    'publishedVersions', (SELECT count(*)::int FROM survey_published_version WHERE tenant_id = '${tenantId}' AND survey_id = '${surveyId}'),
    'publishedBindings', (SELECT count(*)::int FROM survey_question_binding WHERE tenant_id = '${tenantId}' AND survey_id = '${surveyId}'),
    'officialRoutes', (SELECT count(*)::int FROM survey_route WHERE tenant_id = '${tenantId}' AND public_id = '${surveyId}' AND superseded_at IS NULL),
    'engineOutbox', (SELECT count(*)::int FROM engine_outbox WHERE tenant_id = '${tenantId}'),
    'responses', (SELECT count(*)::int FROM response_projection WHERE tenant_id = '${tenantId}')
  )::text;`;
  const output = await platformQuery(tenantId, sql);
  const payload: unknown = JSON.parse(output.trim());
  expect(payload).toEqual({
    publishedVersions: expect.any(Number),
    publishedBindings: expect.any(Number),
    officialRoutes: expect.any(Number),
    engineOutbox: expect.any(Number),
    responses: expect.any(Number),
  });
  return payload as PreviewIsolationSnapshot;
}

async function readPreviewIdentity(tenantId: string, surveyId: string, sessionId: string) {
  assertCanonicalUuid(tenantId);
  assertCanonicalUuid(surveyId);
  assertCanonicalUuid(sessionId);
  const raw = await platformQuery(tenantId, `SELECT json_build_object('sid', engine_sid, 'generation', generation)::text FROM survey_preview_session WHERE tenant_id='${tenantId}' AND survey_id='${surveyId}' AND id='${sessionId}' AND status='ready';`);
  const value: unknown = JSON.parse(raw);
  if (typeof value !== 'object' || value === null || !('sid' in value) || !('generation' in value)
      || typeof value.sid !== 'number' || !Number.isSafeInteger(value.sid) || value.sid <= 0
      || typeof value.generation !== 'string' || !/^preview-[a-z0-9][a-z0-9-]{2,55}$/.test(value.generation)) {
    throw new Error('Preview engine identity is invalid');
  }
  // The bridge generation is an engine UUID, distinct from the preview session generation.
  const engineGeneration = await engineEventQuery(`SELECT generation FROM lime_mjyplatformbridge_generation WHERE survey_id=${value.sid};`);
  assertCanonicalUuid(engineGeneration);
  return { sid: value.sid, generation: engineGeneration };
}

async function readDeliveredEngineEvents(sid: number, generation: string) {
  if (!Number.isSafeInteger(sid) || sid <= 0) throw new Error('Preview engine identity is invalid');
  assertCanonicalUuid(generation);
  const count = Number(await engineEventQuery(`SELECT COUNT(*) FROM lime_mjyplatformbridge_event_log WHERE survey_id=${sid} AND generation='${generation}' AND event_type='response.completed' AND delivered_at IS NOT NULL;`));
  if (!Number.isInteger(count) || count < 0) throw new Error('Engine event count is invalid');
  return count;
}

async function engineEventQuery(sql: string): Promise<string> {
  const container = requiredEnv('ADMIN_WEB_ENGINE_DB_CONTAINER');
  const args = requiredEnv('ADMIN_WEB_ENGINE_DB_KIND') === 'pgsql'
    ? ['exec', container, 'psql', '-U', 'postgres', '-d', 'limesurvey', '-tAq', '-c', sql]
    : ['exec', container, 'mariadb', '-uroot', '-proot', 'limesurvey', '-N', '-B', '-e', sql];
  return (await runText('docker', args)).trim();
}

async function platformQuery(tenantId: string, sql: string): Promise<string> {
  assertCanonicalUuid(tenantId);
  return (await runText('docker', ['exec', requiredEnv('ADMIN_WEB_PLATFORM_DB_CONTAINER'),
    'psql', '-U', 'platform_owner', '-d', 'platform', '-tAq', '-c', `BEGIN; SET LOCAL app.tenant_id = '${tenantId}'; ${sql} COMMIT;`])).trim();
}

async function publishedEngineSid(surveyId: string, tenantId: string): Promise<number> {
  assertCanonicalUuid(surveyId);
  assertCanonicalUuid(tenantId);
  const value = Number(await platformQuery(tenantId, `SELECT engine_sid FROM survey_published_version WHERE tenant_id='${tenantId}' AND survey_id='${surveyId}' AND version_no=1;`));
  if (!Number.isSafeInteger(value) || value <= 0) throw new Error('Invalid diagnostic survey mapping');
  return value;
}

async function exerciseAutomaticRelayOutage(respondent: Page, sid: number, tenantId: string) {
  if (process.env.GITHUB_ACTIONS !== 'true' || process.env.RUNNER_ENVIRONMENT !== 'github-hosted') {
    throw new Error('Relay outage test requires a dedicated GitHub-hosted runner');
  }
  const platform = requiredEnv('ADMIN_WEB_PLATFORM_CONTAINER');
  const relay = requiredEnv('ADMIN_WEB_RELAY_CONTAINER');
  if (!/^[0-9a-f]{64}$/.test(platform) || !/^[0-9a-f]{64}$/.test(relay)) throw new Error('Invalid container identities');
  const project = requiredEnv('COMPOSE_PROJECT_NAME');
  if (!/^relay-ci-[a-z0-9-]+$/.test(project)) throw new Error('Invalid diagnostic project');
  for (const [id, service] of [[platform, 'platform'], [relay, 'test-relay']]) {
    const labels = await runText('docker', ['inspect', '--format', '{{index .Config.Labels "com.docker.compose.project"}}/{{index .Config.Labels "com.docker.compose.service"}}', id]);
    if (labels.trim() !== `${project}/${service}`) throw new Error('Diagnostic container ownership mismatch');
  }
  const heartbeat = async () => Number((await runText('docker', ['exec', relay, 'stat', '-c', '%Y', '/tmp/survey-event-relay-heartbeat'])).trim());
  await expect.poll(heartbeat, { timeout: 60_000 }).toBeGreaterThan(0);
  const before = await heartbeat();
  await runText('docker', ['pause', platform]);
  try {
    await completeLimeSurvey(respondent);
    await expect.poll(async () => Number(await engineEventQuery(`SELECT count(*) FROM lime_mjyplatformbridge_event_log WHERE survey_id=${sid} AND delivered_at IS NULL;`)), { timeout: 30_000 }).toBeGreaterThan(0);
    // The production loop advanced; heartbeat does not prove a delivery attempt
    // or successful delivery.
    await expect.poll(heartbeat, { timeout: 60_000, intervals: [1000, 2000] }).toBeGreaterThan(before);
  } finally {
    await runText('docker', ['unpause', platform]);
  }
  await expect.poll(async () => Number(await engineEventQuery(`SELECT count(*) FROM lime_mjyplatformbridge_event_log WHERE survey_id=${sid} AND delivered_at IS NULL;`)), { timeout: 90_000, intervals: [1000, 2000] }).toBe(0);
  const completed = (await engineEventQuery(`SELECT event_id FROM lime_mjyplatformbridge_event_log WHERE survey_id=${sid} AND event_type='response.completed' ORDER BY id LIMIT 1;`)).trim();
  assertCanonicalUuid(completed);
  const inboxCount = async () => Number(await platformQuery(tenantId, `SELECT count(*) FROM engine_event_inbox WHERE tenant_id='${tenantId}' AND survey_id=${sid} AND event_id='${completed}';`));
  const projectionCount = async () => Number(await platformQuery(tenantId, `SELECT count(*) FROM response_projection WHERE tenant_id='${tenantId}' AND survey_id=${sid} AND state='engine_completed';`));
  await expect.poll(inboxCount, { timeout: 30_000 }).toBe(1);
  await expect.poll(projectionCount, { timeout: 30_000 }).toBe(1);
  // Synthetic acknowledgement loss: replay exactly one event already accepted
  // by the platform, then prove the inbox and projection remain deduplicated.
  await engineEventQuery(`UPDATE lime_mjyplatformbridge_event_log SET delivered_at=NULL WHERE survey_id=${sid} AND event_id='${completed}';`);
  await expect.poll(async () => Number(await engineEventQuery(`SELECT count(*) FROM lime_mjyplatformbridge_event_log WHERE survey_id=${sid} AND event_id='${completed}' AND delivered_at IS NOT NULL;`)), { timeout: 90_000, intervals: [1000, 2000] }).toBe(1);
  expect(await inboxCount()).toBe(1);
  expect(await projectionCount()).toBe(1);

  // Restart the exact test worker, then replay the same synthetic event again.
  await runText('docker', ['restart', relay]);
  await engineEventQuery(`UPDATE lime_mjyplatformbridge_event_log SET delivered_at=NULL WHERE survey_id=${sid} AND event_id='${completed}';`);
  await expect.poll(async () => Number(await engineEventQuery(`SELECT count(*) FROM lime_mjyplatformbridge_event_log WHERE survey_id=${sid} AND event_id='${completed}' AND delivered_at IS NOT NULL;`)), { timeout: 90_000, intervals: [1000, 2000] }).toBe(1);
  expect(await inboxCount()).toBe(1);
  expect(await projectionCount()).toBe(1);
}

async function assertDownloadedCsvContainsFormalResponse(download: Download, expectedQuestionCode: string) {
  const path = await download.path();
  expect(path).not.toBeNull();
  const entries = (await runBytes('unzip', ['-Z1', path!])).toString('utf8').trim().split('\n');
  expect(entries).toEqual(expect.arrayContaining([
    'responses.csv', 'fields.csv', 'attachments.csv', 'extensions.csv',
  ]));
  const responses = await readZipCsv(path!, 'responses.csv');
  const fields = await readZipCsv(path!, 'fields.csv');
  const fieldHeader = fields[0] ?? [];
  const questionCodeIndex = fieldHeader.indexOf('questioncode');
  const fieldnameIndex = fieldHeader.indexOf('fieldname');
  const columnIndex = fieldHeader.indexOf('column');
  expect(questionCodeIndex).toBeGreaterThanOrEqual(0);
  expect(fieldnameIndex).toBeGreaterThanOrEqual(0);
  expect(columnIndex).toBeGreaterThanOrEqual(0);
  const questionField = fields.slice(1).find((row) =>
    row[questionCodeIndex] === expectedQuestionCode
      || row[fieldnameIndex] === expectedQuestionCode
      || row[columnIndex] === expectedQuestionCode);
  expect(questionField).toBeDefined();
  const responseColumn = responses[0]?.indexOf(questionField![columnIndex]!) ?? -1;
  expect(responseColumn).toBeGreaterThanOrEqual(0);
  expect(responses).toHaveLength(3);
  expect(responses[2]?.[responseColumn]).toBe('A1');
}

async function readZipCsv(path: string, entry: string) {
  const bytes = await runBytes('unzip', ['-p', path, entry]);
  expect(bytes.length).toBeGreaterThan(3);
  expect([...bytes.subarray(0, 3)]).toEqual([0xef, 0xbb, 0xbf]);
  const csv = bytes.subarray(3).toString('utf8');
  expect(csv).toContain('\r\n');
  expect(csv.replaceAll('\r\n', '')).not.toContain('\n');
  return parseCsv(csv);
}

function parseCsv(value: string) {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = '';
  let quoted = false;
  for (let index = 0; index < value.length; index += 1) {
    const current = value[index];
    if (current === '"') {
      if (quoted && value[index + 1] === '"') {
        cell += '"';
        index += 1;
      } else {
        quoted = !quoted;
      }
    } else if (current === ',' && !quoted) {
      row.push(cell);
      cell = '';
    } else if (current === '\r' && value[index + 1] === '\n' && !quoted) {
      row.push(cell);
      rows.push(row);
      row = [];
      cell = '';
      index += 1;
    } else {
      cell += current;
    }
  }
  if (cell || row.length) rows.push([...row, cell]);
  return rows;
}

function assertCanonicalUuid(value: string) {
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value)) {
    throw new Error('Expected a canonical UUID');
  }
}

function runText(file: string, args: string[]) {
  return new Promise<string>((resolve, reject) => {
    execFile(file, args, { encoding: 'utf8', maxBuffer: 4 * 1024 * 1024 }, (error, stdout) => {
      if (error) reject(error);
      else resolve(stdout);
    });
  });
}

function runBytes(file: string, args: string[]) {
  return new Promise<Buffer>((resolve, reject) => {
    execFile(file, args, { encoding: 'buffer', maxBuffer: 16 * 1024 * 1024 }, (error, stdout) => {
      if (error) reject(error);
      else resolve(stdout);
    });
  });
}

function limeSurveyAdvanceButton(enginePage: Page) {
  return enginePage.locator(
    '#ls-button-submit:visible, #ls-button-next:visible, button[type="submit"]:visible, input[type="submit"]:visible',
  ).last();
}

test('desktop pointer dragging persists question and group order after refresh', async ({ page }) => {
  const surveyId = await createSortableSurvey(page, 'pointer');

  await dragWithPointer(
    page,
    page.getByRole('button', { name: '拖动题组 导入的题目' }),
    page.getByRole('button', { name: '拖动题目 QNOTE' }),
  );
  await expect(sortStatus(page)).toContainText('题组 导入的题目 已移动到第 1 位');

  await dragWithPointer(
    page,
    page.getByRole('button', { name: '拖动题目 QNOTE' }),
    page.getByRole('button', { name: '拖动题目 Q1' }),
  );
  await expect(sortStatus(page)).toContainText('题目 QNOTE 已移动到题组 导入的题目 第 1 位');

  await saveAndReloadDraft(page, surveyId);
  await expectOutlineOrder(page, sortedOutlineOrder);
});

test('desktop explicit sorting controls persist the same order after refresh', async ({ page }) => {
  const surveyId = await createSortableSurvey(page, 'controls');

  await clickAction(page.getByRole('button', { name: '下移 QNOTE' }));
  await expect(sortStatus(page)).toContainText('题目 QNOTE 已移动到题组 导入的题目 第 1 位');
  await clickAction(page.getByRole('button', { name: '上移 导入的题目' }));
  await expect(sortStatus(page)).toContainText('题组 导入的题目 已移动到第 1 位');

  await saveAndReloadDraft(page, surveyId);
  await expectOutlineOrder(page, sortedOutlineOrder);
});

test('desktop keyboard sorting controls persist question order after refresh', async ({ page }) => {
  const surveyId = await createSortableSurvey(page, 'keyboard');
  const moveDown = page.getByRole('button', { name: '下移 QNOTE' });

  await moveDown.focus();
  await expectFocusedWithVisibleOutline(moveDown);
  await moveDown.press('Enter');
  await expect(sortStatus(page)).toContainText('题目 QNOTE 已移动到题组 导入的题目 第 1 位');

  await saveAndReloadDraft(page, surveyId);
  await expectOutlineOrder(page, [
    { title: '第一题组', questions: [] },
    { title: '导入的题目', questions: ['QNOTE', 'Q1'] },
  ]);
});

test('desktop breakpoints keep workspace, breadcrumbs, dialogs and editor controls usable', async ({ page }) => {
  const metadata = await readMetadata();
  const token = await readToken();
  const result = await readResult();

  for (const width of [768, 819, 820, 1024, 1179, 1180, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await login(page, token, metadata);
    await navigateWithinApp(
      page,
      `/workspace?project=${result.rootProjectId}&parent=${result.folderId}`,
    );
    await expect(page.getByRole('navigation', { name: '当前位置' })).toBeVisible();
    await expectNoHorizontalOverflow(page);
    await assertTouchTarget(page.getByLabel('搜索资源'));
    await assertTouchTarget(page.getByLabel('资源类型'));
    await assertTouchTarget(page.getByLabel('资源状态'));
    await assertTouchTarget(page.getByLabel('排序方式'));

    const projectToggle = page.getByRole('button', { name: '打开项目导航' });
    if (width < 820) {
      await assertTouchTarget(projectToggle);
      await clickAction(projectToggle);
      const drawer = page.getByRole('complementary', { name: '项目与文件夹' });
      await expect(drawer).toBeVisible();
      await expect(drawer).toBeInViewport();
      const closeDrawer = drawer.getByRole('button', { name: '关闭项目导航' });
      await assertTouchTarget(closeDrawer);
      await clickAction(closeDrawer);
    } else {
      await expect(projectToggle).toBeHidden();
    }

    await openCreateDialog(page, '新建文件夹');
    const dialog = page.getByRole('dialog', { name: '新建文件夹' });
    await expect(dialog).toBeVisible();
    await expect(dialog).toBeInViewport();
    await expectFocusedWithVisibleOutline(dialog.getByRole('textbox'));
    await assertTouchTarget(dialog.getByRole('button', { name: '取消' }));
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
    await expectNoHorizontalOverflow(page);

    await navigateWithinApp(page, `/surveys/${result.surveyId}/edit`);
    await expect(page.getByRole('navigation', { name: '面包屑' })).toBeVisible();
    await assertTouchTarget(page.getByRole('button', { name: '保存草稿' }));
    if (width < 820) {
      await expect(page.getByRole('tablist', { name: '编辑区域' })).toBeVisible();
    } else {
      await expect(page.getByRole('tablist', { name: '编辑区域' })).toBeHidden();
    }
    if (width >= 820 && width < 1180) {
      const openInspector = page.getByRole('button', { name: '打开属性检查器' });
      await assertTouchTarget(openInspector);
      await clickAction(openInspector);
      const inspector = page.getByRole('dialog', { name: '问卷属性' });
      await expect(inspector).toBeVisible();
      await expect(inspector.getByLabel('标题')).toBeVisible();
      await assertTouchTarget(inspector.getByRole('button', { name: '关闭属性检查器' }));
      await page.keyboard.press('Escape');
      await expect(inspector).toBeHidden();
      await expect(openInspector).toBeFocused();
    }
    await expectNoHorizontalOverflow(page);
  }
});

test('keyboard-only workspace and narrow editor flow exposes focus and dialogs', async ({ page }) => {
  const metadata = await readMetadata();
  const token = await readToken();
  const result = await readResult();
  await page.setViewportSize({ width: 768, height: 900 });
  await login(page, token, metadata);
  await navigateWithinApp(
    page,
    `/workspace?project=${result.rootProjectId}&parent=${result.folderId}`,
  );

  const create = page.getByRole('button', { name: '新建' });
  await expect(create).toBeVisible();
  const search = page.getByLabel('搜索资源');
  await search.focus();
  await expectFocusedWithVisibleOutline(search);
  await page.keyboard.press('Tab');
  await expect(page.getByLabel('资源类型')).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(page.getByLabel('资源状态')).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(page.getByLabel('排序方式')).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(create).toBeFocused();
  await page.keyboard.press('Enter');
  await page.keyboard.press('Tab');
  await expect(page.getByRole('menuitem', { name: '新建项目' })).toBeFocused();
  await page.keyboard.press('Enter');
  const dialog = page.getByRole('dialog', { name: '新建项目' });
  await expect(dialog).toBeVisible();
  await expectFocusedWithVisibleOutline(dialog.getByRole('textbox'));
  await page.keyboard.press('Escape');
  await expect(create).toBeFocused();

  await navigateWithinApp(page, `/surveys/${result.surveyId}/edit`);
  for (const name of ['大纲', '编辑', '属性']) {
    const tab = page.getByRole('tab', { name });
    await tab.focus();
    await expectFocusedWithVisibleOutline(tab);
    await page.keyboard.press('Enter');
    await expect(tab).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByRole('tabpanel', { name })).toBeVisible();
  }
  await expectNoHorizontalOverflow(page);
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
    await assertTouchTarget(tab);
    await assertUsable(panel);
    await assertUsable(control);
    await assertTouchTarget(control);
    await page.keyboard.press('Tab');
    expect(await page.locator(':focus').evaluate((element) => getComputedStyle(element).outlineStyle))
      .not.toBe('none');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  }
  await assertUsable(page.getByRole('button', { name: '保存草稿' }));
});

test('@mobile real preview remains usable and closes explicitly', async ({ page }) => {
  const metadata = await readMetadata();
  const token = await readToken();
  const result = await readResult();
  await login(page, token, metadata);
  await navigateWithinApp(page, `/surveys/${result.surveyId}/preview`);

  await expect(page.getByRole('heading', { name: '快速预览' })).toBeVisible();
  await clickAction(page.getByRole('button', { name: '创建真实预览' }));
  await expect(page.getByText('真实预览已就绪')).toBeVisible({ timeout: 210_000 });
  await assertTouchTarget(page.getByRole('button', { name: '在新窗口打开真实预览' }));
  await assertTouchTarget(page.getByRole('button', { name: '结束真实预览' }));
  await expectNoHorizontalOverflow(page);
  await captureEvidenceScreenshot(page, 'real-preview-mobile.png');
  await clickAction(page.getByRole('button', { name: '结束真实预览' }));
  await expect(page.getByText('真实预览已结束')).toBeVisible({ timeout: 210_000 });
});

test('@mobile workspace drawer, breadcrumbs and dialog controls remain touch accessible', async ({ page }) => {
  const metadata = await readMetadata();
  const token = await readToken();
  const result = await readResult();
  await login(page, token, metadata);
  await navigateWithinApp(
    page,
    `/workspace?project=${result.rootProjectId}&parent=${result.folderId}`,
  );

  await expect(page.getByRole('navigation', { name: '当前位置' })).toBeVisible();
  await expectNoHorizontalOverflow(page);
  for (const control of [
    page.getByLabel('搜索资源'),
    page.getByLabel('资源类型'),
    page.getByLabel('资源状态'),
    page.getByLabel('排序方式'),
    page.getByRole('button', { name: '打开项目导航' }),
  ]) {
    await assertTouchTarget(control);
  }

  await page.getByRole('button', { name: '打开项目导航' }).tap();
  const drawer = page.getByRole('complementary', { name: '项目与文件夹' });
  await expect(drawer).toBeVisible();
  const closeDrawer = drawer.getByRole('button', { name: '关闭项目导航' });
  await assertTouchTarget(closeDrawer);
  await closeDrawer.tap();

  await openCreateDialog(page, '新建文件夹');
  const dialog = page.getByRole('dialog', { name: '新建文件夹' });
  await expect(dialog).toBeVisible();
  await expectFocusedWithVisibleOutline(dialog.getByRole('textbox'));
  await assertTouchTarget(dialog.getByRole('button', { name: '取消' }));
  await assertTouchTarget(dialog.getByRole('button', { name: '创建文件夹' }));
  await page.keyboard.press('Escape');
  await expectNoHorizontalOverflow(page);
});

test('@mobile touch-accessible sorting persists without horizontal overflow', async ({ page }) => {
  const metadata = await readMetadata();
  const token = await readToken();
  await login(page, token, metadata);
  const result = await readResult();
  const surveyId = result.surveyId;
  await navigateWithinApp(page, `/surveys/${surveyId}/edit`);
  await expect(page).toHaveURL(new RegExp(`/surveys/${surveyId}/edit$`));
  const currentOrder = await readOutlineOrder(page);
  if (currentOrder[0]?.title === '导入的题目') {
    await page.getByRole('button', { name: '上移 第一题组' }).tap();
    await saveAndReloadDraft(page, surveyId);
  }
  await expectOutlineOrder(page, [
    { title: '第一题组', questions: ['QNOTE'] },
    { title: '导入的题目', questions: ['Q1'] },
  ]);
  const reorderButtons = [
    page.getByRole('button', { name: '下移 QNOTE' }),
    page.getByRole('button', { name: '上移 Q1' }),
    page.getByRole('button', { name: '下移 第一题组' }),
    page.getByRole('button', { name: '上移 导入的题目' }),
  ];

  for (const button of reorderButtons) await assertTouchTarget(button);
  await expectNoHorizontalOverflow(page);

  await reorderButtons[2].tap();
  await expect(sortStatus(page)).toContainText('题组 第一题组 已移动到第 2 位');
  await saveAndReloadDraft(page, surveyId);
  await expectOutlineOrder(page, [
    { title: '导入的题目', questions: ['Q1'] },
    { title: '第一题组', questions: ['QNOTE'] },
  ]);
  await expectNoHorizontalOverflow(page);
});

const sortedOutlineOrder: OutlineGroupOrder[] = [
  { title: '导入的题目', questions: ['QNOTE', 'Q1'] },
  { title: '第一题组', questions: [] },
];

async function createSortableSurvey(page: Page, label: string) {
  const metadata = await readMetadata();
  const token = await readToken();
  await login(page, token, metadata);

  const result = await readResult();
  await navigateWithinApp(
    page,
    `/workspace?project=${result.rootProjectId}&parent=${result.folderId}`,
  );
  await expect(page.getByRole('heading', { name: '项目与问卷' })).toBeVisible();
  await createResource(page, '新建问卷', '创建问卷', `排序验收问卷 ${label}`);
  await expect(page).toHaveURL(/\/surveys\/[0-9a-f-]{36}\/edit/);
  const surveyId = surveyIdFrom(page.url());

  await clickAction(page.getByRole('link', { name: '批量导入' }));
  await fillAction(page.getByLabel('待导入文本'), importText);
  await clickAction(page.getByRole('button', { name: '预览导入' }));
  await clickAction(page.getByRole('button', { name: '确认导入 1 道题' }));
  await expect(page).toHaveURL(new RegExp(`/surveys/${surveyId}/edit$`));
  await expectOutlineOrder(page, [
    { title: '第一题组', questions: ['QNOTE'] },
    { title: '导入的题目', questions: ['Q1'] },
  ]);
  return surveyId;
}

async function createSurveyResourceTree(page: Page) {
  const resultsDir = requiredEnv('ADMIN_WEB_TEST_RESULTS_DIR');
  let rootProjectId = '';
  await createResource(page, '新建项目', '创建项目', RUN_PROJECT_NAME, async (payload) => {
    rootProjectId = await recordRunRootId(resultsDir, payload);
  });
  await testWorkspaceRequestControls(page);
  await clickAction(currentResource(page, RUN_PROJECT_NAME));
  await expect(page.getByLabel('所选资源操作')).toBeVisible();
  expect(new URL(page.url()).searchParams.get('project')).toBe(rootProjectId);
  await testArchiveRestore(page);

  await createResource(page, '新建文件夹', '创建文件夹', RUN_FOLDER_NAME);
  await clickAction(currentResource(page, RUN_FOLDER_NAME));
  const folderId = new URL(page.url()).searchParams.get('parent');
  expect(folderId).toMatch(/^[0-9a-f-]{36}$/i);
  await createResource(page, '新建问卷', '创建问卷', RUN_SURVEY_NAME);
  await expect(page).toHaveURL(/\/surveys\/[0-9a-f-]{36}\/edit/);
  return { rootProjectId, folderId: folderId!, surveyId: surveyIdFrom(page.url()) };
}

async function testWorkspaceRequestControls(page: Page) {
  await fillAction(page.getByLabel('搜索资源'), '归档恢复');
  await expect.poll(() => new URL(page.url()).searchParams.get('query')).toBe('归档恢复');
  await page.getByLabel('资源类型').selectOption('folder');
  await expect.poll(() => new URL(page.url()).searchParams.get('kind')).toBe('folder');
  const responsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === '/v1/resources'
      && url.searchParams.get('query') === '归档恢复'
      && url.searchParams.get('kind') === 'folder'
      && url.searchParams.get('archived') === 'active'
      && url.searchParams.get('sort') === 'name_asc';
  });
  await page.getByLabel('排序方式').selectOption('name_asc');
  expect((await responsePromise).status()).toBe(200);
  const url = new URL(page.url());
  expect(url.searchParams.get('query')).toBe('归档恢复');
  expect(url.searchParams.get('kind')).toBe('folder');
  expect(url.searchParams.get('archived')).toBe('active');
  expect(url.searchParams.get('sort')).toBe('name_asc');

  await navigateWithinApp(page, '/workspace');
  expect(new URL(page.url()).search).toBe('');
  await expect(page.getByLabel('搜索资源')).toHaveValue('');
  await expect(page.getByLabel('资源类型')).toHaveValue('');
  await expect(page.getByLabel('排序方式')).toHaveValue('updated_desc');
  await expect(currentResource(page, RUN_PROJECT_NAME)).toBeVisible();
}

async function testArchiveRestore(page: Page) {
  const name = '归档恢复验收';
  const projectId = new URL(page.url()).searchParams.get('project');
  expect(projectId).toMatch(/^[0-9a-f-]{36}$/i);
  await createResource(page, '新建文件夹', '创建文件夹', name);
  await clickAction(currentResource(page, name));
  await clickAction(page.getByRole('button', { name: '归档', exact: true }));
  const archiveDialog = page.getByRole('dialog', { name: '归档资源' });
  await expectFocusedWithVisibleOutline(archiveDialog.getByRole('button', { name: '取消' }));
  await clickAction(archiveDialog.getByRole('button', { name: '确认归档' }));
  await expect(archiveDialog).toBeHidden();

  await page.getByLabel('资源状态').selectOption('archived');
  await clickAction(currentResource(page, name));
  await clickAction(page.getByRole('button', { name: '恢复', exact: true }));
  const restoreDialog = page.getByRole('dialog', { name: '恢复资源' });
  await expectFocusedWithVisibleOutline(restoreDialog.getByRole('button', { name: '取消' }));
  await clickAction(restoreDialog.getByRole('button', { name: '确认恢复' }));
  await expect(restoreDialog).toBeHidden();
  await navigateWithinApp(page, `/workspace?project=${projectId}&parent=${projectId}`);
  await expect(currentResource(page, name)).toBeVisible();
}

function currentResource(page: Page, name: string) {
  return page.getByRole('list', { name: '当前位置资源' })
    .getByRole('button', { name, exact: true });
}

async function dragWithPointer(page: Page, source: Locator, target: Locator) {
  await expectActionable(source);
  await expect(target).toBeVisible({ timeout: ACTION_TIMEOUT_MS });
  await source.scrollIntoViewIfNeeded();
  await target.scrollIntoViewIfNeeded();
  const sourceBox = await source.boundingBox();
  const targetBox = await target.boundingBox();
  expect(sourceBox).not.toBeNull();
  expect(targetBox).not.toBeNull();

  const start = {
    x: sourceBox!.x + sourceBox!.width / 2,
    y: sourceBox!.y + sourceBox!.height / 2,
  };
  const end = {
    x: targetBox!.x + targetBox!.width / 2,
    y: targetBox!.y + targetBox!.height / 2,
  };
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  await page.mouse.move(start.x + 8, start.y, { steps: 2 });
  await page.mouse.move(end.x, end.y, { steps: 12 });
  await page.mouse.up();
  await page.waitForTimeout(75);
}

async function saveAndReloadDraft(page: Page, surveyId: string) {
  const saveResponse = page.waitForResponse((response) =>
    response.url().includes(`/v1/surveys/${surveyId}/draft`)
      && response.request().method() === 'PUT',
  );
  await clickAction(page.getByRole('button', { name: '保存草稿' }));
  expect((await saveResponse).status()).toBe(200);
  await expect(page.getByText(/已保存版本 \d+/)).toBeVisible();
  await page.reload();
  await login(page, await readToken(), await readMetadata());
  await navigateWithinApp(page, `/surveys/${surveyId}/edit`);
  await expect(page).toHaveURL(new RegExp(`/surveys/${surveyId}/edit$`));
  await expect(page.getByRole('heading', { name: '问卷大纲' })).toBeVisible();
}

async function expectOutlineOrder(page: Page, expected: OutlineGroupOrder[]) {
  await expect.poll(() => readOutlineOrder(page)).toEqual(expected);
}

async function readOutlineOrder(page: Page): Promise<OutlineGroupOrder[]> {
  return page.locator('.editor-outline-group').evaluateAll((groups) =>
    groups.map((group) => ({
      title: group.querySelector('h3')?.textContent?.trim() ?? '',
      questions: [...group.querySelectorAll<HTMLButtonElement>('.editor-outline-question > span')]
        .map((question) => question.textContent?.trim() ?? ''),
    })),
  );
}

async function assertTouchTarget(locator: Locator) {
  await expect(locator).toBeVisible();
  await expect(locator).toBeInViewport();
  const box = await locator.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.width).toBeGreaterThanOrEqual(44);
  expect(box!.height).toBeGreaterThanOrEqual(44);
}

async function expectNoHorizontalOverflow(page: Page) {
  await expect.poll(() => page.evaluate(
    () => document.documentElement.scrollWidth <= window.innerWidth,
  )).toBe(true);
}

async function expectFocusedWithVisibleOutline(locator: Locator) {
  await expect(locator).toBeFocused();
  expect(await locator.evaluate((element) => {
    const style = getComputedStyle(element);
    return style.outlineStyle !== 'none' && style.outlineWidth !== '0px';
  })).toBe(true);
}

function sortStatus(page: Page) {
  return page.locator('.editor-outline > [role="status"][aria-live="polite"]');
}

async function createResource(
  page: Page,
  trigger: string,
  submit: string,
  value: string,
  onCreated?: (payload: unknown) => Promise<void>,
) {
  await openCreateDialog(page, trigger);
  const dialog = page.getByRole('dialog', { name: trigger });
  await fillAction(dialog.getByRole('textbox'), value);
  const endpoint = createResourceEndpoint(trigger);
  const createResponse = page.waitForResponse((response) =>
    new URL(response.url()).pathname === endpoint
      && response.request().method() === 'POST',
  );
  await clickAction(dialog.getByRole('button', { name: submit }));
  const response = await createResponse;
  expect(response.ok()).toBe(true);
  const payload: unknown = await response.json();
  await onCreated?.(payload);
  await expect(dialog).toBeHidden();
}

async function openCreateDialog(page: Page, trigger: string) {
  await clickAction(page.getByRole('button', { name: '新建', exact: true }));
  await clickAction(page.getByRole('menuitem', { name: trigger }));
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
    await expect(page).toHaveURL(/\/dashboard$/);
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
    || typeof payload.rootProjectId !== 'string'
    || !/^[0-9a-f-]{36}$/i.test(payload.rootProjectId)
    || typeof payload.folderId !== 'string'
    || !/^[0-9a-f-]{36}$/i.test(payload.folderId)
    || typeof payload.surveyId !== 'string'
    || !/^[0-9a-f-]{36}$/i.test(payload.surveyId)
    || typeof payload.version !== 'number'
    || !Number.isInteger(payload.version)
    || payload.version < 1) {
    throw new Error('ADMIN_WEB_RESULT_FILE does not contain a completed desktop journey');
  }
  return {
    rootProjectId: payload.rootProjectId,
    folderId: payload.folderId,
    surveyId: payload.surveyId,
    version: payload.version,
  };
}

async function writeResult(result: Record<string, unknown>, token: string) {
  assertArtifactContainsNoSecret(result, token);
  const path = requiredEnv('ADMIN_WEB_RESULT_FILE');
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, `${JSON.stringify(result, null, 2)}\n`, { mode: 0o600 });
}

async function captureEvidenceScreenshot(page: Page, filename: string) {
  const outputDir = process.env.ADMIN_WEB_SCREENSHOT_DIR;
  if (!outputDir) return;
  await mkdir(outputDir, { recursive: true });
  await page.screenshot({ path: join(outputDir, filename), fullPage: true });
}

function networkRecord(response: Response, token: string): NetworkRecord {
  const url = new URL(response.url());
  return {
    method: response.request().method(),
    status: response.status(),
    url: redactSecret(`${url.origin}${url.pathname}${url.search}`, token),
  };
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
