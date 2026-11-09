import { expect, type Page, type BrowserContext } from '@playwright/test';
import { isDeepStrictEqual } from 'node:util';
import { randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { approvalDetailSchema, approvalRequestSchema, publishedVersionSchema, surveyOverviewSchema } from '../src/shared/api/approvals';
import { draftViewSchema } from '../src/shared/api/surveys';

type Login = (page: Page, token: string, metadata: { actorId: string; tenantId: string }) => Promise<{ actorId: string; tenantId: string }>;
type Run = (command: string, args: string[]) => Promise<string>;

export async function approvalJourney(owner: Page, surveyId: string, rootProjectId: string, tenantId: string, login: Login, run: Run) {
  for (const id of [surveyId, rootProjectId, tenantId]) expect(id).toMatch(/^[0-9a-f-]{36}$/i);
  const editorId = 'admin-web-e2e-editor';
  const reviewerId = 'admin-web-e2e-reviewer';
  const database = process.env.ADMIN_WEB_PLATFORM_DB_CONTAINER;
  if (!database) throw new Error('Missing isolated database container');
  const sql = (query: string) => run('docker', ['exec', database, 'psql', '-U', 'platform_owner', '-d', 'platform', '-v', 'ON_ERROR_STOP=1', '-tAq', '-c', query]);
  await sql(`BEGIN;
SELECT set_config('app.tenant_id', '${tenantId}', true);
INSERT INTO access_member (tenant_id, actor_id, status, uses_seat, invited_by)
VALUES ('${tenantId}', '${editorId}', 'active', true, 'admin-web-e2e-owner'),
('${tenantId}', '${reviewerId}', 'active', true, 'admin-web-e2e-owner')
ON CONFLICT (tenant_id, actor_id) DO UPDATE SET status = 'active', updated_at = now();
INSERT INTO access_grant (tenant_id, id, actor_id, role_code, resource_id, granted_by)
VALUES ('${tenantId}', '${randomUUID()}', '${editorId}', 'editor', '${rootProjectId}', 'admin-web-e2e-owner'),
('${tenantId}', '${randomUUID()}', '${reviewerId}', 'publish_reviewer', '${rootProjectId}', 'admin-web-e2e-owner')
ON CONFLICT (tenant_id, actor_id, role_code, resource_id) DO NOTHING;
COMMIT;`);
  const token = async (key: string) => {
    const path = process.env[key];
    if (!path) throw new Error(`Missing ${key}`);
    return (await readFile(path, 'utf8')).trim();
  };
  const editorToken = await token('ADMIN_WEB_EDITOR_JWT_FILE');
  const reviewerToken = await token('ADMIN_WEB_REVIEWER_JWT_FILE');
  const browser = owner.context().browser();
  if (!browser) throw new Error('Browser unavailable');
  const baseURL = new URL(owner.url()).origin;
  let editorContext: BrowserContext | undefined;
  let reviewerContext: BrowserContext | undefined;
  let cleanupFailed: boolean;
  const marker = randomUUID().slice(0, 6);
  try {
    editorContext = await browser.newContext({ baseURL });
    reviewerContext = await browser.newContext({ baseURL });
    editorContext.setDefaultTimeout(15_000);
    reviewerContext.setDefaultTimeout(15_000);
    const navigate = async (page: Page, path: string) => {
      await page.evaluate((nextPath) => {
        window.history.pushState({}, '', nextPath);
        window.dispatchEvent(new PopStateEvent('popstate'));
      }, '/dashboard');
      await expect(page.getByRole('heading', { name: '工作台' })).toBeVisible();
      await page.evaluate((nextPath) => {
        window.history.pushState({}, '', nextPath);
        window.dispatchEvent(new PopStateEvent('popstate'));
      }, path);
    };
    const editor = await editorContext.newPage();
    const reviewer = await reviewerContext.newPage();
    const em = await login(editor, editorToken, { actorId: editorId, tenantId });
    const rm = await login(reviewer, reviewerToken, { actorId: reviewerId, tenantId });
    expect(em.actorId).not.toBe(rm.actorId);
    expect(em.tenantId).toBe(rm.tenantId);
    // Fetch inside the independent browser session. Never expose credentials in assertion output.
    const api = async (page: Page, credential: string, path: string, method = 'GET', body?: unknown) => page.evaluate(async (args) => {
      const response = await fetch(args.path, { method: args.method, headers: { Authorization: `Bearer ${args.credential}`, 'Content-Type': 'application/json' }, body: args.body === undefined ? undefined : JSON.stringify(args.body) });
      return { status: response.status, data: await response.json() as unknown };
    }, { credential, path, method, body });
    const get = async (path: string) => {
      const response = await api(editor, editorToken, path);
      expect(response.status).toBe(200);
      return response.data;
    };
    const survey = `/v1/surveys/${surveyId}`;
    expect(await get(`/v1/resource-capabilities?resourceId=${surveyId}`)).toMatchObject({ canEdit: true, canSubmitApproval: true, canApprovePublish: false, canPublishDirectly: false });
    const reviewerCapabilities = await api(reviewer, reviewerToken, `/v1/resource-capabilities?resourceId=${surveyId}`);
    expect(reviewerCapabilities.status).toBe(200);
    expect(reviewerCapabilities.data).toMatchObject({ canApprovePublish: true });
    const counts = async () => JSON.parse((await sql(`BEGIN;
SET LOCAL app.tenant_id = '${tenantId}';
SELECT json_build_object(
'surveys', (SELECT count(*) FROM survey WHERE tenant_id='${tenantId}' AND id='${surveyId}'),
'attempts', (SELECT count(*) FROM survey_publish_attempt WHERE tenant_id='${tenantId}' AND survey_id='${surveyId}'),
'versions', (SELECT count(*) FROM survey_published_version WHERE tenant_id='${tenantId}' AND survey_id='${surveyId}'),
'bindings', (SELECT count(*) FROM survey_question_binding WHERE tenant_id='${tenantId}' AND survey_id='${surveyId}'),
'routes', (SELECT count(*) FROM survey_route WHERE tenant_id='${tenantId}' AND public_id='${surveyId}'))::text;
COMMIT;`)).trim()) as Record<string, number>;
    const pristine = async () => {
      expect(surveyOverviewSchema.parse(await get(survey))).toMatchObject({ status: 'draft', publishedVersion: null, lastPublish: null });
      expect(await get(`${survey}/versions`)).toEqual([]);
      expect(await counts()).toEqual({ surveys: 1, attempts: 0, versions: 0, bindings: 0, routes: 0 });
    };
    const save = async (text: string) => {
      await navigate(editor, `/surveys/${surveyId}/edit`);
      await editor.getByLabel('题目文本').fill(text);
      const response = editor.waitForResponse(r => new URL(r.url()).pathname === `${survey}/draft` && r.request().method() === 'PUT');
      await editor.getByRole('button', { name: '保存草稿' }).click();
      const saved = await response;
      expect(saved.status()).toBe(200);
      return draftViewSchema.parse(await saved.json());
    };
    const submit = async () => {
      await navigate(editor, `/surveys/${surveyId}/publish`);
      const response = editor.waitForResponse(r => new URL(r.url()).pathname === `${survey}/approval-requests` && r.request().method() === 'POST');
      await editor.getByRole('button', { name: '提交审批', exact: true }).click();
      const result = await response;
      expect(result.status()).toBe(201);
      return approvalRequestSchema.parse(await result.json());
    };
    const approve = async (id: string) => {
      await navigate(reviewer, `/surveys/${surveyId}/publish`);
      const response = reviewer.waitForResponse(r => new URL(r.url()).pathname === `/v1/publish-approvals/${id}/approve` && r.request().method() === 'POST');
      await reviewer.getByRole('button', { name: '批准申请', exact: true }).click();
      const result = await response;
      expect(result.status()).toBe(200);
      expect(approvalRequestSchema.parse(await result.json())).toMatchObject({ id, status: 'approved', applicant: editorId, decidedBy: reviewerId });
    };
    const v1 = await save(`审批前稿${marker}`);
    await pristine();
    const withdrawn = await submit();
    expect(withdrawn).toMatchObject({ applicant: editorId, draftVersion: v1.version, status: 'pending' });
    const unauthorizedWithdraw = await api(reviewer, reviewerToken, `/v1/publish-approvals/${withdrawn.id}/withdraw`, 'POST');
    expect(unauthorizedWithdraw.status).toBe(403);
    await navigate(editor, `/surveys/${surveyId}/publish`);
    const withdrawalResponse = editor.waitForResponse(r => new URL(r.url()).pathname === `/v1/publish-approvals/${withdrawn.id}/withdraw` && r.request().method() === 'POST');
    await editor.getByRole('button', { name: '撤回申请', exact: true }).click();
    expect((await withdrawalResponse).status()).toBe(200);
    const withdrawnDetail = approvalDetailSchema.parse(await get(`/v1/publish-approvals/${withdrawn.id}`));
    expect(withdrawnDetail.request.status).toBe('withdrawn');
    expect(withdrawnDetail.history.map(({ event, actor }) => ({ event, actor }))).toEqual([
      { event: 'submitted', actor: editorId }, { event: 'withdrawn', actor: editorId },
    ]);
    expect((await api(reviewer, reviewerToken, `/v1/publish-approvals/${withdrawn.id}/approve`, 'POST')).status).toBe(409);
    await pristine();

    const rejected = await submit();
    const unauthorizedReject = await api(editor, editorToken, `/v1/publish-approvals/${rejected.id}/reject`, 'POST', { reason: '无权驳回' });
    expect(unauthorizedReject.status).toBe(403);
    await navigate(reviewer, `/surveys/${surveyId}/publish`);
    await reviewer.getByRole('button', { name: '驳回申请', exact: true }).click();
    await reviewer.getByLabel('驳回原因').fill('请修订问卷内容');
    const rejectResponse = reviewer.waitForResponse(r => new URL(r.url()).pathname === `/v1/publish-approvals/${rejected.id}/reject` && r.request().method() === 'POST');
    await reviewer.getByRole('button', { name: '确认驳回', exact: true }).click();
    expect((await rejectResponse).status()).toBe(200);
    const rejectedDetail = approvalDetailSchema.parse(await get(`/v1/publish-approvals/${rejected.id}`));
    expect(rejectedDetail.request).toMatchObject({ status: 'rejected', decidedBy: reviewerId, reason: '请修订问卷内容' });
    expect(rejectedDetail.history.map(({ event, actor }) => ({ event, actor }))).toEqual([
      { event: 'submitted', actor: editorId }, { event: 'rejected', actor: reviewerId },
    ]);
    expect((await api(editor, editorToken, `${survey}/publish`, 'POST')).status).toBe(403);
    await pristine();

    const a1 = await submit();
    expect(a1).toMatchObject({ applicant: editorId, draftVersion: v1.version, status: 'pending' });
    await approve(a1.id);
    await pristine();
    const v2 = await save(`重审定稿${marker}`);
    expect(v2.version).toBe(v1.version + 1);
    const first = approvalDetailSchema.parse(await get(`/v1/publish-approvals/${a1.id}`));
    expect(first.request.status).toBe('voided');
    expect(first.history.map(({ event, actor }) => ({ event, actor }))).toEqual([
      { event: 'submitted', actor: editorId }, { event: 'approved', actor: reviewerId }, { event: 'voided', actor: editorId },
    ]);
    await navigate(editor, `/surveys/${surveyId}/publish`);
    await expect(editor.getByRole('button', { name: '提交审批', exact: true })).toBeVisible();
    await expect(editor.getByRole('button', { name: '发布问卷', exact: true })).toHaveCount(0);
    for (const [page, credential, path, body, status, error] of [
      [editor, editorToken, `${survey}/publish`, undefined, 403, 'approval_required'],
      [reviewer, reviewerToken, `/v1/publish-approvals/${a1.id}/approve`, undefined, 409, 'approval_not_pending'],
      [editor, editorToken, `${survey}/approval-requests`, { draftVersion: v1.version }, 409, 'version_conflict'],
    ] as const) {
      const denied = await api(page, credential, path, 'POST', body);
      expect(denied.status).toBe(status);
      expect(denied.data).toMatchObject({ error });
    }
    await pristine();
    const a2 = await submit();
    expect(a2.id).not.toBe(a1.id);
    expect(a2).toMatchObject({ applicant: editorId, draftVersion: v2.version, status: 'pending' });
    await approve(a2.id);
    await navigate(editor, `/surveys/${surveyId}/publish`);
    await editor.getByRole('button', { name: '发布问卷', exact: true }).click();
    await expect(editor.getByText('发布成功')).toBeVisible({ timeout: 210_000 });
    const versions = await get(`${survey}/versions`);
    expect(Array.isArray(versions) && versions.length === 1).toBe(true);
    const published = publishedVersionSchema.parse(await get(`${survey}/versions/1`));
    expect(published.surveyId === surveyId && published.version === 1 && published.draftVersion === v2.version && published.publishedBy === editorId && published.live).toBe(true);
    expect(isDeepStrictEqual(published.definition, v2.definition)).toBe(true);
    expect(isDeepStrictEqual(published.definition, v1.definition)).toBe(false);
    expect(approvalDetailSchema.parse(await get(`/v1/publish-approvals/${a1.id}`)).request.status).toBe('voided');
    const final = approvalDetailSchema.parse(await get(`/v1/publish-approvals/${a2.id}`));
    expect(final.request.status).toBe('published');
    expect(final.history).toEqual(expect.arrayContaining([
      expect.objectContaining({ event: 'approved', actor: reviewerId }),
      expect.objectContaining({ event: 'publish_started', actor: editorId, publishRequestId: published.requestId }),
      expect.objectContaining({ event: 'published', actor: editorId, publishRequestId: published.requestId }),
    ]));
    expect(await counts()).toEqual({ surveys: 1, attempts: 1, versions: 1, bindings: published.fields.length, routes: 1 });
    const db = JSON.parse((await sql(`BEGIN;
SET LOCAL app.tenant_id = '${tenantId}';
SELECT json_build_object(
'version', (SELECT json_build_object('surveyId', survey_id, 'version', version_no, 'requestId', request_id, 'draftVersion', draft_version, 'engineInstanceId', engine_instance_id, 'engineSid', engine_sid, 'publishedBy', published_by, 'definition', definition) FROM survey_published_version WHERE tenant_id='${tenantId}' AND survey_id='${surveyId}' AND version_no=1),
'route', (SELECT json_build_object('engineInstanceId', engine_instance_id, 'engineSid', engine_sid) FROM survey_route WHERE tenant_id='${tenantId}' AND public_id='${surveyId}' AND superseded_at IS NULL),
'fields', (SELECT coalesce(json_agg(json_build_object('questionUuid', question_uuid, 'code', question_code, 'type', question_type, 'fieldname', fieldname, 'aid', aid, 'scale', scale) ORDER BY ordinal), '[]'::json) FROM survey_question_binding WHERE tenant_id='${tenantId}' AND survey_id='${surveyId}' AND version_no=1))::text;
COMMIT;`)).trim()) as unknown;
    expect(isDeepStrictEqual(db, {
      version: { surveyId, version: 1, requestId: published.requestId, draftVersion: v2.version, engineInstanceId: published.engineInstanceId, engineSid: published.engineSid, publishedBy: editorId, definition: v2.definition },
      route: { engineInstanceId: published.engineInstanceId, engineSid: published.engineSid },
      fields: published.fields,
    })).toBe(true);
    await navigate(owner, `/surveys/${surveyId}/publish`);
  } finally {
    const closed = await Promise.allSettled([reviewerContext?.close(), editorContext?.close()]);
    cleanupFailed = closed.some(result => result.status === 'rejected');
  }
  if (cleanupFailed) throw new Error('Approval browser context cleanup failed');
  return `重审定稿${marker}`;
}
