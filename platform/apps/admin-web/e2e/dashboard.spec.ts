import { expect, test, type Browser, type Locator, type Page } from '@playwright/test';
import { execFile } from 'node:child_process';
import { mkdir, readFile, stat, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { redactSensitiveInputs } from './redaction';

interface TestMetadata {
  actorId: string;
  tenantId: string;
}

interface JourneyResult {
  surveyId: string;
}

const REVIEWER_ACTOR = 'admin-web-e2e-reviewer';
const EDITOR_ACTOR = 'admin-web-e2e-editor';
const DATA_ACTOR = 'admin-web-e2e-data';
const UPDATED_SURVEY_NAME = '管理端工作台角色验收问卷';
const VIEWPORTS = [819, 820, 1179, 1180] as const;

test.describe.configure({ mode: 'serial' });

test('login lands on dashboard and real actions preserve recent work across reload', async ({ page }) => {
  const metadata = await readMetadata();
  const result = await readResult();
  await login(page, await readPrivateToken('ADMIN_WEB_JWT_FILE'), metadata.actorId, metadata.tenantId);

  const surveyRow = page.getByRole('table', { name: '问卷运行情况' }).locator('tr').filter({
    has: page.locator(`a[href="/surveys/${result.surveyId}/publish"]`),
  });
  await expect(surveyRow).toBeVisible();
  await surveyRow.getByRole('link', { name: '发布与版本' }).click();
  await expect(page).toHaveURL(new RegExp(`/surveys/${result.surveyId}/publish$`));
  await expect(
    page
      .getByRole('navigation', { name: '问卷工作流' })
      .getByRole('link', { name: '发布与版本' }),
  ).toHaveAttribute('aria-current', 'page');

  await page.getByRole('link', { name: '工作台', exact: true }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.getByRole('link', { name: '继续发布' })).toBeVisible();

  const currentSurveyRow = page.getByRole('table', { name: '问卷运行情况' }).locator('tr').filter({
    has: page.locator(`a[href="/surveys/${result.surveyId}/responses"]`),
  });
  await currentSurveyRow.getByRole('link', { name: '答卷与导出' }).click();
  await expect(page).toHaveURL(new RegExp(`/surveys/${result.surveyId}/responses$`));
  await expect(page.getByRole('heading', { name: '答卷与导出' })).toBeVisible();
  await expect(page.getByLabel('已完成 1')).toBeVisible();

  await page.getByRole('link', { name: '工作台', exact: true }).click();
  await expect(page.getByRole('link', { name: '继续查看答卷' })).toBeVisible();
  await page.reload();
  await expect(page).toHaveURL(/\/dev\/token$/);
  await login(page, await readPrivateToken('ADMIN_WEB_JWT_FILE'), metadata.actorId, metadata.tenantId);
  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.getByRole('link', { name: '继续发布' })).toBeVisible();
  await expect(page.getByRole('link', { name: '继续查看答卷' })).toBeVisible();
});

test('pending approval is visible only to a real publish reviewer', async ({ browser }) => {
  const metadata = await readMetadata();
  const result = await readResult();
  await seedScopedActors(metadata.tenantId, result.surveyId);

  const editor = await rolePage(browser, 'ADMIN_WEB_EDITOR_JWT_FILE');
  try {
    await login(editor, await readPrivateToken('ADMIN_WEB_EDITOR_JWT_FILE'), EDITOR_ACTOR, metadata.tenantId);
    const editorSurveyRow = editor.getByRole('table', { name: '问卷运行情况' }).locator('tr').filter({
      has: editor.locator(`a[href="/surveys/${result.surveyId}/edit"]`),
    });
    await expect(editorSurveyRow).toBeVisible();
    await editorSurveyRow.getByRole('link', { name: '编辑' }).click();
    await expect(editor.getByRole('heading', { name: /管理端产品对齐验收问卷/ })).toBeVisible();
    await editor.getByLabel('标题').fill(UPDATED_SURVEY_NAME);
    const save = editor.waitForResponse((response) =>
      response.url().includes(`/v1/surveys/${result.surveyId}/draft`)
        && response.request().method() === 'PUT',
    );
    await editor.getByRole('button', { name: '保存草稿' }).click();
    expect((await save).status()).toBe(200);
    await editor.getByRole('link', { name: '发布与版本' }).click();
    await editor.getByRole('button', { name: '提交审批' }).click();
    await expect(editor.getByText('待审批', { exact: true }).first()).toBeVisible();
    await editor.getByRole('link', { name: '工作台', exact: true }).click();
    await expect(editor.getByText('待我审批', { exact: true })).toHaveCount(0);
    await expect(editor.getByRole('link', { name: '查看审批' })).toHaveCount(0);
  } finally {
    await editor.context().close();
  }

  const reviewer = await rolePage(browser, 'ADMIN_WEB_REVIEWER_JWT_FILE');
  try {
    await login(reviewer, await readPrivateToken('ADMIN_WEB_REVIEWER_JWT_FILE'), REVIEWER_ACTOR, metadata.tenantId);
    const approvalSummary = reviewer.locator('.dashboard-summary__item').filter({ hasText: '待我审批' });
    await expect(approvalSummary).toContainText('1');
    const approval = reviewer.getByRole('link', { name: '查看审批' });
    await expect(approval).toBeVisible();
    await approval.click();
    await expect(reviewer).toHaveURL(new RegExp(`/surveys/${result.surveyId}/publish$`));
    await expect(reviewer.getByRole('button', { name: '批准申请' })).toBeVisible();
  } finally {
    await reviewer.context().close();
  }
});

test('real data role sees only authorized response and export facts and actions', async ({ browser }) => {
  const metadata = await readMetadata();
  const result = await readResult();
  await seedScopedActors(metadata.tenantId, result.surveyId);

  const data = await rolePage(browser, 'ADMIN_WEB_DATA_JWT_FILE');
  try {
    await login(data, await readPrivateToken('ADMIN_WEB_DATA_JWT_FILE'), DATA_ACTOR, metadata.tenantId);
    await waitForDashboardStable(data);
    const summary = data.getByRole('definition');
    await expect(summary).toHaveCount(1);
    await expect(data.getByText('处理中导出', { exact: true })).toBeVisible();
    await expect(data.locator('.dashboard-summary__item').filter({ hasText: '处理中导出' })).toContainText('1');
    await expect(data.getByText('待我审批', { exact: true })).toHaveCount(0);
    await expect(data.getByText('发布异常', { exact: true })).toHaveCount(0);
    await expect(data.getByText('运行中预览', { exact: true })).toHaveCount(0);

    const exportTask = data.getByRole('link', { name: '查看导出' });
    await expect(exportTask).toBeVisible();
    await expect(data.getByRole('link', { name: '查看审批' })).toHaveCount(0);
    await expect(data.getByRole('link', { name: '查看发布' })).toHaveCount(0);
    await expect(data.getByRole('link', { name: '查看预览' })).toHaveCount(0);

    const surveyRow = data.getByRole('table', { name: '问卷运行情况' }).locator('tr').filter({
      has: data.locator(`a[href="/surveys/${result.surveyId}/responses"]`),
    });
    await expect(surveyRow).toContainText('1 份');
    await expect(surveyRow.getByRole('link', { name: '答卷与导出' })).toBeVisible();
    await expect(surveyRow.getByRole('link', { name: '编辑' })).toHaveCount(0);
    await expect(surveyRow.getByRole('link', { name: '真实预览' })).toHaveCount(0);
    await expect(surveyRow.getByRole('link', { name: '发布与版本' })).toHaveCount(0);

    await exportTask.click();
    await expect(data).toHaveURL(new RegExp(`/surveys/${result.surveyId}/responses$`));
    await expect(data.getByRole('heading', { name: '答卷与导出' })).toBeVisible();
    await expect(data.getByLabel('已完成 1')).toBeVisible();
  } finally {
    await data.context().close();
  }
});

test('dashboard breakpoints and mobile navigation keep primary actions usable', async ({ page }) => {
  const metadata = await readMetadata();
  await login(page, await readPrivateToken('ADMIN_WEB_JWT_FILE'), metadata.actorId, metadata.tenantId);
  const evidenceDir = join(requiredEnv('ADMIN_WEB_TEST_RESULTS_DIR'), 'dashboard-evidence');
  await mkdir(evidenceDir, { recursive: true });

  const screenshots: Array<{
    bottomEvidence: '最近工作';
    documentHeight: number;
    filename: string;
    imageHeight: number;
    imageWidth: number;
    path: '/dashboard';
    viewportHeight: number;
    viewportWidth: number;
  }> = [];
  for (const width of VIEWPORTS) {
    const viewportHeight = 900;
    await page.setViewportSize({ width, height: viewportHeight });
    await waitForDashboardStable(page);
    await assertDashboardGeometry(page, width);

    if (width === 819) {
      const toggle = page.getByRole('button', { name: '打开主导航' });
      await expectTouchTarget(toggle);
      await toggle.click();
      const navigation = page.getByRole('navigation', { name: '全局导航' });
      await expect(navigation).toBeVisible();
      await expect(navigation.getByRole('link', { name: '项目与问卷' })).toBeInViewport();
      await assertNoPageOverflow(page);
      await navigation.getByRole('link', { name: '项目与问卷' }).click();
      await expect(page).toHaveURL(/\/workspace$/);
      await page.getByRole('button', { name: '打开主导航' }).click();
      await page.getByRole('navigation', { name: '全局导航' })
        .getByRole('link', { name: '工作台', exact: true }).click();
      await expect(page).toHaveURL(/\/dashboard$/);
      await waitForDashboardStable(page);
      await exerciseMobileDashboardActions(page);
    } else {
      await expect(page.getByRole('button', { name: '打开主导航' })).toBeHidden();
    }

    await waitForDashboardStable(page);
    await redactSensitiveInputs(page);
    const filename = `dashboard-${width}.png`;
    const screenshotPath = join(evidenceDir, filename);
    const documentHeight = await page.evaluate(() => document.documentElement.scrollHeight);
    await page.screenshot({ path: screenshotPath, fullPage: true });
    const { width: imageWidth, height: imageHeight } = pngDimensions(await readFile(screenshotPath));
    expect(imageWidth).toBe(width);
    expect(imageHeight).toBeGreaterThanOrEqual(documentHeight);
    screenshots.push({
      bottomEvidence: '最近工作',
      documentHeight,
      filename,
      imageHeight,
      imageWidth,
      path: '/dashboard',
      viewportHeight,
      viewportWidth: width,
    });
  }

  await writeFile(join(evidenceDir, 'dashboard-screenshot-manifest.json'), `${JSON.stringify({
    schemaVersion: 1,
    kind: 'sanitized-dashboard-screenshot-set',
    project: 'chromium-dashboard',
    screenshots,
  }, null, 2)}\n`, { mode: 0o600 });
});

async function login(page: Page, token: string, actorId: string, tenantId: string) {
  await page.goto('/dev/token');
  const meResponse = page.waitForResponse((response) =>
    new URL(response.url()).pathname === '/v1/me' && response.request().method() === 'GET',
  );
  const input = page.getByLabel('测试令牌');
  try {
    await input.fill(token);
    await page.getByRole('button', { name: '登录' }).click();
    expect((await meResponse).status()).toBe(200);
    await expect(page).toHaveURL(/\/dashboard$/);
    await expect(page.getByRole('heading', { name: '工作台' })).toBeVisible();
    await expect(page.locator('.account-menu > summary')).toContainText(actorId);
    await expect(page.getByText(`当前租户：${tenantId}`)).toBeVisible();
  } finally {
    await redactSensitiveInputs(page).catch(() => undefined);
  }
}

async function rolePage(browser: Browser, tokenEnv: string) {
  requiredEnv(tokenEnv);
  const context = await browser.newContext({ viewport: { width: 1180, height: 900 } });
  return context.newPage();
}

async function seedScopedActors(tenantId: string, surveyId: string) {
  assertUuid(tenantId);
  assertUuid(surveyId);
  const reviewerGrant = randomUUID();
  const editorGrant = randomUUID();
  const dataGrant = randomUUID();
  const queuedExport = randomUUID();
  const failedExport = randomUUID();
  const sql = `BEGIN;
SELECT set_config('app.tenant_id', '${tenantId}', true);
INSERT INTO access_member (tenant_id, actor_id, status, uses_seat, invited_by)
VALUES ('${tenantId}', '${REVIEWER_ACTOR}', 'active', true, 'admin-web-e2e-owner'),
       ('${tenantId}', '${EDITOR_ACTOR}', 'active', true, 'admin-web-e2e-owner'),
       ('${tenantId}', '${DATA_ACTOR}', 'active', true, 'admin-web-e2e-owner')
ON CONFLICT (tenant_id, actor_id) DO UPDATE SET status = 'active', updated_at = now();
WITH RECURSIVE ancestors AS (
  SELECT id, parent_id FROM access_resource WHERE tenant_id = '${tenantId}' AND id = '${surveyId}'
  UNION ALL
  SELECT parent.id, parent.parent_id
  FROM access_resource parent
  JOIN ancestors child ON child.parent_id = parent.id
  WHERE parent.tenant_id = '${tenantId}'
), root_project AS (
  SELECT id FROM ancestors WHERE parent_id IS NULL
)
INSERT INTO access_grant (tenant_id, id, actor_id, role_code, resource_id, granted_by)
SELECT '${tenantId}'::uuid, '${reviewerGrant}'::uuid, '${REVIEWER_ACTOR}', 'publish_reviewer', id, 'admin-web-e2e-owner'
FROM root_project
UNION ALL
SELECT '${tenantId}'::uuid, '${editorGrant}'::uuid, '${EDITOR_ACTOR}', 'editor', id, 'admin-web-e2e-owner'
FROM root_project
UNION ALL
SELECT '${tenantId}'::uuid, '${dataGrant}'::uuid, '${DATA_ACTOR}', 'raw_data_viewer', id, 'admin-web-e2e-owner'
FROM root_project
ON CONFLICT (tenant_id, actor_id, role_code, resource_id) DO NOTHING;
DELETE FROM response_export_job
WHERE tenant_id = '${tenantId}'::uuid AND requested_by = '${DATA_ACTOR}';
INSERT INTO response_export_job
    (tenant_id, id, survey_id, requested_by, format, filter_snapshot, plan,
     reveal_sensitive, batch_size, status, next_attempt_at, expires_at, finished_at)
VALUES ('${tenantId}'::uuid, '${queuedExport}'::uuid, '${surveyId}'::uuid, '${DATA_ACTOR}', 'csv',
        '{}'::jsonb, '{}'::jsonb, false, 100, 'queued', now() + interval '1 hour',
        now() + interval '2 hours', NULL),
       ('${tenantId}'::uuid, '${failedExport}'::uuid, '${surveyId}'::uuid, '${DATA_ACTOR}', 'csv',
        '{}'::jsonb, '{}'::jsonb, false, 100, 'failed', now(),
        now() + interval '2 hours', now())
ON CONFLICT (tenant_id, id) DO NOTHING;
COMMIT;`;
  await run('docker', [
    'exec', requiredEnv('ADMIN_WEB_PLATFORM_DB_CONTAINER'),
    'psql', '-U', 'platform_owner', '-d', 'platform', '-v', 'ON_ERROR_STOP=1', '-q', '-c', sql,
  ]);
}

async function assertDashboardGeometry(page: Page, width: number) {
  await assertNoPageOverflow(page);
  const geometry = await page.evaluate(() => {
    const visible = (element: Element) => {
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
    };
    const overlaps = (left: DOMRect, right: DOMRect) =>
      Math.min(left.right, right.right) - Math.max(left.left, right.left) > 1
      && Math.min(left.bottom, right.bottom) - Math.max(left.top, right.top) > 1;
    const problems: string[] = [];
    const actions = [...document.querySelectorAll<HTMLElement>(
      '.dashboard-primary-link, .dashboard-actions a, .dashboard-recent-list a, .dashboard-refresh button',
    )].filter(visible);
    for (const action of actions) {
      const rect = action.getBoundingClientRect();
      if (rect.left < 0 || rect.right > window.innerWidth || rect.top < 0) {
        problems.push(`clipped action: ${action.textContent?.trim() || action.getAttribute('aria-label')}`);
      }
    }
    const groups = [
      ...document.querySelectorAll<HTMLElement>(
        '.dashboard-table tbody tr, .dashboard-section-heading, .dashboard-recent-list li',
      ),
    ];
    for (const group of groups) {
      const children = [...group.children].filter(visible);
      for (let left = 0; left < children.length; left += 1) {
        for (let right = left + 1; right < children.length; right += 1) {
          if (overlaps(children[left].getBoundingClientRect(), children[right].getBoundingClientRect())) {
            const leftRect = children[left].getBoundingClientRect();
            const rightRect = children[right].getBoundingClientRect();
            problems.push(JSON.stringify({
              group: group.className || group.tagName,
              left: children[left].textContent?.trim(),
              leftRect: { left: leftRect.left, right: leftRect.right, top: leftRect.top, bottom: leftRect.bottom },
              right: children[right].textContent?.trim(),
              rightRect: { left: rightRect.left, right: rightRect.right, top: rightRect.top, bottom: rightRect.bottom },
            }));
          }
        }
      }
    }
    return { actionCount: actions.length, problems };
  });
  expect(geometry.actionCount).toBeGreaterThan(0);
  expect(geometry.problems, `dashboard geometry at ${width}px`).toEqual([]);
}

async function assertNoPageOverflow(page: Page) {
  await expect.poll(() => page.evaluate(
    () => document.documentElement.scrollWidth <= window.innerWidth,
  )).toBe(true);
}

async function expectTouchTarget(locator: Locator) {
  await expect(locator).toBeVisible();
  const box = await locator.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.width).toBeGreaterThanOrEqual(44);
  expect(box!.height).toBeGreaterThanOrEqual(44);
}

async function waitForDashboardStable(page: Page) {
  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.getByRole('heading', { name: '工作台' })).toBeVisible();
  await expect(page.getByRole('status', { name: '正在加载工作台' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '刷新工作台' })).toBeEnabled();
  for (const heading of ['业务摘要', '需要处理', '问卷运行情况', '最近工作']) {
    await expect(page.getByRole('heading', { name: heading })).toBeAttached();
  }
  const bottom = page.getByRole('heading', { name: '最近工作' });
  await bottom.scrollIntoViewIfNeeded();
  await expect(bottom).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
}

async function exerciseMobileDashboardActions(page: Page) {
  const refresh = page.getByRole('button', { name: '刷新工作台' });
  await expectTouchTarget(refresh);
  const refreshed = page.waitForResponse((response) =>
    new URL(response.url()).pathname === '/v1/dashboard' && response.request().method() === 'GET',
  );
  await refresh.click();
  expect((await refreshed).status()).toBe(200);
  await waitForDashboardStable(page);

  for (const action of [
    page.locator('.dashboard-task-table .dashboard-primary-link').first(),
    page.locator('.dashboard-survey-table .dashboard-actions a').first(),
    page.locator('.dashboard-recent-list a').first(),
  ]) {
    await expectTouchTarget(action);
    const href = await action.getAttribute('href');
    expect(href).toMatch(/^\/surveys\/[0-9a-f-]{36}\/(?:edit|import|preview|publish|responses|versions\/\d+)$/i);
    await action.click();
    await expect(page).toHaveURL(new RegExp(`${href!.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`));
    const mobileNavigation = page.getByRole('button', { name: '打开主导航' });
    if (await mobileNavigation.isVisible()) {
      await mobileNavigation.click();
      await page.getByRole('navigation', { name: '全局导航' })
        .getByRole('link', { name: '工作台', exact: true }).click();
    } else {
      await page.getByRole('link', { name: '工作台', exact: true }).click();
    }
    await waitForDashboardStable(page);
  }
}

function pngDimensions(content: Buffer) {
  if (content.length < 24 || content.toString('ascii', 12, 16) !== 'IHDR') {
    throw new Error('Screenshot is not a PNG with an IHDR');
  }
  return { width: content.readUInt32BE(16), height: content.readUInt32BE(20) };
}

async function readPrivateToken(name: string) {
  const path = requiredEnv(name);
  if (((await stat(path)).mode & 0o777) !== 0o600) throw new Error(`${name} must use mode 0600`);
  const token = (await readFile(path, 'utf8')).trim();
  if (!token) throw new Error(`${name} is empty`);
  return token;
}

async function readMetadata(): Promise<TestMetadata> {
  return JSON.parse(await readFile(requiredEnv('ADMIN_WEB_METADATA_FILE'), 'utf8')) as TestMetadata;
}

async function readResult(): Promise<JourneyResult> {
  return JSON.parse(await readFile(requiredEnv('ADMIN_WEB_RESULT_FILE'), 'utf8')) as JourneyResult;
}

function assertUuid(value: string) {
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value)) {
    throw new Error('Expected a canonical UUID');
  }
}

function run(file: string, args: string[]) {
  return new Promise<void>((resolve, reject) => {
    execFile(file, args, (error) => error ? reject(error) : resolve());
  });
}

function requiredEnv(name: string) {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required`);
  return value;
}
