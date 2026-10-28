import { z } from 'zod';
import type { ApiClient } from './http';

export const assetViewSchema = z.object({
  id: z.string().uuid(), name: z.string(), kind: z.enum(['image', 'audio', 'video']),
  status: z.enum(['active', 'archived']), currentVersion: z.number().int().positive(),
  contentType: z.string(), byteSize: z.number().int().nonnegative(),
  sha256: z.string().regex(/^[0-9a-f]{64}$/),
  createdAt: z.string().datetime({ offset: true }), updatedAt: z.string().datetime({ offset: true }),
});
export type AssetView = z.infer<typeof assetViewSchema>;
const assetListSchema = z.array(assetViewSchema);
const brandImageTypes = new Set(['image/png', 'image/jpeg', 'image/gif', 'image/webp']);

export const assetListQueryKey = (tenantId: string) => ['assets', tenantId, 'list'] as const;
export function imageAssets(assets: AssetView[]): AssetView[] {
  return assets.filter((asset) => asset.status === 'active' && asset.kind === 'image'
    && brandImageTypes.has(asset.contentType));
}
export function listAssets(api: ApiClient, limit?: number, signal?: AbortSignal) {
  const query = limit === undefined ? '' : `?limit=${encodeURIComponent(String(limit))}`;
  return api.request({ path: `/v1/assets${query}`, schema: assetListSchema, signal });
}
export function uploadAsset(api: ApiClient, name: string, file: File, signal?: AbortSignal) {
  const form = new FormData();
  form.append('name', name);
  form.append('file', file);
  return api.request({ path: '/v1/assets', method: 'POST', body: form, schema: assetViewSchema, signal });
}
