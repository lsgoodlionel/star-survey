import { expect, test, vi } from 'vitest';
import { createApiClient } from './http';
import { jsonResponse } from '../../test/server';
import { assetViewSchema, listAssets, uploadAsset, imageAssets, assetListQueryKey } from './assets';

const asset = { id: '10000000-0000-4000-8000-000000000001', name: 'Logo', kind: 'image' as const,
  status: 'active' as const, currentVersion: 1, contentType: 'image/png', byteSize: 4,
  sha256: 'a'.repeat(64), createdAt: '2026-10-27T08:00:00Z', updatedAt: '2026-10-27T08:00:00Z' };

test('validates asset enum and metadata and filters selectable images', () => {
  expect(assetViewSchema.parse(asset)).toEqual(asset);
  expect(imageAssets([asset, { ...asset, id: '20000000-0000-4000-8000-000000000001', kind: 'audio', contentType: 'audio/ogg' },
    { ...asset, id: '30000000-0000-4000-8000-000000000001', status: 'archived' },
    { ...asset, id: '40000000-0000-4000-8000-000000000001', contentType: 'image/svg+xml' }])).toEqual([asset]);
  for (const change of [{ id: 'bad' }, { currentVersion: 0 }, { byteSize: -1 },
    { createdAt: 'bad' }, { status: 'gone' }, { kind: 'unknown' }, { sha256: 'short' }]) {
    expect(() => assetViewSchema.parse({ ...asset, ...change })).toThrow();
  }
});

test('list and upload use tenant-scoped keys and real multipart without manual content type', async () => {
  const fetchImpl = vi.fn<typeof fetch>()
    .mockResolvedValueOnce(jsonResponse([asset]))
    .mockResolvedValueOnce(jsonResponse(asset));
  const api = createApiClient({ fetchImpl });
  await expect(listAssets(api, 20)).resolves.toEqual([asset]);
  const file = new File(['png!'], 'logo.png', { type: 'image/png' });
  await uploadAsset(api, '品牌 Logo', file);
  expect(fetchImpl.mock.calls[0]?.[0]).toBe('/v1/assets?limit=20');
  const [url, init] = fetchImpl.mock.calls[1] ?? [];
  expect(url).toBe('/v1/assets');
  expect(init?.method).toBe('POST');
  expect(init?.body).toBeInstanceOf(FormData);
  const body = init?.body as FormData;
  expect(body.get('name')).toBe('品牌 Logo');
  expect(body.get('file')).toBe(file);
  expect(new Headers(init?.headers).has('Content-Type')).toBe(false);
  expect(assetListQueryKey('a')).not.toEqual(assetListQueryKey('b'));
});
