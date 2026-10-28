import { expect, test, vi } from 'vitest';
import { createApiClient } from './http';
import { jsonResponse } from '../../test/server';
import {
  templateViewSchema, templateDetailSchema, listTemplates, getTemplate, copyTemplate,
  createTenantTemplate, addTenantTemplateVersion, submitTemplate, approveTemplate,
  rejectTemplate, unpublishTemplate, listPendingTemplates, getTemplateVersion,
  listPlatformTemplates, createPlatformTemplate, addPlatformTemplateVersion,
  publishPlatformTemplate, unpublishPlatformTemplate, templateListQueryKey,
  templateDetailQueryKey,
  listManagedTemplates, getTemplateCapabilities, templateCapabilitiesSchema,
  templateManagedQueryKey, templateCapabilitiesQueryKey,
} from './templates';

const id = '10000000-0000-4000-8000-000000000001';
const surveyId = '20000000-0000-4000-8000-000000000001';
const view = { id, scope: 'tenant', name: '模板', description: null, status: 'draft', currentVersion: 1,
  publishedVersion: null, createdBy: 'author', reviewedBy: null, reviewedAt: null,
  reviewReason: null, updatedAt: '2026-10-27T08:00:00Z' };
const detail = { template: view, versions: [{ versionNo: 1, sourceSurveyId: surveyId,
  sourceDraftVersion: 2, createdBy: 'author', createdAt: '2026-10-27T08:00:00Z' }],
  history: [{ event: 'create', actor: 'author', versionNo: 1, reason: null,
    copiedSurveyId: null, occurredAt: '2026-10-27T08:00:00Z' }] };

test('validates every scope and status and full detail metadata', () => {
  for (const scope of ['tenant', 'platform']) for (const status of
    ['draft', 'pending', 'published', 'rejected', 'unpublished']) {
    expect(templateViewSchema.parse({ ...view, scope, status }).status).toBe(status);
  }
  expect(templateDetailSchema.parse(detail)).toEqual(detail);
  expect(templateViewSchema.parse({ ...view, reviewedBy: 'reviewer', reviewedAt: '2026-10-27T09:00:00Z', reviewReason: '修订' }).reviewReason).toBe('修订');
});

test.each([
  { id: 'bad' }, { updatedAt: 'yesterday' }, { currentVersion: 0 },
  { publishedVersion: -1 }, { scope: 'all' }, { status: 'unknown' },
  { reviewedAt: undefined }, { createdBy: undefined },
])('rejects malformed template payload %j', (change) => {
  expect(() => templateViewSchema.parse({ ...view, ...change })).toThrow();
});

test('rejects malformed nested version and history fields', () => {
  expect(() => templateDetailSchema.parse({ ...detail, versions: [{ ...detail.versions[0], sourceSurveyId: 'bad' }] })).toThrow();
  expect(() => templateDetailSchema.parse({ ...detail, history: [{ ...detail.history[0], occurredAt: 'bad' }] })).toThrow();
});

test('uses tenant endpoints and encoded identifiers for all tenant actions', async () => {
  const request = vi.fn().mockResolvedValue(view);
  const api = { request };
  const weird = 'id /?';
  await listTemplates(api, 'all'); await getTemplate(api, weird);
  await getTemplateVersion(api, weird, 2); await listPendingTemplates(api);
  await createTenantTemplate(api, surveyId, '名称', '说明');
  await addTenantTemplateVersion(api, weird, surveyId);
  await submitTemplate(api, weird); await approveTemplate(api, weird);
  await rejectTemplate(api, weird, '原因'); await unpublishTemplate(api, weird, '下架');
  await copyTemplate(api, weird, surveyId, '副本');
  expect(request.mock.calls.map(([arg]) => arg.path)).toEqual([
    '/v1/survey-templates?scope=all', '/v1/survey-templates/id%20%2F%3F',
    '/v1/survey-templates/id%20%2F%3F/versions/2', '/v1/survey-templates/pending',
    '/v1/survey-templates', '/v1/survey-templates/id%20%2F%3F/versions',
    '/v1/survey-templates/id%20%2F%3F/submit', '/v1/survey-templates/id%20%2F%3F/approve',
    '/v1/survey-templates/id%20%2F%3F/reject', '/v1/survey-templates/id%20%2F%3F/unpublish',
    '/v1/survey-templates/id%20%2F%3F/copies',
  ]);
  expect(request.mock.calls[8][0].body).toEqual({ reason: '原因' });
  expect(request.mock.calls[10][0].body).toEqual({ parentId: surveyId, title: '副本' });
});

test('uses separate platform endpoints and validates platform-only responses', async () => {
  const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse([view]));
  await expect(listPlatformTemplates(createApiClient({ fetchImpl }))).rejects.toMatchObject({ kind: 'unexpected' });
  const request = vi.fn().mockResolvedValue({ ...view, scope: 'platform' });
  const api = { request };
  await listPlatformTemplates(api); await createPlatformTemplate(api, '平台', null, { definitionVersion: 2 });
  await addPlatformTemplateVersion(api, id, { definitionVersion: 2 });
  await publishPlatformTemplate(api, id, 2); await unpublishPlatformTemplate(api, id);
  expect(request.mock.calls.map(([arg]) => arg.path)).toEqual([
    '/v1/platform/survey-templates', '/v1/platform/survey-templates',
    `/v1/platform/survey-templates/${id}/versions`, `/v1/platform/survey-templates/${id}/publish`,
    `/v1/platform/survey-templates/${id}/unpublish`,
  ]);
  expect(request.mock.calls[3][0].body).toEqual({ versionNo: 2 });
});

test('isolates tenant query keys and normalizes default scope', () => {
  expect(templateListQueryKey('tenant-a')).toEqual(templateListQueryKey('tenant-a', 'all'));
  expect(templateListQueryKey('tenant-a')).not.toEqual(templateListQueryKey('tenant-b'));
  expect(templateDetailQueryKey('tenant-a', id)).not.toEqual(templateDetailQueryKey('tenant-b', id));
});

test('reads managed lifecycle states and server-issued action capabilities', async () => {
  const request = vi.fn().mockResolvedValue([]);
  const api = { request };
  await listManagedTemplates(api);
  await getTemplateCapabilities(api, 'id /?');
  expect(request.mock.calls.map(([arg]) => arg.path)).toEqual([
    '/v1/survey-templates/managed', '/v1/survey-templates/id%20%2F%3F/capabilities',
  ]);
  expect(templateCapabilitiesSchema.parse({ canAddVersion: true, canSubmit: false,
    canReview: false, canUnpublish: false }).canAddVersion).toBe(true);
  expect(() => templateCapabilitiesSchema.parse({ canAddVersion: true, canSubmit: false,
    canReview: 'yes', canUnpublish: false })).toThrow();
  expect(templateManagedQueryKey('a')).not.toEqual(templateManagedQueryKey('b'));
  expect(templateCapabilitiesQueryKey('a', id)).not.toEqual(templateCapabilitiesQueryKey('b', id));
});
