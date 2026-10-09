import { expect, test, vi } from 'vitest';
import { createApiClient } from './http';
import {
  archiveResource,
  listResources,
  moveResource,
  renameResource,
  resourceCapabilitiesSchema,
  resourceQueryKey,
  resourceViewSchema,
  restoreResource,
  type ResourceFilters,
} from './resources';
import { jsonResponse } from '../../test/server';

const resourceId = '10000000-0000-4000-8000-000000000001';
const parentId = '20000000-0000-4000-8000-000000000001';
const resource = {
  id: resourceId,
  kind: 'folder',
  parentId,
  name: '客户反馈',
  createdAt: '2026-10-08T08:00:00Z',
  updatedAt: '2026-10-09T09:30:00+08:00',
  archivedAt: null,
};

test('parsesResourceTimestampsAndNullableArchiveState', () => {
  expect(resourceViewSchema.parse(resource)).toEqual(resource);
  expect(
    resourceViewSchema.parse({ ...resource, archivedAt: '2026-10-09T10:00:00Z' }).archivedAt,
  ).toBe('2026-10-09T10:00:00Z');
  expect(
    resourceCapabilitiesSchema.parse({
      canCreateProject: true,
      canCreateChildren: true,
      canEdit: true,
      canSubmitApproval: false,
      canPublishDirectly: false,
      canApprovePublish: false,
      canArchive: true,
      canRestore: false,
    }),
  ).toMatchObject({ canArchive: true, canRestore: false });
});

test.each([
  ['missing updatedAt', { archivedAt: null }],
  ['invalid updatedAt', { updatedAt: 'yesterday' }],
  ['missing archivedAt', { updatedAt: '2026-10-09T09:30:00Z' }],
  ['invalid archivedAt', { archivedAt: 'not-a-timestamp' }],
])('rejectsMalformedServerPayloadsWith%s', async (_label, replacement) => {
  const malformed = { ...resource, ...replacement } as Record<string, unknown>;
  if (_label.startsWith('missing ')) delete malformed[_label.slice('missing '.length)];
  const api = createApiClient({
    fetchImpl: vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({ items: [malformed], nextCursor: null }),
    ),
  });

  await expect(listResources(api)).rejects.toMatchObject({ kind: 'unexpected' });
});

test('encodesEveryResourceFilterAndPreservesTheOpaqueCursor', async () => {
  const opaqueCursor = 'r2:+/=?&';
  const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
    jsonResponse({ items: [resource], nextCursor: opaqueCursor }),
  );
  const api = createApiClient({ fetchImpl });

  await expect(
    listResources(api, {
      parentId,
      query: '客户 feedback',
      kind: 'survey',
      archived: 'archived',
      sort: 'name_asc',
      cursor: opaqueCursor,
    }),
  ).resolves.toMatchObject({ nextCursor: opaqueCursor });

  expect(fetchImpl).toHaveBeenCalledWith(
    `/v1/resources?parentId=${parentId}&query=%E5%AE%A2%E6%88%B7+feedback&kind=survey&archived=archived&sort=name_asc&cursor=r2%3A%2B%2F%3D%3F%26`,
    expect.any(Object),
  );
});

test('isolatesResourceQueryKeysByTenantParentAndEveryFilter', () => {
  const base: ResourceFilters = {
    query: '客户',
    kind: 'folder',
    archived: 'active',
    sort: 'updated_desc',
  };
  const keys = [
    resourceQueryKey('tenant-a', parentId, base),
    resourceQueryKey('tenant-b', parentId, base),
    resourceQueryKey('tenant-a', resourceId, base),
    resourceQueryKey('tenant-a', parentId, { ...base, query: '员工' }),
    resourceQueryKey('tenant-a', parentId, { ...base, kind: 'survey' }),
    resourceQueryKey('tenant-a', parentId, { ...base, archived: 'archived' }),
    resourceQueryKey('tenant-a', parentId, { ...base, sort: 'name_asc' }),
  ];

  expect(new Set(keys.map((key) => JSON.stringify(key)))).toHaveLength(keys.length);
});

test('sendsTypedRenameMoveArchiveAndRestoreRequests', async () => {
  const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async () => jsonResponse(resource));
  const api = createApiClient({ fetchImpl });

  await renameResource(api, 'folder/id', '新名称');
  await moveResource(api, 'folder/id', parentId);
  await archiveResource(api, 'folder/id');
  await restoreResource(api, 'folder/id');

  expect(fetchImpl.mock.calls.map(([path, init]) => [path, init?.method, init?.body])).toEqual([
    ['/v1/resources/folder%2Fid', 'PATCH', JSON.stringify({ name: '新名称' })],
    ['/v1/resources/folder%2Fid/move', 'POST', JSON.stringify({ parentId })],
    ['/v1/resources/folder%2Fid/archive', 'POST', undefined],
    ['/v1/resources/folder%2Fid/restore', 'POST', undefined],
  ]);
});
