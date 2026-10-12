import { expect, test, vi } from 'vitest';
import { jsonResponse } from '../../test/server';
import {
  dashboardQueryKey,
  getDashboard,
  recordRecentWork,
  type DashboardView,
} from './dashboard';
import { createApiClient } from './http';
import { dashboardViewSchema } from './schemas';

const surveyId = '10000000-0000-4000-8000-000000000001';

const dashboard: DashboardView = {
  generatedAt: '2026-10-10T00:00:00Z',
  visibleSections: ['approval', 'publish', 'preview', 'export', 'survey'],
  summary: {
    pendingApprovals: 1,
    publishExceptions: 2,
    activePreviews: 3,
    activeExports: 4,
  },
  tasks: [
    {
      taskKey: 'approval:10000000-0000-4000-8000-000000000001',
      kind: 'pending_approval',
      surveyId,
      surveyName: '客户满意度',
      status: 'pending',
      updatedAt: '2026-10-10T00:00:00Z',
      targetPath: `/surveys/${surveyId}/publish`,
    },
  ],
  surveys: [
    {
      surveyId,
      name: '客户满意度',
      draftVersion: 4,
      publishedVersion: 3,
      publishState: 'published',
      completedResponses: null,
      updatedAt: '2026-10-10T00:00:00Z',
      actions: ['edit', 'preview', 'publish', 'responses'],
    },
  ],
  recentWork: [
    {
      surveyId,
      surveyName: '客户满意度',
      page: 'version',
      targetPath: `/surveys/${surveyId}/versions/3`,
      visitedAt: '2026-10-10T00:00:00Z',
    },
  ],
};

test('validates the complete dashboard contract and every enum value', () => {
  expect(dashboardViewSchema.parse(dashboard)).toEqual(dashboard);

  for (const section of ['approval', 'publish', 'preview', 'export', 'survey']) {
    expect(() => dashboardViewSchema.parse({ ...dashboard, visibleSections: [section] })).not.toThrow();
  }
  for (const kind of [
    'pending_approval',
    'publish_exception',
    'preview_exception',
    'export_exception',
    'draft_pending_publish',
  ]) {
    expect(() => dashboardViewSchema.parse({
      ...dashboard,
      tasks: [{ ...dashboard.tasks[0], kind }],
    })).not.toThrow();
  }
  for (const publishState of [
    'draft',
    'pending_approval',
    'approved',
    'publishing',
    'published',
    'failed',
    'needs_reconciliation',
  ]) {
    expect(() => dashboardViewSchema.parse({
      ...dashboard,
      surveys: [{ ...dashboard.surveys[0], publishState }],
    })).not.toThrow();
  }
  for (const action of ['edit', 'preview', 'publish', 'responses']) {
    expect(() => dashboardViewSchema.parse({
      ...dashboard,
      surveys: [{ ...dashboard.surveys[0], actions: [action] }],
    })).not.toThrow();
  }
  for (const page of ['edit', 'import', 'preview', 'publish', 'responses']) {
    expect(() => dashboardViewSchema.parse({
      ...dashboard,
      recentWork: [{
        ...dashboard.recentWork[0],
        page,
        targetPath: `/surveys/${surveyId}/${page}`,
      }],
    })).not.toThrow();
  }

  expect(() => dashboardViewSchema.parse({ ...dashboard, visibleSections: ['billing'] })).toThrow();
  expect(() => dashboardViewSchema.parse({
    ...dashboard,
    tasks: [{ ...dashboard.tasks[0], kind: 'unknown' }],
  })).toThrow();
  expect(() => dashboardViewSchema.parse({
    ...dashboard,
    surveys: [{ ...dashboard.surveys[0], publishState: 'unknown' }],
  })).toThrow();
  expect(() => dashboardViewSchema.parse({
    ...dashboard,
    recentWork: [{ ...dashboard.recentWork[0], page: 'unknown' }],
  })).toThrow();
});

test('rejects missing and unknown dashboard fields at every object boundary', () => {
  const withoutGeneratedAt: Partial<DashboardView> = { ...dashboard };
  delete withoutGeneratedAt.generatedAt;
  const surveyWithoutName = { ...dashboard.surveys[0] } as Partial<DashboardView['surveys'][number]>;
  delete surveyWithoutName.name;
  const recentWorkWithoutVisitedAt = {
    ...dashboard.recentWork[0],
  } as Partial<DashboardView['recentWork'][number]>;
  delete recentWorkWithoutVisitedAt.visitedAt;

  expect(() => dashboardViewSchema.parse(withoutGeneratedAt)).toThrow();
  expect(() => dashboardViewSchema.parse({ ...dashboard, internalNote: 'secret' })).toThrow();
  expect(() => dashboardViewSchema.parse({
    ...dashboard,
    summary: { ...dashboard.summary, totalSurveys: 99 },
  })).toThrow();
  expect(() => dashboardViewSchema.parse({
    ...dashboard,
    tasks: [{ ...dashboard.tasks[0], internalStatus: 'secret' }],
  })).toThrow();
  expect(() => dashboardViewSchema.parse({
    ...dashboard,
    surveys: [surveyWithoutName],
  })).toThrow();
  expect(() => dashboardViewSchema.parse({
    ...dashboard,
    surveys: [{ ...dashboard.surveys[0], internalStatus: 'secret' }],
  })).toThrow();
  expect(() => dashboardViewSchema.parse({
    ...dashboard,
    recentWork: [recentWorkWithoutVisitedAt],
  })).toThrow();
  expect(() => dashboardViewSchema.parse({
    ...dashboard,
    recentWork: [{ ...dashboard.recentWork[0], internalStatus: 'secret' }],
  })).toThrow();
});

test('accepts a survey without a published version', () => {
  expect(() => dashboardViewSchema.parse({
    ...dashboard,
    surveys: [{ ...dashboard.surveys[0], publishedVersion: null }],
  })).not.toThrow();
});

test('accepts only existing survey-relative target routes for the matching survey', () => {
  const validPaths = [
    `/surveys/${surveyId}/edit`,
    `/surveys/${surveyId}/import`,
    `/surveys/${surveyId}/preview`,
    `/surveys/${surveyId}/publish`,
    `/surveys/${surveyId}/responses`,
    `/surveys/${surveyId}/versions/12`,
  ];
  for (const targetPath of validPaths) {
    expect(() => dashboardViewSchema.parse({
      ...dashboard,
      tasks: [{ ...dashboard.tasks[0], targetPath }],
    })).not.toThrow();
  }

  for (const targetPath of [
    'https://evil.example/surveys/1/edit',
    '/workspace',
    `/surveys/${surveyId}/settings`,
    `/surveys/${surveyId}/versions/0`,
    `/surveys/${surveyId}/versions/9007199254740992`,
    `/surveys/${surveyId}/edit?next=https://evil.example`,
    '/surveys/20000000-0000-4000-8000-000000000002/edit',
  ]) {
    expect(() => dashboardViewSchema.parse({
      ...dashboard,
      tasks: [{ ...dashboard.tasks[0], targetPath }],
    })).toThrow();
  }
});

test('serializes dashboard limits and parses the response', async () => {
  const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(dashboard));

  await expect(getDashboard(
    createApiClient({ fetchImpl }),
    { surveyLimit: 12, taskLimit: 7 },
  )).resolves.toEqual(dashboard);
  expect(fetchImpl.mock.calls[0]?.[0]).toBe('/v1/dashboard?surveyLimit=12&taskLimit=7');
});

test('accepts dashboard limit boundaries', async () => {
  const fetchImpl = vi.fn<typeof fetch>()
    .mockResolvedValueOnce(jsonResponse(dashboard))
    .mockResolvedValueOnce(jsonResponse(dashboard));
  const api = createApiClient({ fetchImpl });

  await getDashboard(api, { surveyLimit: 1, taskLimit: 1 });
  await getDashboard(api, { surveyLimit: 50, taskLimit: 50 });

  expect(fetchImpl.mock.calls.map(([path]) => path)).toEqual([
    '/v1/dashboard?surveyLimit=1&taskLimit=1',
    '/v1/dashboard?surveyLimit=50&taskLimit=50',
  ]);
});

test.each([
  ['surveyLimit', 0],
  ['surveyLimit', 51],
  ['surveyLimit', 1.5],
  ['surveyLimit', Number.MAX_SAFE_INTEGER + 1],
  ['surveyLimit', Number.NaN],
  ['taskLimit', 0],
  ['taskLimit', 51],
  ['taskLimit', 1.5],
  ['taskLimit', Number.MAX_SAFE_INTEGER + 1],
  ['taskLimit', Number.POSITIVE_INFINITY],
] as const)('rejects invalid dashboard option %s=%s before requesting', (key, value) => {
  const request = vi.fn();

  expect(() => getDashboard({ request }, { [key]: value })).toThrow();
  expect(request).not.toHaveBeenCalled();
});

test('uses server defaults when dashboard limits are omitted', async () => {
  const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(dashboard));

  await getDashboard(createApiClient({ fetchImpl }));

  expect(fetchImpl.mock.calls[0]?.[0]).toBe('/v1/dashboard');
});

test('records recent work through the ApiClient 204 convention', async () => {
  const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 204 }));

  await expect(recordRecentWork(createApiClient({ fetchImpl }), {
    surveyId,
    page: 'version',
    version: 3,
  })).resolves.toBeUndefined();
  expect(fetchImpl.mock.calls.map(([path, init]) => [path, init?.method, init?.body])).toEqual([
    [
      '/v1/dashboard/recent-work',
      'POST',
      JSON.stringify({ surveyId, page: 'version', version: 3 }),
    ],
  ]);
});

test('accepts recent work command integer boundaries', async () => {
  const request = vi.fn().mockResolvedValue(undefined);
  const api = { request };

  await recordRecentWork(api, { surveyId, page: 'edit', version: null });
  await recordRecentWork(api, { surveyId, page: 'version', version: 1 });
  await recordRecentWork(api, {
    surveyId,
    page: 'version',
    version: Number.MAX_SAFE_INTEGER,
  });

  expect(request.mock.calls.map(([request]) => request.body)).toEqual([
    { surveyId, page: 'edit', version: null },
    { surveyId, page: 'version', version: 1 },
    { surveyId, page: 'version', version: Number.MAX_SAFE_INTEGER },
  ]);
});

test.each([
  { surveyId: 'not-a-uuid', page: 'edit', version: null },
  { surveyId, page: 'edit' },
  { surveyId, page: 'edit', version: 1 },
  { surveyId, page: 'version', version: null },
  { surveyId, page: 'version', version: 0 },
  { surveyId, page: 'version', version: 1.5 },
  { surveyId, page: 'version', version: Number.MAX_SAFE_INTEGER + 1 },
  { surveyId, page: 'version', version: 1, targetPath: '/surveys/anything/edit' },
])('rejects invalid recent work command before requesting: $page/$version', (command) => {
  const request = vi.fn();

  expect(() => recordRecentWork({ request }, command as never)).toThrow();
  expect(request).not.toHaveBeenCalled();
});

test('isolates dashboard query keys by tenant', () => {
  expect(dashboardQueryKey('tenant-a')).toEqual(['dashboard', 'tenant-a']);
  expect(dashboardQueryKey('tenant-a')).not.toEqual(dashboardQueryKey('tenant-b'));
});
