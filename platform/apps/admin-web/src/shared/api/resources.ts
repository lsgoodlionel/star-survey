import { z } from 'zod';
import type { ApiClient } from './http';

export const resourceKindSchema = z.enum(['project', 'folder', 'survey']);

export const resourceViewSchema = z.object({
  id: z.string().uuid(),
  kind: resourceKindSchema,
  parentId: z.string().uuid().nullable(),
  name: z.string(),
  createdAt: z.string().datetime({ offset: true }),
});

export const resourcePageSchema = z.object({
  items: z.array(resourceViewSchema),
  nextCursor: z.string().nullable(),
});

export type ResourceKind = z.infer<typeof resourceKindSchema>;
export type ResourceView = z.infer<typeof resourceViewSchema>;
export type ResourcePage = z.infer<typeof resourcePageSchema>;

export interface ListResourcesOptions {
  parentId?: string | null;
  cursor?: string | null;
  signal?: AbortSignal;
}

export function listResources(api: ApiClient, options: ListResourcesOptions = {}) {
  const search = new URLSearchParams();
  if (options.parentId) search.set('parentId', options.parentId);
  if (options.cursor) search.set('cursor', options.cursor);
  const query = search.toString();
  return api.request({
    path: `/v1/resources${query ? `?${query}` : ''}`,
    schema: resourcePageSchema,
    signal: options.signal,
  });
}

export function createProject(api: ApiClient, name: string) {
  return api.request({
    path: '/v1/projects',
    method: 'POST',
    body: { name },
    schema: resourceViewSchema,
  });
}

export function createFolder(api: ApiClient, parentId: string, name: string) {
  return api.request({
    path: '/v1/folders',
    method: 'POST',
    body: { parentId, name },
    schema: resourceViewSchema,
  });
}
