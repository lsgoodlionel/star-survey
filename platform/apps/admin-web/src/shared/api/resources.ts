import { z } from 'zod';
import type { ApiClient } from './http';

export const resourceKindSchema = z.enum(['project', 'folder', 'survey']);

export const resourceViewSchema = z.object({
  id: z.string().uuid(),
  kind: resourceKindSchema,
  parentId: z.string().uuid().nullable(),
  name: z.string(),
  createdAt: z.string().datetime({ offset: true }),
  updatedAt: z.string().datetime({ offset: true }),
  archivedAt: z.string().datetime({ offset: true }).nullable(),
});

export const resourcePageSchema = z.object({
  items: z.array(resourceViewSchema),
  nextCursor: z.string().nullable(),
});

export const resourceCapabilitiesSchema = z.object({
  canCreateProject: z.boolean(),
  canCreateChildren: z.boolean(),
  canEdit: z.boolean(),
  canSubmitApproval: z.boolean(),
  canPublishDirectly: z.boolean(),
  canApprovePublish: z.boolean(),
  canArchive: z.boolean(),
  canRestore: z.boolean(),
});

export type ResourceKind = z.infer<typeof resourceKindSchema>;
export type ResourceView = z.infer<typeof resourceViewSchema>;
export type ResourcePage = z.infer<typeof resourcePageSchema>;
export type ResourceCapabilities = z.infer<typeof resourceCapabilitiesSchema>;

export interface ResourceFilters {
  query?: string;
  kind?: ResourceKind;
  archived?: 'active' | 'archived';
  sort?: 'updated_desc' | 'name_asc';
}

export interface NormalizedResourceFilters {
  query: string | null;
  kind: ResourceKind | null;
  archived: 'active' | 'archived';
  sort: 'updated_desc' | 'name_asc';
}

export interface ListResourcesOptions extends ResourceFilters {
  parentId?: string | null;
  cursor?: string | null;
  signal?: AbortSignal;
}

export function normalizeResourceFilters(
  filters: ResourceFilters = {},
): NormalizedResourceFilters {
  const strippedQuery = filters.query?.trim();
  return {
    query: strippedQuery ? strippedQuery.normalize('NFC') : null,
    kind: filters.kind ?? null,
    archived: filters.archived ?? 'active',
    sort: filters.sort ?? 'updated_desc',
  };
}

export const resourceQueryKey = (
  tenantId: string,
  parentId: string | null,
  filters: ResourceFilters = {},
) => ['resources', tenantId, parentId, normalizeResourceFilters(filters)] as const;

export function listResources(api: ApiClient, options: ListResourcesOptions = {}) {
  const filters = normalizeResourceFilters(options);
  const search = new URLSearchParams();
  if (options.parentId) search.set('parentId', options.parentId);
  if (filters.query) search.set('query', filters.query);
  if (filters.kind) search.set('kind', filters.kind);
  search.set('archived', filters.archived);
  search.set('sort', filters.sort);
  if (options.cursor?.trim()) search.set('cursor', options.cursor);
  const query = search.toString();
  return api.request({
    path: `/v1/resources${query ? `?${query}` : ''}`,
    schema: resourcePageSchema,
    signal: options.signal,
  });
}

export function getResource(api: ApiClient, id: string, signal?: AbortSignal) {
  return api.request({
    path: `/v1/resources/${encodeURIComponent(id)}`,
    schema: resourceViewSchema,
    signal,
  });
}

export async function getResourcePath(api: ApiClient, id: string, signal?: AbortSignal) {
  const path: ResourceView[] = [];
  const visited = new Set<string>();
  let currentId: string | null = id;
  while (currentId) {
    if (visited.has(currentId)) throw new Error('资源层级无效');
    visited.add(currentId);
    const resource = await getResource(api, currentId, signal);
    path.push(resource);
    currentId = resource.parentId;
  }
  return path.reverse();
}

export function getResourceCapabilities(api: ApiClient, resourceId?: string, signal?: AbortSignal) {
  const search = resourceId ? `?resourceId=${encodeURIComponent(resourceId)}` : '';
  return api.request({
    path: `/v1/resource-capabilities${search}`,
    schema: resourceCapabilitiesSchema,
    signal,
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

export function renameResource(api: ApiClient, id: string, name: string, signal?: AbortSignal) {
  return api.request({
    path: `/v1/resources/${encodeURIComponent(id)}`,
    method: 'PATCH',
    body: { name },
    schema: resourceViewSchema,
    signal,
  });
}

export function moveResource(api: ApiClient, id: string, parentId: string, signal?: AbortSignal) {
  return api.request({
    path: `/v1/resources/${encodeURIComponent(id)}/move`,
    method: 'POST',
    body: { parentId },
    schema: resourceViewSchema,
    signal,
  });
}

export function archiveResource(api: ApiClient, id: string, signal?: AbortSignal) {
  return changeResourceArchiveState(api, id, 'archive', signal);
}

export function restoreResource(api: ApiClient, id: string, signal?: AbortSignal) {
  return changeResourceArchiveState(api, id, 'restore', signal);
}

function changeResourceArchiveState(
  api: ApiClient,
  id: string,
  action: 'archive' | 'restore',
  signal?: AbortSignal,
) {
  return api.request({
    path: `/v1/resources/${encodeURIComponent(id)}/${action}`,
    method: 'POST',
    schema: resourceViewSchema,
    signal,
  });
}
