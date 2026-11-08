import { expect, test, type Browser, type Locator, type Page } from '@playwright/test';
import { execFile, execFileSync } from 'node:child_process';
import { mkdir, readFile, stat, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { responsePageSchema, responseSummarySchema } from '../src/shared/api/responses';
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
  await expect(page.getByRole('link', { name: '继续发布' }).and(page.locator(`a[href="/surveys/${result.surveyId}/publish"]`))).toBeVisible();

  const currentSurveyRow = page.getByRole('table', { name: '问卷运行情况' }).locator('tr').filter({
    has: page.locator(`a[href="/surveys/${result.surveyId}/responses"]`),
  });
  await currentSurveyRow.getByRole('link', { name: '答卷与导出' }).click();
  await expect(page).toHaveURL(new RegExp(`/surveys/${result.surveyId}/responses$`));
  await expect(page.getByRole('heading', { name: '答卷与导出' })).toBeVisible();
  await expect(page.getByLabel('已完成 1')).toBeVisible();

  await page.getByRole('link', { name: '工作台', exact: true }).click();
  await expect(page.getByRole('link', { name: '继续查看答卷' }).and(page.locator(`a[href="/surveys/${result.surveyId}/responses"]`))).toBeVisible();
  await page.reload();
  await expect(page).toHaveURL(/\/dev\/token$/);
  await login(page, await readPrivateToken('ADMIN_WEB_JWT_FILE'), metadata.actorId, metadata.tenantId);
  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.getByRole('link', { name: '继续发布' }).and(page.locator(`a[href="/surveys/${result.surveyId}/publish"]`))).toBeVisible();
  await expect(page.getByRole('link', { name: '继续查看答卷' }).and(page.locator(`a[href="/surveys/${result.surveyId}/responses"]`))).toBeVisible();
});

test('short links fail identically after revocation or expiry and reject unauthorized changes', async ({ page }) => {
  const metadata = await readMetadata();
  const result = await readResult();
  await seedScopedActors(metadata.tenantId, result.surveyId);
  const owner = await readPrivateToken('ADMIN_WEB_JWT_FILE');
  const data = await readPrivateToken('ADMIN_WEB_DATA_JWT_FILE');
  const base = requiredEnv('ADMIN_WEB_BASE_URL');
  const create = async (label: string) => {
    const response = await page.request.post(`${base}/v1/delivery/surveys/${result.surveyId}/links`, {
      headers: { Authorization: `Bearer ${owner}` }, data: { label, params: {}, ttlSeconds: 3600, shortLink: true },
    });
    expect(response.status()).toBe(201);
    const link = await response.json() as { id: string; shortUrl: string; url: string };
    assertUuid(link.id);
    expect(link.shortUrl).toBeTruthy();
    return link;
  };
  const revoked = await create('synthetic revoked');
  const expired = await create('synthetic expired');
  const valid = await create('synthetic valid');
  const shortPath = (link: { shortUrl: string }) => new URL(link.shortUrl).pathname;
  const live = await page.request.get(`${base}${shortPath(valid)}`, { maxRedirects: 0 });
  expect(live.status()).toBe(302);
  expect(live.headers().location).toBe(valid.url);
  expect(live.headers()['cache-control']).toBe('no-store');
  const forbidden = await page.request.delete(`${base}/v1/delivery/links/${revoked.id}`, {
    headers: { Authorization: `Bearer ${data}` },
  });
  expect(forbidden.status()).toBe(403);
  expect((await page.request.get(`${base}${shortPath(revoked)}`, { maxRedirects: 0 })).status()).toBe(302);
  const done = await page.request.delete(`${base}/v1/delivery/links/${revoked.id}`, {
    headers: { Authorization: `Bearer ${owner}` },
  });
  expect(done.status()).toBe(200);
  assertUuid(metadata.tenantId);
  await run('docker', ['exec', requiredEnv('ADMIN_WEB_PLATFORM_DB_CONTAINER'), 'psql', '-U', 'platform_owner',
    '-d', 'platform', '-v', 'ON_ERROR_STOP=1', '-q', '-c', `BEGIN;
SELECT set_config('app.tenant_id', '${metadata.tenantId}', true);
UPDATE delivery_link SET expires_at = now() - interval '1 minute' WHERE id = '${expired.id}';
COMMIT;`]);
  const unknownPath = `${shortPath(valid).slice(0, -1)}${shortPath(valid).endsWith('2') ? '3' : '2'}`;
  const failures = await Promise.all([shortPath(revoked), shortPath(expired), unknownPath].map(path =>
    page.request.get(`${base}${path}`, { maxRedirects: 0 })));
  for (const failure of failures) expect(failure.status()).toBe(404);
  const bodies = await Promise.all(failures.map(failure => failure.text()));
  expect(bodies[0]).toEqual(bodies[1]);
  expect(bodies[1]).toEqual(bodies[2]);
});

test('tenant owner can view the current subscription in the real stack', async ({ page }) => {
  const metadata = await readMetadata();
  await login(page, await readPrivateToken('ADMIN_WEB_JWT_FILE'), metadata.actorId, metadata.tenantId);
  await page.getByRole('link', { name: '当前订阅' }).click();
  await expect(page.getByRole('heading', { name: '当前订阅' })).toBeVisible();
  await expect(page.getByText('成员席位')).toBeVisible();
  await expect(page.getByRole('main').getByText('5', { exact: true })).toBeVisible();
});

test('tenant owner manages a member and an authorization in the real stack', async ({ page }) => {
  const metadata = await readMetadata();
  await login(page, await readPrivateToken('ADMIN_WEB_JWT_FILE'), metadata.actorId, metadata.tenantId);
  await page.getByRole('link', { name: '成员管理' }).click();
  await expect(page.getByRole('heading', { name: '成员管理' })).toBeVisible();
  console.log('[member-e2e] management page ready');
  const actor = `e2e-member-${randomUUID()}`;
  await page.getByLabel('成员标识').fill(actor);
  await page.getByRole('button', { name: '邀请成员' }).click();
  await expect(page.getByRole('cell', { name: actor })).toBeVisible();
  console.log('[member-e2e] invitation visible');
  await page.getByLabel('授权成员').selectOption(actor);
  await page.getByLabel('角色', { exact: true }).selectOption('editor');
  console.log('[member-e2e] member and role selected');
  await page.getByRole('button', { name: '授予角色' }).click();
  const grant = page.getByRole('table', { name: '授权列表' }).locator('tr').filter({ hasText: actor });
  await expect(grant).toContainText('editor');
  console.log('[member-e2e] grant visible');
  await grant.getByRole('button', { name: '撤销' }).click();
  await page.getByRole('button', { name: '确认撤销' }).click();
  await expect(grant).toHaveCount(0);
  console.log('[member-e2e] grant revoked');
  const member = page.getByRole('table', { name: '成员列表' }).locator('tr').filter({ hasText: actor });
  await member.getByRole('button', { name: '移除' }).click();
  await page.getByRole('button', { name: '确认移除' }).click();
  await expect(member).toHaveCount(0);
  console.log('[member-e2e] member removed');
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

test('real statistics-only role keeps the summary and loses cached details and export access', async ({ browser }) => {
  const metadata = await readMetadata();
  const result = await readResult();
  await seedScopedActors(metadata.tenantId, result.surveyId);

  const data = await rolePage(browser, 'ADMIN_WEB_DATA_JWT_FILE');
  let statisticsOnly = false;
  try {
    const token = await readPrivateToken('ADMIN_WEB_DATA_JWT_FILE');
    await login(data, token, DATA_ACTOR, metadata.tenantId);
    const responsePath = `/v1/surveys/${result.surveyId}/responses`;
    const rawResponse = data.waitForResponse((response) =>
      new URL(response.url()).pathname === responsePath && response.request().method() === 'GET',
    );
    await navigateToResponses(data, result.surveyId);
    const raw = await rawResponse;
    expect(raw.status()).toBe(200);
    const parsedRaw = responsePageSchema.safeParse(await raw.json().catch(() => null));
    if (!parsedRaw.success) throw new Error('invalid_response_page');
    expect(parsedRaw.data.items.length).toBe(1);
    expect(parsedRaw.data.items[0].state === 'engine_completed').toBe(true);
    expect(parsedRaw.data.items[0].answersStatus === 'available').toBe(true);
    expect(Object.keys(parsedRaw.data.items[0].answers ?? {}).length).toBeGreaterThan(0);
    await expect(data.locator('.response-table-wrap tbody tr')).toHaveCount(1);
    await expect(data.locator('.response-table-wrap time')).toHaveCount(2);
    await expect(data.getByRole('button', { name: '创建导出任务' })).toBeVisible();

    await setScopedDataRole(metadata.tenantId, result.surveyId, 'statistics_viewer');
    statisticsOnly = true;
    const summaryResponse = data.waitForResponse((response) =>
      new URL(response.url()).pathname === `${responsePath}/summary` && response.request().method() === 'GET',
    );
    const deniedDetails = data.waitForResponse((response) =>
      new URL(response.url()).pathname === responsePath && response.request().method() === 'GET',
    );
    // Keep the mounted page and its successful raw-data cache while refreshing authorization.
    await data.getByRole('button', { name: '刷新答卷数据' }).click();
    const summary = await summaryResponse;
    expect(summary.status()).toBe(200);
    const parsedSummary = responseSummarySchema.safeParse(await summary.json().catch(() => null));
    if (!parsedSummary.success) throw new Error('invalid_response_summary');
    expect(parsedSummary.data.surveyId === result.surveyId).toBe(true);
    expect(parsedSummary.data.total.engineCompleted).toBe(1);
    expect((await deniedDetails).status()).toBe(403);

    const exportStatus = await data.evaluate(async ({ surveyId, token, requestId }) => {
      try {
        const response = await fetch(`/v1/surveys/${surveyId}/exports`, {
          method: 'POST',
          headers: {
            Authorization: `Bearer ${token}`,
            Accept: 'application/json',
            'Content-Type': 'application/json',
            'Idempotency-Key': requestId,
          },
          body: JSON.stringify({ format: 'csv', filter: { states: null, versions: null }, templateVersion: null }),
        });
        return response.status;
      } catch {
        throw new Error('statistics_export_request_failed');
      }
    }, { surveyId: result.surveyId, token, requestId: randomUUID() });
    expect(exportStatus).toBe(403);

    await expect(data).toHaveURL(new RegExp(`/surveys/${result.surveyId}/responses$`));
    await expect(data.getByText('答卷数据已更新', { exact: true })).toBeVisible();
    await expect(data.getByLabel('已完成 1', { exact: true })).toBeVisible();
    await expect(data.getByText('你可以查看统计摘要，但没有查看答卷明细和导出的权限。', { exact: true })).toBeVisible();
    await expect(data.locator('.response-table-wrap')).toHaveCount(0);
    await expect(data.locator('.responses-page tbody tr')).toHaveCount(0);
    await expect(data.locator('.responses-page time')).toHaveCount(0);
    await expect(data.getByRole('columnheader', { name: '开始/完成时间' })).toHaveCount(0);
    for (const label of ['答卷状态', '发布版本', '开始时间起', '开始时间止', '完成时间起', '完成时间止', '导出格式']) {
      await expect(data.getByLabel(label, { exact: true })).toHaveCount(0);
    }
    await expect(data.getByRole('button', { name: /选择显示字段|收起显示字段/ })).toHaveCount(0);
    await expect(data.getByLabel('答卷分页', { exact: true })).toHaveCount(0);
    await expect(data.locator('.export-workspace')).toHaveCount(0);
    await expect(data.getByRole('button', { name: '创建导出任务' })).toHaveCount(0);
  } finally {
    try {
      if (statisticsOnly) await setScopedDataRole(metadata.tenantId, result.surveyId, 'raw_data_viewer');
    } finally {
      await data.context().close();
    }
  }
});

test('data export faults cancel safely, expose terminal state, and reauthorize each download', async ({ browser }) => {
  const metadata = await readMetadata();
  const result = await readResult();
  await seedScopedActors(metadata.tenantId, result.surveyId);
  const faults = await seedExportFaultJobs(metadata.tenantId, result.surveyId);

  const data = await rolePage(browser, 'ADMIN_WEB_DATA_JWT_FILE');
  try {
    await login(data, await readPrivateToken('ADMIN_WEB_DATA_JWT_FILE'), DATA_ACTOR, metadata.tenantId);
    await restoreExportJob(data, metadata.tenantId, result.surveyId, faults.queued);
    const cancel = data.waitForResponse((response) => response.url().includes(`/v1/exports/${faults.queued}/cancel`));
    await data.getByRole('button', { name: '取消任务' }).click();
    expect((await cancel).status()).toBe(200);
    await expect(data.locator('.export-job')).toContainText('已取消');
    await expect(data.getByText('任务已取消，可按当前筛选重新创建。')).toBeVisible();

    await restoreExportJob(data, metadata.tenantId, result.surveyId, faults.failed);
    await expect(data.locator('.export-job')).toContainText('导出失败');
    await expect(data.getByRole('alert')).toHaveText('导出失败，请按当前筛选重新创建任务。');
    await expect(data.getByRole('button', { name: '下载导出文件' })).toHaveCount(0);
    const afterFailed = await recoverExportFromTerminal(data, result.surveyId, faults.failed);

    await restoreExportJob(data, metadata.tenantId, result.surveyId, faults.expired);
    await expect(data.locator('.export-job')).toContainText('文件已过期');
    await expect(data.getByText('导出文件已过期，请重新创建任务。')).toBeVisible();
    await expect(data.getByRole('button', { name: '下载导出文件' })).toHaveCount(0);

    const afterExpired = await recoverExportFromTerminal(data, result.surveyId, faults.expired);
    expect(afterExpired).not.toBe(afterFailed);

    await revokeDataExportPermission(metadata.tenantId, result.surveyId);
    const denied = data.waitForResponse((response) => response.url().includes('/download')
      && response.request().method() === 'GET');
    await data.getByRole('button', { name: '下载导出文件' }).click();
    expect((await denied).status()).toBe(403);
    await expect(data.getByRole('alert')).toHaveText('无权执行当前操作');
    await expect(data.getByRole('button', { name: '下载导出文件' })).toBeVisible();
  } finally {
    await seedScopedActors(metadata.tenantId, result.surveyId);
    await data.context().close();
  }
});

test('tenant settings manages a synthetic organisation connection without contacting a provider', async ({ page }) => {
  const metadata = await readMetadata();
  await login(page, await readPrivateToken('ADMIN_WEB_JWT_FILE'), metadata.actorId, metadata.tenantId);
  await page.getByRole('link', { name: '组织连接' }).click();
  await expect(page.getByRole('heading', { name: '组织连接', exact: true })).toBeVisible();
  const corp = `ci-${randomUUID()}`;
  await page.getByLabel('组织标识', { exact: true }).fill(corp);
  await page.getByLabel('应用标识', { exact: true }).fill('ci-app');
  await page.getByLabel('密钥引用名', { exact: true }).fill('CI_UNUSED_REF');
  const created = page.waitForResponse((response) => response.request().method() === 'POST'
    && new URL(response.url()).pathname === '/v1/org-connections');
  await page.getByRole('button', { name: '创建连接' }).click();
  const createResponse = await created;
  expect(createResponse.status()).toBe(201);
  const connection = await createResponse.json() as { id: string };
  assertUuid(connection.id);
  assertUuid(metadata.tenantId);
  const row = page.locator('li').filter({ has: page.getByText(corp, { exact: true }) });
  await expect(row).toBeVisible();
  await row.getByRole('button', { name: '编辑连接' }).click();
  await row.getByLabel('新的密钥引用名').fill('CI_ROTATED_REF');
  await row.getByLabel('新的未知成员策略').selectOption('jit_staff');
  await row.getByRole('button', { name: '保存连接设置' }).click();
  await expect(row.getByText('CI_ROTATED_REF', { exact: true })).toBeVisible();
  await expect(row.getByText('自动开通员工', { exact: true })).toBeVisible();
  await row.getByRole('button', { name: '停用连接' }).click();
  await row.getByRole('button', { name: '确认操作' }).click();
  await expect(row.getByText('已停用', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '刷新连接' }).click();
  await expect(row.getByText('已停用', { exact: true })).toBeVisible();
  await row.getByRole('button', { name: '启用连接' }).click();
  await row.getByRole('button', { name: '确认操作' }).click();
  await expect(row.getByText('已启用', { exact: true })).toBeVisible();
  const sql = `SELECT set_config('app.tenant_id', '${metadata.tenantId}', false);
    SELECT (SELECT count(*) FROM org_connection WHERE tenant_id='${metadata.tenantId}' AND id='${connection.id}'
      AND secret_ref='CI_ROTATED_REF' AND unknown_user_policy='jit_staff' AND enabled=true),
    (SELECT count(*) FROM audit_log WHERE tenant_id='${metadata.tenantId}' AND resource LIKE 'org_connection/${connection.id} %'
      AND action='identity.org_connection.create'),
    (SELECT count(*) FROM audit_log WHERE tenant_id='${metadata.tenantId}' AND resource LIKE 'org_connection/${connection.id} %'
      AND action='identity.org_connection.update');`;
  const evidence = execFileSync('docker', ['exec', '-i', requiredEnv('ADMIN_WEB_PLATFORM_DB_CONTAINER'), 'psql',
    '-U', 'platform_owner', '-d', 'platform', '-At', '-v', 'ON_ERROR_STOP=1'], { input: sql, encoding: 'utf8' });
  expect(evidence.trim().split('\n').at(-1)).toBe('1|1|3');
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

async function seedExportFaultJobs(tenantId: string, surveyId: string) {
  assertUuid(tenantId);
  assertUuid(surveyId);
  const queued = randomUUID();
  const failed = randomUUID();
  const expired = randomUUID();
  const sql = `BEGIN;
SELECT set_config('app.tenant_id', '${tenantId}', true);
DELETE FROM response_export_job
WHERE tenant_id = '${tenantId}'::uuid AND requested_by = '${DATA_ACTOR}';
INSERT INTO response_export_job
    (tenant_id, id, survey_id, requested_by, format, filter_snapshot, plan,
     reveal_sensitive, batch_size, status, next_attempt_at, expires_at, finished_at, last_error, created_at)
VALUES ('${tenantId}'::uuid, '${queued}'::uuid, '${surveyId}'::uuid, '${DATA_ACTOR}', 'csv',
        '{}'::jsonb, '{}'::jsonb, false, 100, 'queued', now() + interval '1 hour',
        now() + interval '2 hours', NULL, NULL, now()),
       ('${tenantId}'::uuid, '${failed}'::uuid, '${surveyId}'::uuid, '${DATA_ACTOR}', 'csv',
        '{}'::jsonb, '{}'::jsonb, false, 100, 'failed', now(),
        now() + interval '2 hours', now(), 'fault_injection', now()),
       ('${tenantId}'::uuid, '${expired}'::uuid, '${surveyId}'::uuid, '${DATA_ACTOR}', 'csv',
        '{}'::jsonb, '{}'::jsonb, false, 100, 'expired', now(),
        now() - interval '1 second', now(), NULL, now() - interval '2 hours');
COMMIT;`;
  await run('docker', [
    'exec', requiredEnv('ADMIN_WEB_PLATFORM_DB_CONTAINER'),
    'psql', '-U', 'platform_owner', '-d', 'platform', '-v', 'ON_ERROR_STOP=1', '-q', '-c', sql,
  ]);
  return { queued, failed, expired };
}

async function recoverExportFromTerminal(page: Page, surveyId: string, oldId: string): Promise<string> {
  await page.getByLabel('答卷状态').selectOption('engine_completed');
  await page.locator('.response-filters label').filter({ hasText: '发布版本' }).locator('select').selectOption('1');
  const answer = page.locator('.response-answers > div').filter({ has: page.locator('dd', { hasText: /^A1$/ }) }).first();
  await expect(answer).toBeVisible();
  const question = (await answer.locator('dt').textContent())?.trim() ?? '';
  expect(question).not.toBe('');
  const responsePromise = page.waitForResponse((response) =>
    new URL(response.url()).pathname === `/v1/surveys/${surveyId}/exports` && response.request().method() === 'POST');
  await page.getByRole('button', { name: '创建导出任务' }).click();
  const response = await responsePromise;
  expect(response.status()).toBe(202);
  expect(response.request().postDataJSON()).toEqual({ format: 'csv', filter: { states: ['engine_completed'], versions: [1] }, templateVersion: null });
  const created = await response.json() as { jobId: string };
  assertUuid(created.jobId);
  expect(created.jobId).not.toBe(oldId);
  await expect(page.locator('.export-job')).toContainText('导出完成', { timeout: 60_000 });
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: '下载导出文件' }).click();
  const download = await downloadPromise;
  const path = await download.path();
  expect(path).not.toBeNull();
  const responses = await readExportCsv(path!, 'responses.csv');
  const fields = await readExportCsv(path!, 'fields.csv');
  const code = fields[0].indexOf('questioncode');
  const fieldname = fields[0].indexOf('fieldname');
  const column = fields[0].indexOf('column');
  expect(Math.min(code, fieldname, column)).toBeGreaterThanOrEqual(0);
  const field = fields.slice(1).find((row) => row[code] === question || row[fieldname] === question || row[column] === question);
  expect(field).toBeDefined();
  const valueIndex = responses[0].indexOf(field![column]);
  expect(valueIndex).toBeGreaterThanOrEqual(0);
  expect(responses).toHaveLength(3);
  expect(responses[2][valueIndex]).toBe('A1');
  return created.jobId;
}

async function readExportCsv(path: string, entry: string): Promise<string[][]> {
  const bytes = await new Promise<Buffer>((resolve, reject) => {
    execFile('unzip', ['-p', path, entry], { encoding: 'buffer', maxBuffer: 16 * 1024 * 1024 },
      (error, stdout) => error ? reject(error) : resolve(stdout));
  });
  expect([...bytes.subarray(0, 3)]).toEqual([0xef, 0xbb, 0xbf]);
  const text = bytes.subarray(3).toString('utf8');
  const rows: string[][] = [];
  let row: string[] = [], cell = '', quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === '"') {
      if (quoted && text[i + 1] === '"') { cell += '"'; i++; } else quoted = !quoted;
    } else if (c === ',' && !quoted) { row.push(cell); cell = ''; }
    else if (c === '\r' && text[i + 1] === '\n' && !quoted) { row.push(cell); rows.push(row); row = []; cell = ''; i++; }
    else cell += c;
  }
  expect(quoted).toBe(false);
  if (cell || row.length) rows.push([...row, cell]);
  return rows;
}

async function restoreExportJob(page: Page, tenantId: string, surveyId: string, jobId: string) {
  await page.evaluate(({ key, value }) => sessionStorage.setItem(key, value), {
    key: exportStorageKey(tenantId, surveyId), value: jobId,
  });
  await navigateToResponses(page, surveyId);
}

async function navigateToResponses(page: Page, surveyId: string) {
  await page.evaluate(() => {
    window.history.pushState({}, '', '/dashboard');
    window.dispatchEvent(new PopStateEvent('popstate'));
  });
  await expect(page.getByRole('heading', { name: '工作台' })).toBeVisible();
  await page.evaluate((path) => {
    window.history.pushState({}, '', path);
    window.dispatchEvent(new PopStateEvent('popstate'));
  }, `/surveys/${surveyId}/responses`);
  await expect(page.getByRole('heading', { name: '答卷与导出' })).toBeVisible();
}

function exportStorageKey(tenantId: string, surveyId: string) {
  return `survey-export:${tenantId}:${DATA_ACTOR}:${surveyId}`;
}

async function revokeDataExportPermission(tenantId: string, surveyId: string) {
  assertUuid(tenantId);
  assertUuid(surveyId);
  const sql = `BEGIN;
SELECT set_config('app.tenant_id', '${tenantId}', true);
DO $$
DECLARE deleted_count integer;
BEGIN
  WITH RECURSIVE ancestors AS (
    SELECT id, parent_id FROM access_resource WHERE tenant_id = '${tenantId}' AND id = '${surveyId}'
    UNION ALL
    SELECT parent.id, parent.parent_id
    FROM access_resource parent
    JOIN ancestors child ON child.parent_id = parent.id
    WHERE parent.tenant_id = '${tenantId}'
  )
  DELETE FROM access_grant
  WHERE tenant_id = '${tenantId}'::uuid AND actor_id = '${DATA_ACTOR}' AND role_code = 'raw_data_viewer'
    AND resource_id IN (SELECT id FROM ancestors);
  GET DIAGNOSTICS deleted_count = ROW_COUNT;
  IF deleted_count <> 1 THEN
    RAISE EXCEPTION 'expected one raw data grant to be revoked, got %', deleted_count;
  END IF;
END $$;
COMMIT;`;
  await run('docker', [
    'exec', requiredEnv('ADMIN_WEB_PLATFORM_DB_CONTAINER'),
    'psql', '-U', 'platform_owner', '-d', 'platform', '-v', 'ON_ERROR_STOP=1', '-q', '-c', sql,
  ]);
}

async function setScopedDataRole(
  tenantId: string,
  surveyId: string,
  role: 'raw_data_viewer' | 'statistics_viewer',
) {
  assertUuid(tenantId);
  assertUuid(surveyId);
  const previousRole = role === 'statistics_viewer' ? 'raw_data_viewer' : 'statistics_viewer';
  // Change only the existing synthetic grant's role, preserving its ID, scope and metadata.
  // An existing statistics grant or any other applicable grant fails the transition atomically.
  const sql = `BEGIN;
SELECT set_config('app.tenant_id', '${tenantId}', true);
DO $$
DECLARE changed_count integer;
BEGIN
  WITH RECURSIVE ancestors AS (
    SELECT id, parent_id FROM access_resource WHERE tenant_id = '${tenantId}' AND id = '${surveyId}'
    UNION ALL
    SELECT parent.id, parent.parent_id
    FROM access_resource parent
    JOIN ancestors child ON child.parent_id = parent.id
    WHERE parent.tenant_id = '${tenantId}'
  ), root_project AS (
    SELECT id FROM ancestors WHERE parent_id IS NULL
  ), applicable_grants AS (
    SELECT id FROM access_grant
    WHERE tenant_id = '${tenantId}'::uuid AND actor_id = '${DATA_ACTOR}'
      AND (resource_id IS NULL OR resource_id IN (SELECT id FROM ancestors))
  )
  UPDATE access_grant SET role_code = '${role}'
  WHERE tenant_id = '${tenantId}'::uuid AND actor_id = '${DATA_ACTOR}' AND role_code = '${previousRole}'
    AND resource_id IN (SELECT id FROM root_project)
    AND (SELECT count(*) FROM applicable_grants) = 1;
  GET DIAGNOSTICS changed_count = ROW_COUNT;
  IF changed_count <> 1 THEN
    RAISE EXCEPTION 'unexpected_scoped_data_role_count';
  END IF;
END $$;
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
