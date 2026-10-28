import { expect, test, vi } from 'vitest';
import { createApiClient } from './http';
import { audienceSummarySchema, getSurveyAudience, setSurveyAudience } from './audience';
import { jsonResponse } from '../../test/server';

const surveyId = '10000000-0000-4000-8000-000000000001';
const listId = '20000000-0000-4000-8000-000000000001';
const summary = {
  audience: { surveyId, listId, orgUnitId: null, includeDescendants: true, tag: null, updatedBy: 'actor-1', updatedAt: '2026-10-28T08:00:00Z' },
  recipients: 3,
};

test('validates the audience summary without a recipient roster', () => {
  expect(audienceSummarySchema.parse(summary)).toEqual(summary);
  expect(() => audienceSummarySchema.parse({ ...summary, recipients: -1 })).toThrow();
});

test('reads and writes the audience using the survey scoped endpoint', async () => {
  const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async () => jsonResponse(summary));
  const api = createApiClient({ fetchImpl });
  await getSurveyAudience(api, 'survey/id');
  await setSurveyAudience(api, 'survey/id', { listId, includeDescendants: false });
  expect(fetchImpl.mock.calls.map(([path, init]) => [path, init?.method, init?.body])).toEqual([
    ['/v1/surveys/survey%2Fid/audience', 'GET', undefined],
    ['/v1/surveys/survey%2Fid/audience', 'PUT', JSON.stringify({ listId, includeDescendants: false })],
  ]);
});
