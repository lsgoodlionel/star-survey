import { expect, test, vi } from 'vitest';
import { createApiClient } from './http';
import {
  createDeliveryLink,
  deliveryLinksQueryKey,
  fetchDeliveryQr,
  linkViewSchema,
  listDeliveryLinks,
  revokeDeliveryLink,
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

test('validates the complete delivery link contract', () => {
  expect(linkViewSchema.parse(link)).toEqual(link);
  expect(() => linkViewSchema.parse({ ...link, signedParams: { exp: 123 } })).toThrow();
  expect(() => linkViewSchema.parse({ ...link, createdAt: 'today' })).toThrow();
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
  const svg = '<svg xmlns="http://www.w3.org/2000/svg" />';
  const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
    binaryResponse(svg, { contentType: 'image/svg+xml' }),
  );

  const result = await fetchDeliveryQr(
    { fetchImpl, getToken: () => 'jwt-token' },
    'link/id',
    { format: 'svg', moduleSize: 8 },
  );

  expect(await result.text()).toBe(svg);
  const [path, init] = fetchImpl.mock.calls[0] ?? [];
  expect(path).toBe('/v1/delivery/links/link%2Fid/qr?format=svg&moduleSize=8');
  expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer jwt-token');
  expect(init).toMatchObject({ credentials: 'same-origin' });
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
