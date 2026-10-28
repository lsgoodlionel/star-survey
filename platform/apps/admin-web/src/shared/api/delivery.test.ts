import { expect, test, vi } from 'vitest';
import { createApiClient } from './http';
import {
  cancelDeliveryTask,
  createAudienceDeliveryTask,
  createDeliveryLink,
  deliveryLinksQueryKey,
  deliveryRecipientSchema,
  deliveryTaskSchema,
  reminderRuleSchema,
  fetchDeliveryQr,
  createReminderRule,
  getDeliveryTask,
  linkViewSchema,
  listReminderRules,
  listSurveyDeliveryTasks,
  listDeliveryRecipients,
  listDeliveryLinks,
  revokeDeliveryLink,
  setReminderRuleEnabled,
} from './delivery';
import { binaryResponse, jsonResponse } from '../../test/server';

const surveyId = '10000000-0000-4000-8000-000000000001';
const linkId = '20000000-0000-4000-8000-000000000001';
const link = {
  id: linkId,
  surveyId,
  label: '微信公众号',
  signedParams: { source: 'wechat', exp: '1798761600', sig: 'signed-value' },
  url: 'https://survey.example/s/abc?source=wechat&exp=1798761600&sig=signed-value',
  shortUrl: 'https://survey.example/s/abc',
  expiresAt: '2027-01-01T00:00:00Z',
  revokedAt: null,
  createdAt: '2026-10-09T08:00:00Z',
};
const taskId = '30000000-0000-4000-8000-000000000001';
const task = {
  id: taskId, surveyId, channel: 'email', kind: 'delivery', status: 'queued', sourceTaskId: null,
  recipientCount: 2, sentCount: 0, failedCount: 0, skippedCount: 0, billedUnits: 0, attempts: 0,
  lastError: null, createdAt: '2026-10-28T08:00:00Z', finishedAt: null,
};
const recipient = {
  id: '40000000-0000-4000-8000-000000000001', address: 'w***@example.test', displayName: '王小明',
  state: 'queued', attempts: 0, remindersSent: 0, lastError: null, sentAt: null, lastReminderAt: null,
};
const reminderRule = {
  id: '50000000-0000-4000-8000-000000000001', surveyId, sourceTaskId: taskId,
  firstDelaySeconds: 3600, intervalSeconds: 86400, maxReminders: 2, subject: '请继续填写',
  enabled: true, createdAt: '2026-10-29T08:00:00Z',
};

test('validates the complete delivery link contract', () => {
  expect(linkViewSchema.parse(link)).toEqual(link);
  expect(() => linkViewSchema.parse({ ...link, signedParams: { exp: 123 } })).toThrow();
  expect(() => linkViewSchema.parse({ ...link, createdAt: 'today' })).toThrow();
});

test('validates task state and redacted recipient contracts', () => {
  expect(deliveryTaskSchema.parse(task)).toEqual(task);
  expect(deliveryRecipientSchema.parse(recipient)).toEqual(recipient);
  expect(() => deliveryTaskSchema.parse({ ...task, recipientCount: -1 })).toThrow();
  expect(reminderRuleSchema.parse(reminderRule)).toEqual(reminderRule);
  expect(() => reminderRuleSchema.parse({ ...reminderRule, maxReminders: 11 })).toThrow();
});

test('lists survey tasks and manages only typed reminder rules', async () => {
  const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
    const path = String(input);
    if (path.includes('reminder-rules') && init?.method === 'POST') return jsonResponse(reminderRule, 201);
    if (path.includes('reminder-rules') && init?.method === 'PATCH') return jsonResponse({ ...reminderRule, enabled: false });
    if (path.includes('reminder-rules')) return jsonResponse([reminderRule]);
    return jsonResponse([task]);
  });
  const api = createApiClient({ fetchImpl });
  await listSurveyDeliveryTasks(api, 'survey/id');
  await listReminderRules(api, 'survey/id');
  await createReminderRule(api, 'survey/id', {
    sourceTaskId: 'task/id', firstDelaySeconds: 0, intervalSeconds: 60, maxReminders: 2,
    subject: null, bodyTemplate: '请点击 {{unsubscribe}}',
  });
  await setReminderRuleEnabled(api, 'survey/id', 'rule/id', false);
  expect(fetchImpl.mock.calls.map(([path, init]) => [path, init?.method, init?.body])).toEqual([
    ['/v1/surveys/survey%2Fid/delivery-tasks?limit=50', 'GET', undefined],
    ['/v1/delivery/surveys/survey%2Fid/reminder-rules', 'GET', undefined],
    ['/v1/delivery/surveys/survey%2Fid/reminder-rules', 'POST', JSON.stringify({
      sourceTaskId: 'task/id', firstDelaySeconds: 0, intervalSeconds: 60, maxReminders: 2,
      subject: null, bodyTemplate: '请点击 {{unsubscribe}}',
    })],
    ['/v1/delivery/surveys/survey%2Fid/reminder-rules/rule%2Fid', 'PATCH', JSON.stringify({ enabled: false })],
  ]);
});

test('uses the audience scoped create endpoint and task status endpoints', async () => {
  const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
    if (String(input).endsWith('/recipients')) return jsonResponse([recipient]);
    return jsonResponse(task, init?.method === 'POST' && String(input).includes('/surveys/') ? 201 : 200);
  });
  const api = createApiClient({ fetchImpl });
  await createAudienceDeliveryTask(api, 'survey/id', {
    channel: 'email', subject: '邀请填写', bodyTemplate: '请填写问卷', idempotencyKey: 'request-1',
  });
  await getDeliveryTask(api, 'task/id');
  await listDeliveryRecipients(api, 'task/id');
  await cancelDeliveryTask(api, 'task/id');
  expect(fetchImpl.mock.calls.map(([path, init]) => [path, init?.method, init?.body])).toEqual([
    ['/v1/surveys/survey%2Fid/delivery-tasks', 'POST', JSON.stringify({ channel: 'email', subject: '邀请填写', bodyTemplate: '请填写问卷', idempotencyKey: 'request-1' })],
    ['/v1/delivery/tasks/task%2Fid', 'GET', undefined],
    ['/v1/delivery/tasks/task%2Fid/recipients', 'GET', undefined],
    ['/v1/delivery/tasks/task%2Fid/cancel', 'POST', undefined],
  ]);
});

test('isolates delivery link queries by tenant and survey', () => {
  const keys = [
    deliveryLinksQueryKey('tenant-a', surveyId),
    deliveryLinksQueryKey('tenant-b', surveyId),
    deliveryLinksQueryKey('tenant-a', '30000000-0000-4000-8000-000000000001'),
  ];
  expect(new Set(keys.map((key) => JSON.stringify(key)))).toHaveLength(3);
});

test('sends exact delivery list create and revoke requests', async () => {
  const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
    if (init?.method === 'POST') return jsonResponse(link, 201);
    if (init?.method === 'DELETE') return jsonResponse({ ...link, revokedAt: '2026-10-09T09:00:00Z' });
    return jsonResponse([link]);
  });
  const api = createApiClient({ fetchImpl });

  await listDeliveryLinks(api, 'survey/id');
  await createDeliveryLink(api, 'survey/id', {
    label: '微信公众号',
    params: { source: 'wechat' },
    ttlSeconds: 3600,
    shortLink: true,
  });
  await revokeDeliveryLink(api, 'link/id');

  expect(fetchImpl.mock.calls.map(([path, init]) => [path, init?.method, init?.body])).toEqual([
    ['/v1/delivery/surveys/survey%2Fid/links', 'GET', undefined],
    [
      '/v1/delivery/surveys/survey%2Fid/links',
      'POST',
      JSON.stringify({
        label: '微信公众号',
        params: { source: 'wechat' },
        ttlSeconds: 3600,
        shortLink: true,
      }),
    ],
    ['/v1/delivery/links/link%2Fid', 'DELETE', undefined],
  ]);
});

test('loads the server generated QR as an authenticated blob', async () => {
  vi.useFakeTimers();
  const svg = '<svg xmlns="http://www.w3.org/2000/svg" />';
  const caller = new AbortController();
  const removeEventListener = vi.spyOn(caller.signal, 'removeEventListener');
  const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
    binaryResponse(svg, { contentType: 'image/svg+xml' }),
  );

  try {
    const result = await fetchDeliveryQr(
      { fetchImpl, getToken: () => 'jwt-token' },
      'link/id',
      { format: 'svg', moduleSize: 8, signal: caller.signal },
    );

    expect(await result.text()).toBe(svg);
    const [path, init] = fetchImpl.mock.calls[0] ?? [];
    expect(path).toBe('/v1/delivery/links/link%2Fid/qr?format=svg&moduleSize=8');
    expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer jwt-token');
    expect(init).toMatchObject({ credentials: 'same-origin' });
    expect(removeEventListener).toHaveBeenCalledWith('abort', expect.any(Function));
    expect(vi.getTimerCount()).toBe(0);
  } finally {
    vi.useRealTimers();
  }
});

test('rejects a QR response with the wrong media type', async () => {
  await expect(
    fetchDeliveryQr(
      { fetchImpl: vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ error: 'not an image' })) },
      linkId,
      { format: 'png' },
    ),
  ).rejects.toThrow('二维码响应格式无效');
});

test('times out a stalled QR request without aborting the caller signal', async () => {
  vi.useFakeTimers();
  const caller = new AbortController();
  const removeEventListener = vi.spyOn(caller.signal, 'removeEventListener');
  const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async (_input, init) => {
    const signal = init?.signal;
    return new Promise<Response>((_resolve, reject) => {
      signal?.addEventListener(
        'abort',
        () => reject(new DOMException('Aborted', 'AbortError')),
        { once: true },
      );
    });
  });

  try {
    const request = fetchDeliveryQr(
      { fetchImpl, timeoutMs: 50 },
      linkId,
      { signal: caller.signal },
    );
    const rejection = expect(request).rejects.toMatchObject({ kind: 'unavailable' });

    await vi.advanceTimersByTimeAsync(50);
    await rejection;

    expect(caller.signal.aborted).toBe(false);
    expect(fetchImpl.mock.calls[0]?.[1]?.signal).not.toBe(caller.signal);
    expect(removeEventListener).toHaveBeenCalledWith('abort', expect.any(Function));
  } finally {
    vi.useRealTimers();
  }
});

test('default timeout cancels a QR response whose blob body remains pending', async () => {
  vi.useFakeTimers();
  const caller = new AbortController();
  const removeEventListener = vi.spyOn(caller.signal, 'removeEventListener');
  let bodyStarted!: () => void;
  const started = new Promise<void>((resolve) => {
    bodyStarted = resolve;
  });
  const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async (_input, init) => {
    const signal = init?.signal;
    return {
      status: 200,
      ok: true,
      headers: new Headers({ 'Content-Type': 'image/png' }),
      blob: () => new Promise<Blob>((_resolve, reject) => {
        bodyStarted();
        signal?.addEventListener(
          'abort',
          () => reject(new DOMException('Aborted', 'AbortError')),
          { once: true },
        );
      }),
    } as Response;
  });

  try {
    const request = fetchDeliveryQr(
      { fetchImpl },
      linkId,
      { signal: caller.signal },
    );
    let outcome: unknown = 'pending';
    void request.catch((error: unknown) => {
      outcome = error;
    });
    await started;

    await vi.advanceTimersByTimeAsync(15_000);
    await Promise.resolve();

    expect(outcome).toMatchObject({ kind: 'unavailable' });
    await expect(request).rejects.toMatchObject({ kind: 'unavailable' });
    expect(caller.signal.aborted).toBe(false);
    expect(removeEventListener).toHaveBeenCalledWith('abort', expect.any(Function));
    expect(vi.getTimerCount()).toBe(0);
  } finally {
    vi.useRealTimers();
  }
});

test('caller abort cancels a pending QR blob and clears timeout resources', async () => {
  vi.useFakeTimers();
  const caller = new AbortController();
  const removeEventListener = vi.spyOn(caller.signal, 'removeEventListener');
  let bodyStarted!: () => void;
  const started = new Promise<void>((resolve) => {
    bodyStarted = resolve;
  });
  const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async (_input, init) => {
    const signal = init?.signal;
    return {
      status: 200,
      ok: true,
      headers: new Headers({ 'Content-Type': 'image/png' }),
      blob: () => new Promise<Blob>((_resolve, reject) => {
        bodyStarted();
        signal?.addEventListener(
          'abort',
          () => reject(new DOMException('Aborted', 'AbortError')),
          { once: true },
        );
      }),
    } as Response;
  });

  try {
    const request = fetchDeliveryQr(
      { fetchImpl },
      linkId,
      { signal: caller.signal },
    );
    let outcome: unknown = 'pending';
    void request.catch((error: unknown) => {
      outcome = error;
    });
    await started;

    caller.abort();
    await vi.advanceTimersByTimeAsync(0);

    expect(outcome).toMatchObject({ kind: 'unavailable' });
    await expect(request).rejects.toMatchObject({ kind: 'unavailable' });
    expect(caller.signal.aborted).toBe(true);
    expect(removeEventListener).toHaveBeenCalledWith('abort', expect.any(Function));
    expect(vi.getTimerCount()).toBe(0);
  } finally {
    vi.useRealTimers();
  }
});
