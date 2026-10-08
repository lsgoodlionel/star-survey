import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { expect, test, vi } from 'vitest';
import { createAppRoutes } from '../../app/router';
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
const surveyA = resource('30000000-0000-4000-8000-000000000001', 'survey', folderA.id, '客户反馈');

function resource(id: string, kind: 'project' | 'folder' | 'survey', parentId: string | null, name: string) {
  return { id, kind, parentId, name, createdAt: '2026-10-08T08:00:00Z' };
}

interface CapabilityFixture {
  canCreateProject: boolean;
  canCreateChildren: boolean;
  canEdit: boolean;
  canSubmitApproval: boolean;
  canPublishDirectly: boolean;
  canApprovePublish: boolean;
}

const allCapabilities: CapabilityFixture = {
  canCreateProject: true,
  canCreateChildren: true,
  canEdit: true,
  canSubmitApproval: true,
  canPublishDirectly: true,
  canApprovePublish: true,
};

function renderWorkspace(
  initialEntry = '/workspace',
  options: { roles?: string[]; capabilities?: CapabilityFixture; queryClient?: QueryClient } = {},
) {
  const { roles = ['tenant_owner'], capabilities = allCapabilities } = options;
  authState.tenantId = 'tenant-a';
  authState.roles = roles;
  server.use(http.get('/v1/resource-capabilities', () => HttpResponse.json(capabilities)));
  const queryClient =
    options.queryClient ??
    new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
  const router = createMemoryRouter(createAppRoutes(false), { initialEntries: [initialEntry] });
  const view = () => (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
  const result = render(view());
  return {
    ...result,
    queryClient,
    router,
    switchTenant: (tenantId: string) => act(() => authState.setTenantId(tenantId)),
  };
}

test('loadsOnlyTheSelectedBranchAndFollowsOpaqueCursors', async () => {
  const requests: Array<{ parentId: string | null; cursor: string | null }> = [];
  const opaqueCursor = 'branch+cursor/with=padding';
  server.use(
    http.get('/v1/resources', ({ request }) => {
      const url = new URL(request.url);
      const parentId = url.searchParams.get('parentId');
      const cursor = url.searchParams.get('cursor');
      requests.push({ parentId, cursor });
      if (!parentId) return HttpResponse.json({ items: [projectA, projectB], nextCursor: null });
      if (parentId === projectA.id && !cursor) {
        return HttpResponse.json({ items: [folderA], nextCursor: opaqueCursor });
      }
      if (parentId === projectA.id && cursor === opaqueCursor) {
        return HttpResponse.json({ items: [surveyA], nextCursor: null });
      }
      return HttpResponse.json({ items: [], nextCursor: null });
    }),
  );
  const user = userEvent.setup();
  renderWorkspace();

  expect(await screen.findByRole('button', { name: '项目甲' })).toBeInTheDocument();
  expect(requests).toEqual([{ parentId: null, cursor: null }]);

  await user.click(screen.getByRole('button', { name: '展开 项目甲' }));
  expect(await screen.findByRole('button', { name: '调研资料' })).toBeInTheDocument();
  expect(requests).toEqual([
    { parentId: null, cursor: null },
    { parentId: projectA.id, cursor: null },
  ]);

  await user.click(screen.getByRole('button', { name: '加载更多 项目甲' }));
  expect(await screen.findByRole('button', { name: '客户反馈' })).toBeInTheDocument();
  expect(requests.at(-1)).toEqual({ parentId: projectA.id, cursor: opaqueCursor });
  expect(requests.some((request) => request.parentId === projectB.id)).toBe(false);
});

test('resolvesADeepUrlTargetAndExpandsEveryAncestor', async () => {
  const gets: string[] = [];
  server.use(
    http.get('/v1/resources/:id', ({ params }) => {
      gets.push(String(params.id));
      const found = [projectA, folderA, surveyA].find((item) => item.id === params.id);
      return found ? HttpResponse.json(found) : HttpResponse.json({}, { status: 404 });
    }),
    http.get('/v1/resources', ({ request }) => {
      const parentId = new URL(request.url).searchParams.get('parentId');
      if (!parentId) return HttpResponse.json({ items: [projectA, projectB], nextCursor: null });
      if (parentId === projectA.id) return HttpResponse.json({ items: [folderA], nextCursor: null });
      if (parentId === folderA.id) return HttpResponse.json({ items: [surveyA], nextCursor: null });
      return HttpResponse.json({ items: [], nextCursor: null });
    }),
  );
  const user = userEvent.setup();
  const { router } = renderWorkspace(`/workspace?resource=${surveyA.id}`);

  const selected = await screen.findByRole('button', { name: '客户反馈' });
  expect(selected).toHaveAttribute('aria-current', 'true');
  expect(screen.getByRole('heading', { name: '客户反馈' })).toBeInTheDocument();
  expect(screen.getByRole('link', { name: '编辑问卷' })).toHaveAttribute(
    'href',
    `/surveys/${surveyA.id}/edit`,
  );
  expect(gets).toEqual([surveyA.id, folderA.id, projectA.id]);
  expect(screen.getByRole('button', { name: '收起 项目甲' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '收起 调研资料' })).toBeInTheDocument();

  await user.click(screen.getByRole('button', { name: '项目乙' }));
  await waitFor(() => expect(router.state.location.search).toBe(`?resource=${projectB.id}`));
});

test('isolatesResourceQueriesWhenTheSessionTenantChanges', async () => {
  const requests: string[] = [];
  let activeTenant = 'tenant-a';
  server.use(
    http.get('/v1/resources', () => {
      requests.push(activeTenant);
      return HttpResponse.json({
        items: [activeTenant === 'tenant-a' ? projectA : projectB],
        nextCursor: null,
      });
    }),
  );
  authState.tenantId = 'tenant-a';
  const rendered = renderWorkspace();
  expect(await screen.findByRole('button', { name: '项目甲' })).toBeInTheDocument();

  activeTenant = 'tenant-b';
  rendered.switchTenant('tenant-b');

  expect(await screen.findByRole('button', { name: '项目乙' })).toBeInTheDocument();
  expect(requests).toEqual(['tenant-a', 'tenant-b']);
  expect(rendered.queryClient.getQueryData(['resources', 'tenant-a', null])).toBeDefined();
  expect(rendered.queryClient.getQueryData(['resources', 'tenant-b', null])).toBeDefined();
});

test('doesNotMergeTenantALocalCreationsIntoTenantBAfterSessionSwitch', async () => {
  const createdInA = resource('10000000-0000-4000-8000-000000000099', 'project', null, '租户甲本地项目');
  let activeTenant = 'tenant-a';
  server.use(
    http.get('/v1/resources', () =>
      HttpResponse.json({ items: activeTenant === 'tenant-b' ? [projectB] : [], nextCursor: null }),
    ),
    http.post('/v1/projects', () => HttpResponse.json(createdInA, { status: 201 })),
    http.get('/v1/resources/:id', () => HttpResponse.json({}, { status: 404 })),
  );
  const user = userEvent.setup();
  const rendered = renderWorkspace();
  await user.click(await screen.findByRole('button', { name: '新建项目' }));
  const dialog = screen.getByRole('dialog', { name: '新建项目' });
  await user.type(within(dialog).getByLabelText('名称'), createdInA.name);
  await user.click(within(dialog).getByRole('button', { name: '创建项目' }));
  expect(await screen.findByRole('button', { name: createdInA.name })).toBeInTheDocument();

  activeTenant = 'tenant-b';
  rendered.switchTenant('tenant-b');

  expect(await screen.findByRole('button', { name: '项目乙' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: createdInA.name })).not.toBeInTheDocument();
});

test('ignoresAnInflightTenantACreateAfterTenantBOpensItsOwnDialog', async () => {
  const createdInA = resource('10000000-0000-4000-8000-000000000098', 'project', null, '租户甲迟到项目');
  let activeTenant = 'tenant-a';
  let releaseCreate!: () => void;
  const createGate = new Promise<void>((resolve) => {
    releaseCreate = resolve;
  });
  server.use(
    http.get('/v1/resources', () =>
      HttpResponse.json({ items: activeTenant === 'tenant-b' ? [projectB] : [], nextCursor: null }),
    ),
    http.post('/v1/projects', async () => {
      await createGate;
      return HttpResponse.json(createdInA, { status: 201 });
    }),
  );
  const user = userEvent.setup();
  const rendered = renderWorkspace();

  await user.click(await screen.findByRole('button', { name: '新建项目' }));
  let dialog = screen.getByRole('dialog', { name: '新建项目' });
  await user.type(within(dialog).getByLabelText('名称'), createdInA.name);
  await user.click(within(dialog).getByRole('button', { name: '创建项目' }));

  activeTenant = 'tenant-b';
  rendered.switchTenant('tenant-b');
  expect(await screen.findByRole('button', { name: projectB.name })).toBeInTheDocument();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

  await user.click(screen.getByRole('button', { name: '新建项目' }));
  dialog = screen.getByRole('dialog', { name: '新建项目' });
  const tenantBInput = within(dialog).getByLabelText('名称');
  await user.type(tenantBInput, '租户乙草稿');

  releaseCreate();
  await waitFor(() =>
    expect(rendered.queryClient.getMutationCache().getAll().at(-1)?.state.status).toBe('success'),
  );
  expect(tenantBInput).toHaveValue('租户乙草稿');
  expect(screen.getByRole('dialog', { name: '新建项目' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: createdInA.name })).not.toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(rendered.router.state.location.pathname).toBe('/workspace');
  expect(rendered.router.state.location.search).toBe('');
});

test('ignoresAnInflightTenantACreateFailureAfterTenantBOpensItsOwnDialog', async () => {
  const attemptedInA = resource(
    '10000000-0000-4000-8000-000000000097',
    'project',
    null,
    '租户甲失败项目',
  );
  let activeTenant = 'tenant-a';
  let signalRequestStarted!: () => void;
  let releaseFailure!: () => void;
  const requestStarted = new Promise<void>((resolve) => {
    signalRequestStarted = resolve;
  });
  const createGate = new Promise<void>((resolve) => {
    releaseFailure = resolve;
  });
  server.use(
    http.get('/v1/resources', () =>
      HttpResponse.json({ items: activeTenant === 'tenant-b' ? [projectB] : [], nextCursor: null }),
    ),
    http.post('/v1/projects', async () => {
      signalRequestStarted();
      await createGate;
      return HttpResponse.json({ traceId: 'tenant-a-create-failure' }, { status: 503 });
    }),
  );
  const user = userEvent.setup();
  const rendered = renderWorkspace();

  await user.click(await screen.findByRole('button', { name: '新建项目' }));
  let dialog = screen.getByRole('dialog', { name: '新建项目' });
  await user.type(within(dialog).getByLabelText('名称'), attemptedInA.name);
  await user.click(within(dialog).getByRole('button', { name: '创建项目' }));
  await requestStarted;

  activeTenant = 'tenant-b';
  rendered.switchTenant('tenant-b');
  expect(await screen.findByRole('button', { name: projectB.name })).toBeInTheDocument();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

  await user.click(screen.getByRole('button', { name: '新建项目' }));
  dialog = screen.getByRole('dialog', { name: '新建项目' });
  const tenantBInput = within(dialog).getByLabelText('名称');
  await user.type(tenantBInput, '租户乙草稿');

  releaseFailure();
  await waitFor(() =>
    expect(rendered.queryClient.getMutationCache().getAll().at(-1)?.state.status).toBe('error'),
  );
  expect(tenantBInput).toBeEnabled();
  expect(tenantBInput).toHaveValue('租户乙草稿');
  expect(within(dialog).getByRole('button', { name: '创建项目' })).toBeEnabled();
  expect(screen.getByRole('dialog', { name: '新建项目' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: projectB.name })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: attemptedInA.name })).not.toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(rendered.router.state.location.pathname).toBe('/workspace');
  expect(rendered.router.state.location.search).toBe('');
});

test('pinsResolvedDeepPathNodesMissingFromFirstPagesWithoutExhaustingOpaqueCursors', async () => {
  const otherFolder = resource('20000000-0000-4000-8000-000000000002', 'folder', projectA.id, '其他文件夹');
  const otherSurvey = resource('30000000-0000-4000-8000-000000000002', 'survey', folderA.id, '其他问卷');
  const requests: Array<{ parentId: string | null; cursor: string | null }> = [];
  server.use(
    http.get('/v1/resources/:id', ({ params }) => {
      const found = [projectA, folderA, surveyA].find((item) => item.id === params.id);
      return found ? HttpResponse.json(found) : HttpResponse.json({}, { status: 404 });
    }),
    http.get('/v1/resources', ({ request }) => {
      const url = new URL(request.url);
      const parentId = url.searchParams.get('parentId');
      const cursor = url.searchParams.get('cursor');
      requests.push({ parentId, cursor });
      if (!parentId && !cursor) {
        return HttpResponse.json({ items: [projectB], nextCursor: 'opaque-root-next' });
      }
      if (!parentId && cursor === 'opaque-root-next') {
        return HttpResponse.json({ items: [], nextCursor: null });
      }
      if (parentId === projectA.id) {
        return HttpResponse.json({ items: [otherFolder], nextCursor: null });
      }
      if (parentId === folderA.id) {
        return HttpResponse.json({ items: [otherSurvey], nextCursor: null });
      }
      return HttpResponse.json({ items: [], nextCursor: null });
    }),
  );
  const user = userEvent.setup();
  renderWorkspace(`/workspace?resource=${surveyA.id}`);

  expect(await screen.findByRole('button', { name: surveyA.name })).toHaveAttribute('aria-current', 'true');
  expect(screen.getByRole('button', { name: projectA.name })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: folderA.name })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: projectB.name })).toBeInTheDocument();

  await user.click(screen.getByRole('button', { name: '加载更多' }));
  await waitFor(() =>
    expect(requests).toContainEqual({ parentId: null, cursor: 'opaque-root-next' }),
  );
  expect(requests.filter((request) => request.cursor !== null)).toHaveLength(1);
});

test('createsAProjectFolderAndBlankSurveyWithChineseDefaults', async () => {
  const createdProject = resource('10000000-0000-4000-8000-000000000009', 'project', null, '年度调研');
  const createdFolder = resource(
    '20000000-0000-4000-8000-000000000009',
    'folder',
    createdProject.id,
    '客户组',
  );
  const createdSurveyId = '30000000-0000-4000-8000-000000000009';
  const bodies: Record<string, unknown>[] = [];
  const children = new Map<string, ReturnType<typeof resource>[]>([['root', []]]);
  const resourceGets: Array<string | null> = [];
  server.use(
    http.get('/v1/resources', ({ request }) => {
      const parentId = new URL(request.url).searchParams.get('parentId');
      resourceGets.push(parentId);
      return HttpResponse.json({ items: children.get(parentId ?? 'root') ?? [], nextCursor: null });
    }),
    http.post('/v1/projects', async ({ request }) => {
      bodies.push((await request.json()) as Record<string, unknown>);
      children.set('root', [createdProject]);
      return HttpResponse.json(createdProject, { status: 201 });
    }),
    http.post('/v1/folders', async ({ request }) => {
      bodies.push((await request.json()) as Record<string, unknown>);
      children.set(createdProject.id, [createdFolder]);
      return HttpResponse.json(createdFolder, { status: 201 });
    }),
    http.post('/v1/surveys', async ({ request }) => {
      bodies.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json(
        {
          id: createdSurveyId,
          title: '满意度调查',
          status: 'draft',
          draftVersion: 1,
          publishedVersion: null,
          lastPublish: null,
        },
        { status: 201 },
      );
    }),
  );
  const user = userEvent.setup();
  const { router } = renderWorkspace();
  await user.click(await screen.findByRole('button', { name: '新建项目' }));
  let dialog = screen.getByRole('dialog', { name: '新建项目' });
  await user.type(within(dialog).getByLabelText('名称'), '年度调研');
  await user.click(within(dialog).getByRole('button', { name: '创建项目' }));
  expect(await screen.findByRole('button', { name: '年度调研' })).toHaveAttribute('aria-current', 'true');
  expect(resourceGets.filter((parentId) => parentId === null)).toHaveLength(2);

  await user.click(await screen.findByRole('button', { name: '新建文件夹' }));
  dialog = screen.getByRole('dialog', { name: '新建文件夹' });
  await user.type(within(dialog).getByLabelText('名称'), '客户组');
  await user.click(within(dialog).getByRole('button', { name: '创建文件夹' }));
  expect(await screen.findByRole('button', { name: '客户组' })).toHaveAttribute('aria-current', 'true');
  expect(resourceGets.filter((parentId) => parentId === createdProject.id)).toHaveLength(1);

  await user.click(await screen.findByRole('button', { name: '新建问卷' }));
  dialog = screen.getByRole('dialog', { name: '新建问卷' });
  await user.type(within(dialog).getByLabelText('标题'), '满意度调查');
  await user.click(within(dialog).getByRole('button', { name: '创建问卷' }));

  await waitFor(() => expect(router.state.location.pathname).toBe(`/surveys/${createdSurveyId}/edit`));
  expect(bodies[0]).toEqual({ name: '年度调研' });
  expect(bodies[1]).toEqual({ parentId: createdProject.id, name: '客户组' });
  expect(bodies[2]).toMatchObject({
    parentId: createdFolder.id,
    definition: {
      definitionVersion: 2,
      title: '满意度调查',
      language: 'zh-Hans',
      groups: [
        {
          title: '第一题组',
          questions: [{ code: 'QNOTE', type: 'X', text: '请在这里添加问卷说明' }],
        },
      ],
    },
  });
});

test('usesServerCapabilitiesInsteadOfTokenRolesForAvailableActions', async () => {
  server.use(
    http.get('/v1/resources', () => HttpResponse.json({ items: [projectA], nextCursor: null })),
  );
  renderWorkspace('/workspace', {
    roles: ['tenant_owner'],
    capabilities: {
      canCreateProject: false,
      canCreateChildren: false,
      canEdit: false,
      canSubmitApproval: false,
      canPublishDirectly: false,
      canApprovePublish: false,
    },
  });

  expect(await screen.findByRole('button', { name: '项目甲' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '新建项目' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '新建文件夹' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '新建问卷' })).not.toBeInTheDocument();
});

test('shows404AsUnavailableWithoutRevealingAnotherTenant', async () => {
  server.use(
    http.get('/v1/resources', () => HttpResponse.json({ items: [], nextCursor: null })),
    http.get('/v1/resources/:id', () =>
      HttpResponse.json(
        { error: 'resource_belongs_to_another_tenant', ownerTenant: 'tenant-secret' },
        { status: 404 },
      ),
    ),
  );
  renderWorkspace(`/workspace?resource=${projectA.id}`);

  expect(await screen.findByRole('alert')).toHaveTextContent('资源不存在或不可访问');
  expect(screen.queryByText(/tenant-secret|另一个租户|resource_belongs/)).not.toBeInTheDocument();
});

test('containsDialogFocusClosesOnEscapeAndRestoresTheTrigger', async () => {
  server.use(http.get('/v1/resources', () => HttpResponse.json({ items: [], nextCursor: null })));
  const user = userEvent.setup();
  renderWorkspace();
  const trigger = await screen.findByRole('button', { name: '新建项目' });

  await user.click(trigger);
  const dialog = screen.getByRole('dialog', { name: '新建项目' });
  expect(dialog.tagName).toBe('DIALOG');
  expect(screen.getByRole('complementary', { name: '工作区资源' }).closest('[inert]')).not.toBeNull();
  const input = within(dialog).getByLabelText('名称');
  const cancel = within(dialog).getByRole('button', { name: '取消' });
  const submit = within(dialog).getByRole('button', { name: '创建项目' });
  expect(input).toHaveFocus();

  submit.focus();
  await user.tab();
  expect(input).toHaveFocus();
  input.focus();
  await user.tab({ shift: true });
  expect(submit).toHaveFocus();
  expect(cancel).toBeInTheDocument();

  await user.keyboard('{Escape}');
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});

test('preventsDuplicateNextPageRequestsFromSynchronousActivation', async () => {
  let nextPageRequests = 0;
  let releaseNextPage!: () => void;
  const nextPageGate = new Promise<void>((resolve) => {
    releaseNextPage = resolve;
  });
  server.use(
    http.get('/v1/resources', async ({ request }) => {
      const cursor = new URL(request.url).searchParams.get('cursor');
      if (!cursor) return HttpResponse.json({ items: [projectA], nextCursor: 'opaque-next' });
      nextPageRequests += 1;
      await nextPageGate;
      return HttpResponse.json({ items: [projectB], nextCursor: null });
    }),
  );
  renderWorkspace();
  const loadMore = await screen.findByRole('button', { name: '加载更多' });

  fireEvent.click(loadMore);
  fireEvent.click(loadMore);
  await waitFor(() => expect(nextPageRequests).toBe(1));
  releaseNextPage();
  expect(await screen.findByRole('button', { name: '项目乙' })).toBeInTheDocument();
});

test('usesHonestNestedListSemanticsInsteadOfAnIncompleteAriaTree', async () => {
  server.use(
    http.get('/v1/resources', () => HttpResponse.json({ items: [projectA], nextCursor: null })),
  );
  renderWorkspace();

  expect(await screen.findByRole('list', { name: '资源列表' })).toBeInTheDocument();
  expect(screen.queryByRole('tree')).not.toBeInTheDocument();
  expect(screen.queryByRole('treeitem')).not.toBeInTheDocument();
});
