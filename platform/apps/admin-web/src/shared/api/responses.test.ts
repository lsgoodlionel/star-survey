import { expect, test, vi } from 'vitest';
import { createApiClient } from './http';
import {
  getResponseSummary,
  listResponses,
  normalizeResponseFilters,
  responsePageQueryKey,
  responsePageSchema,
  responseSummaryQueryKey,
  responseSummarySchema,
  type ResponseFilters,
} from './responses';
import { jsonResponse } from '../../test/server';

const surveyId = '10000000-0000-4000-8000-000000000001';
const row = {
  version: 2,
  engineInstanceId: 'engine-cn-1',
  engineSid: 731245,
  generation: 'generation-2',
  responseId: 42,
  state: 'engine_completed',
  startedAt: '2026-10-09T08:00:00Z',
  completedAt: '2026-10-09T08:03:00Z',
  deletedAt: null,
  answersStatus: 'available',
  answers: { Q1: '满意', Q2: null },
};
const page = { items: [row], nextCursor: 'v2:+/=?&', sensitiveRevealed: false };
const summary = {
  surveyId,
  versions: [
    { version: 1, counts: { inProgress: 1, engineCompleted: 2, deleted: 0 } },
    { version: 2, counts: { inProgress: 0, engineCompleted: 3, deleted: 1 } },
  ],
  total: { inProgress: 1, engineCompleted: 5, deleted: 1 },
};

test('validates response summaries pages and nullable answer values', () => {
  expect(responseSummarySchema.parse(summary)).toEqual(summary);
  expect(responsePageSchema.parse(page)).toEqual(page);
  expect(() => responsePageSchema.parse({ ...page, items: [{ ...row, state: 'finished' }] })).toThrow();
  expect(() => responsePageSchema.parse({ ...page, sensitiveRevealed: 'no' })).toThrow();
});

test('normalizes blank response filters without changing opaque cursors', () => {
  expect(normalizeResponseFilters({ state: undefined, cursor: '  opaque cursor  ' })).toEqual({
    state: null,
    version: null,
    startedFrom: null,
    startedTo: null,
    completedFrom: null,
    completedTo: null,
    limit: 50,
    cursor: '  opaque cursor  ',
  });
});

test('encodes every response filter and preserves the opaque cursor', async () => {
  const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(page));
  const filters: ResponseFilters = {
    state: 'engine_completed',
    version: 2,
    startedFrom: '2026-10-01T00:00:00Z',
    startedTo: '2026-10-31T23:59:59Z',
    completedFrom: '2026-10-02T00:00:00Z',
    completedTo: '2026-10-30T23:59:59Z',
    limit: 100,
    cursor: 'v2:+/=?&',
  };

  await listResponses(createApiClient({ fetchImpl }), 'survey/id', filters);

  expect(fetchImpl).toHaveBeenCalledWith(
    '/v1/surveys/survey%2Fid/responses?state=engine_completed&version=2&startedFrom=2026-10-01T00%3A00%3A00Z&startedTo=2026-10-31T23%3A59%3A59Z&completedFrom=2026-10-02T00%3A00%3A00Z&completedTo=2026-10-30T23%3A59%3A59Z&limit=100&cursor=v2%3A%2B%2F%3D%3F%26',
    expect.any(Object),
  );
});

test('response query keys include tenant survey every filter and cursor', () => {
  const base: ResponseFilters = { state: 'in_progress', version: 2, limit: 25, cursor: 'cursor-a' };
  const keys = [
    responsePageQueryKey('tenant-a', surveyId, base),
    responsePageQueryKey('tenant-b', surveyId, base),
    responsePageQueryKey('tenant-a', '20000000-0000-4000-8000-000000000001', base),
    responsePageQueryKey('tenant-a', surveyId, { ...base, state: 'deleted' }),
    responsePageQueryKey('tenant-a', surveyId, { ...base, version: 3 }),
    responsePageQueryKey('tenant-a', surveyId, { ...base, limit: 50 }),
    responsePageQueryKey('tenant-a', surveyId, { ...base, cursor: 'cursor-b' }),
  ];
  expect(new Set(keys.map((key) => JSON.stringify(key)))).toHaveLength(keys.length);
  expect(responseSummaryQueryKey('tenant-a', surveyId)).not.toEqual(
    responseSummaryQueryKey('tenant-b', surveyId),
  );
});

test('requests the survey scoped response summary', async () => {
  const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(summary));
  await expect(getResponseSummary(createApiClient({ fetchImpl }), 'survey/id')).resolves.toEqual(summary);
  expect(fetchImpl).toHaveBeenCalledWith(
    '/v1/surveys/survey%2Fid/responses/summary',
    expect.any(Object),
  );
});
