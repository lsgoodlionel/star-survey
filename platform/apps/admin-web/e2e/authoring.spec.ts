import { expect, test, type Locator, type Page, type Response, type TestInfo } from '@playwright/test';
import { mkdir, readFile, stat, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
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

test('author filters stable resources, restores archive, and publishes the same survey', async ({ page }) => {
  const metadata = await readMetadata();
  const token = await readToken();
  const network: NetworkRecord[] = [];
  page.on('response', (response) => network.push(networkRecord(response, token)));

  const me = await login(page, token, metadata);

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

  const previewPagePromise = page.context().waitForEvent('page');
  await clickAction(page.getByRole('button', { name: '创建真实预览' }));
  await expect(page.getByText('真实预览已就绪')).toBeVisible({ timeout: 210_000 });
  await captureEvidenceScreenshot(page, 'real-preview-desktop.png');
  await clickAction(page.getByRole('button', { name: '在新窗口打开真实预览' }));
  const previewPage = await previewPagePromise;
  await completeLimeSurvey(previewPage);
  await previewPage.close();
  await clickAction(page.getByRole('button', { name: '结束真实预览' }));
  await expect(page.getByText('真实预览已结束')).toBeVisible({ timeout: 210_000 });

  await clickAction(page.getByRole('navigation', { name: '问卷工作流' }).getByRole('link', {
    name: '答卷与导出',
  }));
  await expect(page.getByRole('heading', { name: '答卷与导出' })).toBeVisible();
  await expect(page.getByLabel('已完成 0')).toBeVisible();

  await clickAction(page.getByRole('link', { name: '发布与版本' }));
  await clickAction(page.getByRole('button', { name: '提交审批' }));
  await expect(page.getByRole('button', { name: '批准申请' })).toBeVisible();
  await clickAction(page.getByRole('button', { name: '批准申请' }));
  await expect(page.getByRole('button', { name: '发布问卷' })).toBeVisible();
  await clickAction(page.getByRole('button', { name: '发布问卷' }));
  await expect(page.getByText('发布成功')).toBeVisible({ timeout: 210_000 });

  await fillAction(page.getByLabel('链接名称'), '正式验收链接');
  await clickAction(page.getByRole('button', { name: '创建链接' }));
  const deliveryList = page.getByRole('list', { name: '投放链接' });
  const deliveryLink = deliveryList.getByRole('link').first();
  await expect(deliveryLink).toBeVisible();
  await expect(deliveryList.getByRole('img', { name: '正式验收链接二维码' })).toBeVisible();
  const deliveryUrl = await deliveryLink.getAttribute('href');
  expect(deliveryUrl).toMatch(/^http:\/\/127\.0\.0\.1:/);

  const respondentPage = await page.context().newPage();
  await respondentPage.goto(deliveryUrl!);
  await completeLimeSurvey(respondentPage);
  await respondentPage.close();

  await expect.poll(async () => {
    const response = await page.request.get(
      `${requiredEnv('ADMIN_WEB_BASE_URL')}/v1/surveys/${surveyId}/responses/summary`,
      { headers: { Authorization: `Bearer ${token}` } },
    );
    if (!response.ok()) return -1;
    const payload = await response.json() as { total?: { engineCompleted?: number } };
    return payload.total?.engineCompleted ?? -1;
  }, {
    timeout: 60_000,
    intervals: [1_000, 2_000, 5_000],
  }).toBe(1);
  // The app intentionally keeps server queries fresh for 30 seconds. Re-enter
  // the page after that window so the user journey exercises a real refetch.
  await page.waitForTimeout(30_500);
  await clickAction(page.getByRole('navigation', { name: '问卷工作流' }).getByRole('link', {
    name: '答卷与导出',
  }));
  await expect(page.getByRole('heading', { name: '答卷与导出' })).toBeVisible();
  await expect(page.getByLabel('已完成 1')).toBeVisible();
  await expect(page.getByRole('table')).toContainText('Q9');
  await expect(page.getByRole('table')).toContainText('A1');
  await clickAction(page.getByRole('button', { name: '创建导出任务' }));
  const exportJob = page.locator('.export-job');
  await expect(exportJob).toContainText('导出完成', { timeout: 60_000 });
  const downloadPromise = page.waitForEvent('download');
  await clickAction(page.getByRole('button', { name: '下载导出文件' }));
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/\.(?:csv|zip)$/i);

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

  for (const width of [768, 819, 820, 1024, 1440]) {
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
  const create = page.getByRole('button', { name: '新建' });
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
    await assertUsable(panel);
    await assertUsable(control);
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
  await expect(page.getByRole('heading', { name: '资源工作台' })).toBeVisible();
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
