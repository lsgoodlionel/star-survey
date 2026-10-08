import { expect, test, vi } from 'vitest';
import { z } from 'zod';
import { createApiClient } from './http';

const responseSchema = z.object({ ok: z.boolean() });

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

test('rejectsUnsafeApiUrlsBeforeReadingTheBearerToken', async () => {
  const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ ok: true }));
  const getToken = vi.fn(() => 'secret-jwt');
  const options = { fetchImpl, getToken, locationOrigin: 'https://admin.example' };
  const api = createApiClient(options);

  for (const path of [
    'https://attacker.example/v1/me',
    '//attacker.example/v1/me',
    '//admin.example/v1/me',
    '/api/me',
    '/v10/me',
  ]) {
    await expect(api.request({ path, schema: responseSchema })).rejects.toMatchObject({
      kind: 'unexpected',
      message: '请求地址无效',
    });
  }

  expect(getToken).not.toHaveBeenCalled();
  expect(fetchImpl).not.toHaveBeenCalled();
});

test('normalizesSameOriginV1UrlsBeforeFetching', async () => {
  const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ ok: true }));
  const options = { fetchImpl, locationOrigin: 'https://admin.example' };
  const api = createApiClient(options);

  await expect(
    api.request({ path: 'https://admin.example/v1/me?view=compact', schema: responseSchema }),
  ).resolves.toEqual({ ok: true });

  expect(fetchImpl).toHaveBeenCalledWith('/v1/me?view=compact', expect.any(Object));
});

test('awaits401CleanupBeforeParsingAnInvalidErrorBody', async () => {
  const events: string[] = [];
  let finishCleanup!: () => void;
  const cleanupGate = new Promise<void>((resolve) => {
    finishCleanup = resolve;
  });
  const response = {
    status: 401,
    ok: false,
    headers: new Headers({ 'Content-Type': 'application/json' }),
    json: vi.fn(async () => {
      events.push('parse');
      throw new SyntaxError('invalid json');
    }),
  } as unknown as Response;
  const onUnauthorized = vi.fn(async (requestToken: string | null) => {
    events.push(`cleanup:${requestToken}`);
    await cleanupGate;
    events.push('cleanup:done');
  });
  const api = createApiClient({
    fetchImpl: vi.fn<typeof fetch>().mockResolvedValue(response),
    getToken: () => 'expired-token',
    onUnauthorized,
  });

  const result = api.request({ path: '/v1/private', schema: responseSchema });
  const rejection = expect(result).rejects.toMatchObject({ kind: 'unauthenticated', status: 401 });
  await vi.waitFor(() => expect(onUnauthorized).toHaveBeenCalledWith('expired-token'));
  expect(events).toEqual(['cleanup:expired-token']);

  finishCleanup();
  await rejection;
  expect(events).toEqual(['cleanup:expired-token', 'cleanup:done', 'parse']);
});

test('composesCallerAbortWithTheRequestTimeout', async () => {
  const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async (_input, init) => {
    const signal = init?.signal;
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
    return new Promise<Response>((_resolve, reject) => {
      signal?.addEventListener(
        'abort',
        () => reject(new DOMException('Aborted', 'AbortError')),
        { once: true },
      );
    });
  });
  const api = createApiClient({ fetchImpl });
  const alreadyAborted = new AbortController();
  alreadyAborted.abort();

  await expect(
    api.request({ path: '/v1/pre-aborted', schema: responseSchema, signal: alreadyAborted.signal }),
  ).rejects.toMatchObject({ kind: 'unavailable' });
  expect(fetchImpl.mock.calls[0]?.[1]?.signal).toMatchObject({ aborted: true });

  const caller = new AbortController();
  const running = api.request({ path: '/v1/running', schema: responseSchema, signal: caller.signal });
  caller.abort();
  await expect(running).rejects.toMatchObject({ kind: 'unavailable' });

  vi.useFakeTimers();
  const timedOut = api.request({ path: '/v1/timeout', schema: responseSchema, timeoutMs: 50 });
  const timeoutRejection = expect(timedOut).rejects.toMatchObject({ kind: 'unavailable' });
  await vi.advanceTimersByTimeAsync(50);
  await timeoutRejection;
  vi.useRealTimers();
});
