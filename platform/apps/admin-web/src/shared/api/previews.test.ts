import { expect, test, vi } from 'vitest';
import { jsonResponse } from '../../test/server';
import {
  createPreviewClient,
  previewSessionQueryKey,
  previewSessionSchema,
} from './previews';

const surveyId = '10000000-0000-4000-8000-000000000001';
const sessionId = '20000000-0000-4000-8000-000000000001';
const requestId = '30000000-0000-4000-8000-000000000001';
const session = {
  id: sessionId,
  requestId,
  surveyId,
  draftVersion: 7,
  requestedBy: 'owner-a',
  engineInstanceId: 'engine-a',
  engineSid: 123456,
  generation: 'preview-generation-a',
  previewUrl: 'https://survey.example/v1/preview/access?token=opaque',
  expiresAt: '2026-10-09T09:30:00Z',
  status: 'ready',
  failure: null,
  cleanupAttempts: 0,
  createdAt: '2026-10-09T09:00:00Z',
  updatedAt: '2026-10-09T09:00:01Z',
  closedAt: null,
};

test('validates the complete preview session contract and known lifecycle states', () => {
  expect(previewSessionSchema.parse(session)).toEqual(session);
  for (const status of ['creating', 'ready', 'closing', 'closed', 'failed', 'cleanup_failed']) {
    expect(previewSessionSchema.parse({ ...session, status }).status).toBe(status);
  }
  expect(() => previewSessionSchema.parse({ ...session, status: 'active' })).toThrow();
  expect(() => previewSessionSchema.parse({ ...session, draftVersion: 1.5 })).toThrow();
});

test('creates a preview with the stable request id and accepts a 202 lifecycle response', async () => {
  const creating = { ...session, engineSid: null, generation: null, previewUrl: null, status: 'creating' };
  const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(creating, 202));
  const client = createPreviewClient({ fetchImpl, getToken: () => 'jwt-token' });

  await expect(client.create('survey/id', { requestId, ttlSeconds: 1800 })).resolves.toEqual(creating);

  const [path, init] = fetchImpl.mock.calls[0] ?? [];
  expect(path).toBe('/v1/surveys/survey%2Fid/preview-sessions');
  expect(init?.method).toBe('POST');
  expect(init?.body).toBe(JSON.stringify({ requestId, ttlSeconds: 1800 }));
  expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer jwt-token');
});

test('loads and closes the same preview session and accepts closing responses', async () => {
  const fetchImpl = vi.fn<typeof fetch>()
    .mockResolvedValueOnce(jsonResponse(session))
    .mockResolvedValueOnce(jsonResponse({ ...session, status: 'closing' }, 202));
  const client = createPreviewClient({ fetchImpl });

  await client.get('session/id');
  await client.close('session/id');

  expect(fetchImpl.mock.calls.map(([path, init]) => [path, init?.method])).toEqual([
    ['/v1/preview-sessions/session%2Fid', 'GET'],
    ['/v1/preview-sessions/session%2Fid', 'DELETE'],
  ]);
});

test('preview query keys are isolated by tenant survey and session', () => {
  const keys = [
    previewSessionQueryKey('tenant-a', surveyId, sessionId),
    previewSessionQueryKey('tenant-b', surveyId, sessionId),
    previewSessionQueryKey('tenant-a', '40000000-0000-4000-8000-000000000001', sessionId),
    previewSessionQueryKey('tenant-a', surveyId, '50000000-0000-4000-8000-000000000001'),
  ];
  expect(new Set(keys.map((key) => JSON.stringify(key)))).toHaveLength(keys.length);
});

test('normalizes a stalled request to a Chinese unavailable error and cleans timeout state', async () => {
  vi.useFakeTimers();
  const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async (_input, init) =>
    new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true });
    }));
  try {
    const request = createPreviewClient({ fetchImpl, timeoutMs: 50 }).get(sessionId);
    const rejection = expect(request).rejects.toMatchObject({ kind: 'unavailable' });
    await vi.advanceTimersByTimeAsync(50);
    await rejection;
    expect(vi.getTimerCount()).toBe(0);
  } finally {
    vi.useRealTimers();
  }
});
