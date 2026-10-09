import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { expect, test, vi } from 'vitest';
import { createAppRoutes } from '../../app/router';
import type { ResourceCapabilities, ResourceView } from '../../shared/api/resources';
import { server } from '../../test/server';

const authState = vi.hoisted(() => {
  const listeners = new Set<() => void>();
  return {
    tenantId: 'tenant-a',
    roles: ['tenant_owner'] as string[],
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    setTenantId(tenantId: string) {
      authState.tenantId = tenantId;
      listeners.forEach((listener) => listener());
    },
  };
});

vi.mock('../auth/AuthProvider', async () => {
  const { useSyncExternalStore } = await import('react');
  const { createApiClient } = await import('../../shared/api/http');
  let api: ReturnType<typeof createApiClient> | undefined;
  return {
    AuthProvider: ({ children }: { children: ReactNode }) => children,
    useAuth: () => {
      const tenantId = useSyncExternalStore(authState.subscribe, () => authState.tenantId);
      return {
        api: (api ??= createApiClient()),
        logout: vi.fn(),
        session: {
          token: 'workspace-token',
          expiresAt: Date.now() + 60_000,
          me: { tenantId, actorId: 'author-7', roles: authState.roles },
        },
      };
    },
  };
});

const projectA = resource('10000000-0000-4000-8000-000000000001', 'project', null, '项目甲');
const projectB = resource('10000000-0000-4000-8000-000000000002', 'project', null, '项目乙');
const folderA = resource('20000000-0000-4000-8000-000000000001', 'folder', projectA.id, '调研资料');
const surveyA = resource('30000000-0000-4000-8000-000000000001', 'survey', projectA.id, '客户反馈');
const surveyB = resource('30000000-0000-4000-8000-000000000002', 'survey', projectB.id, '员工体验');

function resource(
  id: string,
  kind: ResourceView['kind'],
  parentId: string | null,
  name: string,
  archivedAt: string | null = null,
): ResourceView {
  return {
    id,
    kind,
    parentId,
    name,
    createdAt: '2026-10-08T08:00:00Z',
    updatedAt: '2026-10-09T09:30:00+08:00',
    archivedAt,
  };
}

const allCapabilities: ResourceCapabilities = {
  canCreateProject: true,
  canCreateChildren: true,
  canEdit: true,
  canSubmitApproval: true,
  canPublishDirectly: true,
  canApprovePublish: true,
  canArchive: true,
  canRestore: true,
};

function renderWorkspace(
  initialEntry = '/workspace',
  options: {
    roles?: string[];
    capabilities?: ResourceCapabilities | ((resourceId: string | null) => ResourceCapabilities);
  } = {},
) {
  authState.tenantId = 'tenant-a';
  authState.roles = options.roles ?? ['tenant_owner'];
  server.use(
    http.get('/v1/resource-capabilities', ({ request }) => {
      const resourceId = new URL(request.url).searchParams.get('resourceId');
      return HttpResponse.json(
        typeof options.capabilities === 'function'
          ? options.capabilities(resourceId)
          : (options.capabilities ?? allCapabilities),
      );
    }),
  );
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const router = createMemoryRouter(createAppRoutes(false), { initialEntries: [initialEntry] });
  const result = render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return {
    ...result,
    queryClient,
    router,
    switchTenant: (tenantId: string) => act(() => authState.setTenantId(tenantId)),
  };
}

function installListHandler(children: Record<string, ResourceView[]>) {
  server.use(
    http.get('/v1/resources/:id', ({ params }) => {
      const found = [projectA, folderA, surveyA].find((item) => item.id === params.id);
      return found ? HttpResponse.json(found) : new HttpResponse(null, { status: 404 });
    }),
    http.get('/v1/resources', ({ request }) => {
      const url = new URL(request.url);
      const parentId = url.searchParams.get('parentId') ?? 'root';
      const kind = url.searchParams.get('kind');
      const archived = url.searchParams.get('archived') ?? 'active';
      const items = (children[parentId] ?? []).filter(
        (item) =>
          (!kind || item.kind === kind) &&
          (archived === 'archived' ? item.archivedAt !== null : item.archivedAt === null),
      );
      return HttpResponse.json({ items, nextCursor: null });
    }),
  );
}

test('keepsOnlyProjectsAndFoldersInNavigationAndListsCurrentContainerRows', async () => {
  installListHandler({ root: [projectA, projectB], [projectA.id]: [folderA, surveyA] });
  const user = userEvent.setup();
  renderWorkspace(`/workspace?project=${projectA.id}&parent=${projectA.id}`);

  const navigation = await screen.findByRole('complementary', { name: '项目与文件夹' });
  expect(await within(navigation).findByRole('button', { name: projectA.name })).toBeInTheDocument();
  expect(await within(navigation).findByRole('button', { name: folderA.name })).toBeInTheDocument();
  expect(within(navigation).queryByText(surveyA.name)).not.toBeInTheDocument();
  const list = screen.getByRole('list', { name: '当前位置资源' });
  expect(within(list).getByRole('button', { name: folderA.name })).toBeInTheDocument();
  expect(within(list).getByRole('button', { name: surveyA.name })).toBeInTheDocument();
  expect(within(list).getByText('文件夹')).toBeInTheDocument();
  expect(within(list).getByText('问卷')).toBeInTheDocument();
  expect(within(list).getAllByText(/2026/).length).toBeGreaterThan(0);

  await user.click(within(list).getByRole('button', { name: folderA.name }));
  expect(screen.getByRole('navigation', { name: '当前位置' })).toHaveTextContent('项目甲/调研资料');
});

test('restoresEveryUrlFilterAndPassesOpaquePaginationWithoutClientSorting', async () => {
  const requests: string[] = [];
  const opaqueCursor = 'page:+/=?&';
  server.use(
    http.get('/v1/resources/:id', ({ params }) =>
      params.id === surveyA.id
        ? HttpResponse.json(surveyA)
        : new HttpResponse(null, { status: 404 }),
    ),
    http.get('/v1/resources', ({ request }) => {
      const url = new URL(request.url);
      requests.push(url.search);
      if (url.searchParams.get('kind') === 'project') {
        return HttpResponse.json({ items: [projectA], nextCursor: null });
      }
      if (url.searchParams.get('cursor') === opaqueCursor) {
        return HttpResponse.json({ items: [folderA], nextCursor: null });
      }
      return HttpResponse.json({ items: [surveyA], nextCursor: opaqueCursor });
    }),
  );
  const user = userEvent.setup();
  renderWorkspace(
    `/workspace?project=${projectA.id}&parent=${projectA.id}&resource=${surveyA.id}&query=%E5%AE%A2%E6%88%B7&kind=survey&archived=active&sort=name_asc`,
  );

  expect(await screen.findByRole('button', { name: surveyA.name })).toBeInTheDocument();
  expect(screen.getByRole('searchbox', { name: '搜索资源' })).toHaveValue('客户');
  expect(screen.getByRole('combobox', { name: '资源类型' })).toHaveValue('survey');
  expect(screen.getByRole('combobox', { name: '资源状态' })).toHaveValue('active');
  expect(screen.getByRole('combobox', { name: '排序方式' })).toHaveValue('name_asc');
  await user.click(screen.getByRole('button', { name: '加载更多' }));
  expect(await screen.findByRole('button', { name: folderA.name })).toBeInTheDocument();
  expect(requests.filter((query) =>
    query.includes(`parentId=${projectA.id}`) && query.includes('kind=survey'),
  )).toEqual([
    `?parentId=${projectA.id}&query=%E5%AE%A2%E6%88%B7&kind=survey&archived=active&sort=name_asc`,
    `?parentId=${projectA.id}&query=%E5%AE%A2%E6%88%B7&kind=survey&archived=active&sort=name_asc&cursor=page%3A%2B%2F%3D%3F%26`,
  ]);
  const names = within(screen.getByRole('list', { name: '当前位置资源' }))
    .getAllByRole('button')
    .map((button) => button.textContent);
  expect(names).toEqual([surveyA.name, folderA.name]);
});

test('restoresAResourceOnlyDeepLinkIntoProjectParentAndSelectionUrlState', async () => {
  const nestedSurvey = { ...surveyA, parentId: folderA.id };
  installListHandler({ root: [projectA], [projectA.id]: [folderA], [folderA.id]: [nestedSurvey] });
  server.use(
    http.get('/v1/resources/:id', ({ params }) => {
      const found = [projectA, folderA, nestedSurvey].find((item) => item.id === params.id);
      return found ? HttpResponse.json(found) : new HttpResponse(null, { status: 404 });
    }),
  );
  const { router } = renderWorkspace(`/workspace?resource=${nestedSurvey.id}`);

  expect(await screen.findByRole('button', { name: nestedSurvey.name })).toHaveAttribute(
    'aria-current',
    'true',
  );
  await waitFor(() => {
    expect(router.state.location.search).toContain(`project=${projectA.id}`);
    expect(router.state.location.search).toContain(`parent=${folderA.id}`);
  });
  expect(screen.getByRole('navigation', { name: '当前位置' })).toHaveTextContent('项目甲/调研资料');
});

test('tracksRecentlyOpenedResourcesOnlyForTheCurrentTenantSession', async () => {
  let tenant = 'tenant-a';
  server.use(
    http.get('/v1/resources', ({ request }) => {
      const kind = new URL(request.url).searchParams.get('kind');
      const project = tenant === 'tenant-a' ? projectA : projectB;
      const survey = tenant === 'tenant-a' ? surveyA : surveyB;
      return HttpResponse.json({ items: kind === 'project' ? [project] : [survey], nextCursor: null });
    }),
  );
  const user = userEvent.setup();
  const rendered = renderWorkspace(`/workspace?project=${projectA.id}&parent=${projectA.id}`);
  await user.click(await screen.findByRole('button', { name: surveyA.name }));
  expect(screen.getByRole('region', { name: '最近打开' })).toHaveTextContent(surveyA.name);

  tenant = 'tenant-b';
  rendered.switchTenant('tenant-b');
  expect(await screen.findByRole('button', { name: projectB.name })).toBeInTheDocument();
  expect(screen.queryByRole('region', { name: '最近打开' })).not.toBeInTheDocument();
  expect(screen.queryByText(surveyA.name)).not.toBeInTheDocument();
});

test('keepsFiltersAcrossErrorAndRetriesTheCurrentContainer', async () => {
  let attempts = 0;
  server.use(
    http.get('/v1/resources', ({ request }) => {
      const url = new URL(request.url);
      if (url.searchParams.get('kind') === 'project') {
        return HttpResponse.json({ items: [projectA], nextCursor: null });
      }
      attempts += 1;
      if (attempts === 1) return HttpResponse.json({ traceId: 'list-failed' }, { status: 503 });
      return HttpResponse.json({ items: [], nextCursor: null });
    }),
  );
  const user = userEvent.setup();
  const { router } = renderWorkspace(
    `/workspace?project=${projectA.id}&parent=${projectA.id}&query=%E5%AE%A2%E6%88%B7&kind=survey&archived=active&sort=updated_desc`,
  );

  expect(await screen.findByRole('alert')).toHaveTextContent('服务暂时不可用，请稍后重试');
  expect(screen.getByRole('searchbox', { name: '搜索资源' })).toHaveValue('客户');
  await user.click(screen.getByRole('button', { name: '重试' }));
  expect(await screen.findByText('当前位置暂无资源')).toBeInTheDocument();
  expect(router.state.location.search).toContain('query=%E5%AE%A2%E6%88%B7');
});

test('isolatesLateTenantAndProjectResponsesFromTheCurrentView', async () => {
  let releaseTenantA!: () => void;
  const tenantAGate = new Promise<void>((resolve) => {
    releaseTenantA = resolve;
  });
  let tenant = 'tenant-a';
  server.use(
    http.get('/v1/resources', async ({ request }) => {
      const requestTenant = tenant;
      const url = new URL(request.url);
      if (requestTenant === 'tenant-a') await tenantAGate;
      const project = requestTenant === 'tenant-a' ? projectA : projectB;
      const survey = requestTenant === 'tenant-a' ? surveyA : surveyB;
      return HttpResponse.json({
        items: url.searchParams.get('kind') === 'project' ? [project] : [survey],
        nextCursor: null,
      });
    }),
  );
  const rendered = renderWorkspace(`/workspace?project=${projectA.id}&parent=${projectA.id}`);
  tenant = 'tenant-b';
  rendered.switchTenant('tenant-b');
  releaseTenantA();

  expect(await screen.findByRole('button', { name: projectB.name })).toBeInTheDocument();
  await waitFor(() => expect(screen.queryByText(surveyA.name)).not.toBeInTheDocument());
  expect(screen.queryByText(projectA.name)).not.toBeInTheDocument();
});

test('doesNotLetALateProjectResponseReplaceTheSelectedProjectList', async () => {
  let releaseProjectA!: () => void;
  const projectAGate = new Promise<void>((resolve) => {
    releaseProjectA = resolve;
  });
  server.use(
    http.get('/v1/resources', async ({ request }) => {
      const url = new URL(request.url);
      const parentId = url.searchParams.get('parentId');
      if (url.searchParams.get('kind') === 'project') {
        return HttpResponse.json({ items: [projectA, projectB], nextCursor: null });
      }
      if (parentId === projectA.id) {
        await projectAGate;
        return HttpResponse.json({ items: [surveyA], nextCursor: null });
      }
      return HttpResponse.json({ items: [surveyB], nextCursor: null });
    }),
  );
  const user = userEvent.setup();
  renderWorkspace(`/workspace?project=${projectA.id}&parent=${projectA.id}`);
  await user.click(await screen.findByRole('button', { name: projectB.name }));
  expect(await screen.findByRole('button', { name: surveyB.name })).toBeInTheDocument();
  releaseProjectA();
  await waitFor(() => expect(screen.queryByText(surveyA.name)).not.toBeInTheDocument());
});

test('usesCapabilitiesToHideCreateAndResourceActionsRegardlessOfTokenRoles', async () => {
  installListHandler({ root: [projectA], [projectA.id]: [folderA] });
  const user = userEvent.setup();
  renderWorkspace(`/workspace?project=${projectA.id}&parent=${projectA.id}`, {
    roles: ['tenant_owner'],
    capabilities: {
      ...allCapabilities,
      canCreateProject: false,
      canCreateChildren: false,
      canEdit: false,
      canArchive: false,
      canRestore: false,
    },
  });
  await user.click(await screen.findByRole('button', { name: folderA.name }));
  expect(screen.queryByRole('button', { name: '新建' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '重命名' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '移动' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '归档' })).not.toBeInTheDocument();
});

test('renamesAndMovesAResourceThenRefreshesAffectedTenantBranches', async () => {
  const requestedParents: Array<string | null> = [];
  const children: Record<string, ResourceView[]> = {
    root: [projectA, projectB],
    [projectA.id]: [folderA],
    [projectB.id]: [],
  };
  server.use(
    http.get('/v1/resources/:id', ({ params }) => {
      const found = [projectA, projectB, folderA].find((item) => item.id === params.id);
      return found ? HttpResponse.json(found) : new HttpResponse(null, { status: 404 });
    }),
    http.get('/v1/resources', ({ request }) => {
      const url = new URL(request.url);
      const parentId = url.searchParams.get('parentId');
      requestedParents.push(parentId);
      const items = (children[parentId ?? 'root'] ?? []).filter((item) =>
        url.searchParams.get('kind') ? item.kind === url.searchParams.get('kind') : true,
      );
      return HttpResponse.json({ items, nextCursor: null });
    }),
    http.patch('/v1/resources/:id', async ({ request }) => {
      const { name } = (await request.json()) as { name: string };
      children[projectA.id] = [{ ...folderA, name }];
      return HttpResponse.json(children[projectA.id][0]);
    }),
    http.post('/v1/resources/:id/move', async ({ request }) => {
      const { parentId } = (await request.json()) as { parentId: string };
      const moved = { ...children[projectA.id][0], parentId };
      children[projectA.id] = [];
      children[parentId] = [moved];
      return HttpResponse.json(moved);
    }),
  );
  const user = userEvent.setup();
  const { queryClient } = renderWorkspace(
    `/workspace?project=${projectA.id}&parent=${projectA.id}`,
  );
  const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
  await user.click(await screen.findByRole('button', { name: folderA.name }));
  await user.click(await screen.findByRole('button', { name: '重命名' }));
  let dialog = screen.getByRole('dialog', { name: '重命名资源' });
  const nameInput = within(dialog).getByLabelText('名称');
  await user.clear(nameInput);
  await user.type(nameInput, '新的资料夹');
  await user.click(within(dialog).getByRole('button', { name: '保存名称' }));
  expect(await screen.findByRole('button', { name: '新的资料夹' })).toBeInTheDocument();

  await user.click(screen.getByRole('button', { name: '移动' }));
  dialog = screen.getByRole('dialog', { name: '移动资源' });
  await user.selectOptions(within(dialog).getByLabelText('目标位置'), projectB.id);
  await user.click(within(dialog).getByRole('button', { name: '确认移动' }));
  await waitFor(() => expect(screen.queryByText('新的资料夹')).not.toBeInTheDocument());
  expect(requestedParents).toContain(projectA.id);
  expect(invalidate).toHaveBeenCalledWith({
    queryKey: ['resources', 'tenant-a', projectA.id],
  });
  expect(invalidate).toHaveBeenCalledWith({
    queryKey: ['resources', 'tenant-a', projectB.id],
  });
});

test('archivesASurveyWithPublicLinkWarningAndRemovesItFromActiveView', async () => {
  let archived = false;
  server.use(
    http.get('/v1/resources', ({ request }) => {
      const url = new URL(request.url);
      if (url.searchParams.get('kind') === 'project') {
        return HttpResponse.json({ items: [projectA], nextCursor: null });
      }
      return HttpResponse.json({ items: archived ? [] : [surveyA], nextCursor: null });
    }),
    http.post('/v1/resources/:id/archive', () => {
      archived = true;
      return HttpResponse.json({ ...surveyA, archivedAt: '2026-10-09T10:00:00Z' });
    }),
  );
  const user = userEvent.setup();
  renderWorkspace(`/workspace?project=${projectA.id}&parent=${projectA.id}`);
  await user.click(await screen.findByRole('button', { name: surveyA.name }));
  await user.click(await screen.findByRole('button', { name: '归档' }));
  const dialog = screen.getByRole('dialog', { name: '归档资源' });
  expect(dialog).toHaveTextContent('归档不会关闭已发布的公开答卷链接');
  await user.click(within(dialog).getByRole('button', { name: '确认归档' }));
  expect(await screen.findByText('当前位置暂无资源')).toBeInTheDocument();
  expect(screen.queryByRole('region', { name: '最近打开' })).not.toBeInTheDocument();
});

test('restoresAResourceAndRemovesItFromArchivedView', async () => {
  const archivedSurvey = resource(
    surveyA.id,
    'survey',
    projectA.id,
    surveyA.name,
    '2026-10-09T10:00:00Z',
  );
  let restored = false;
  server.use(
    http.get('/v1/resources', ({ request }) => {
      const url = new URL(request.url);
      if (url.searchParams.get('kind') === 'project') {
        return HttpResponse.json({ items: [projectA], nextCursor: null });
      }
      return HttpResponse.json({ items: restored ? [] : [archivedSurvey], nextCursor: null });
    }),
    http.post('/v1/resources/:id/restore', () => {
      restored = true;
      return HttpResponse.json({ ...archivedSurvey, archivedAt: null });
    }),
  );
  const user = userEvent.setup();
  renderWorkspace(
    `/workspace?project=${projectA.id}&parent=${projectA.id}&archived=archived`,
  );
  await user.click(await screen.findByRole('button', { name: archivedSurvey.name }));
  await user.click(await screen.findByRole('button', { name: '恢复' }));
  const dialog = screen.getByRole('dialog', { name: '恢复资源' });
  await user.click(within(dialog).getByRole('button', { name: '确认恢复' }));
  await waitFor(() => expect(screen.queryByText(archivedSurvey.name)).not.toBeInTheDocument());
});

test('retainsMutationFormAndFiltersAfterFailureAndRestoresFocusOnClose', async () => {
  installListHandler({ root: [projectA], [projectA.id]: [folderA] });
  server.use(
    http.patch('/v1/resources/:id', () =>
      HttpResponse.json({ traceId: 'rename-failed' }, { status: 503 }),
    ),
  );
  const user = userEvent.setup();
  const { router } = renderWorkspace(
    `/workspace?project=${projectA.id}&parent=${projectA.id}&query=%E8%B5%84%E6%96%99&sort=name_asc`,
  );
  await user.click(await screen.findByRole('button', { name: folderA.name }));
  const trigger = await screen.findByRole('button', { name: '重命名' });
  await user.click(trigger);
  const dialog = screen.getByRole('dialog', { name: '重命名资源' });
  const input = within(dialog).getByLabelText('名称');
  await user.clear(input);
  await user.type(input, '保留的名称');
  await user.click(within(dialog).getByRole('button', { name: '保存名称' }));

  expect(await within(dialog).findByRole('alert')).toHaveTextContent('服务暂时不可用，请重试');
  expect(input).toHaveValue('保留的名称');
  expect(router.state.location.search).toContain('query=%E8%B5%84%E6%96%99');
  expect(router.state.location.search).toContain('sort=name_asc');
  await user.click(within(dialog).getByRole('button', { name: '取消' }));
  expect(trigger).toHaveFocus();
});

test('providesChineseCreateMenuBreadcrumbAndProjectDrawerControls', async () => {
  installListHandler({ root: [projectA], [projectA.id]: [] });
  const user = userEvent.setup();
  renderWorkspace(`/workspace?project=${projectA.id}&parent=${projectA.id}`);

  expect(await screen.findByRole('heading', { name: '资源工作台' })).toBeInTheDocument();
  const projectNav = screen.getByRole('complementary', { name: '项目与文件夹' });
  expect(
    await within(projectNav).findByRole('button', { name: projectA.name }),
  ).toBeInTheDocument();
  expect(screen.getByRole('navigation', { name: '当前位置' })).toHaveTextContent('项目甲');
  expect(screen.getByRole('button', { name: '打开项目导航' })).toHaveAttribute(
    'aria-controls',
    'workspace-project-nav',
  );
  const createTrigger = screen.getByRole('button', { name: '新建' });
  await user.click(createTrigger);
  expect(screen.getByRole('menuitem', { name: '新建项目' })).toBeInTheDocument();
  expect(screen.getByRole('menuitem', { name: '新建文件夹' })).toBeInTheDocument();
  expect(screen.getByRole('menuitem', { name: '新建问卷' })).toBeInTheDocument();
  await user.click(screen.getByRole('menuitem', { name: '新建项目' }));
  const dialog = screen.getByRole('dialog', { name: '新建项目' });
  expect(within(dialog).getByLabelText('名称')).toHaveFocus();
  await user.click(within(dialog).getByRole('button', { name: '取消' }));
  expect(createTrigger).toHaveFocus();
});
