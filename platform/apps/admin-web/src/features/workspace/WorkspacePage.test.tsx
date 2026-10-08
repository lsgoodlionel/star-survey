import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { expect, test, vi } from 'vitest';
import { createAppRoutes } from '../../app/router';
import { server } from '../../test/server';

const authState = vi.hoisted(() => ({ roles: ['tenant_owner'] as string[] }));

vi.mock('../auth/AuthProvider', async () => {
  const { createApiClient } = await import('../../shared/api/http');
  let api: ReturnType<typeof createApiClient> | undefined;
  return {
    AuthProvider: ({ children }: { children: ReactNode }) => children,
    useAuth: () => ({
      api: (api ??= createApiClient()),
      logout: vi.fn(),
      session: {
        token: 'workspace-token',
        expiresAt: Date.now() + 60_000,
        me: { tenantId: 'tenant-a', actorId: 'author-7', roles: authState.roles },
      },
    }),
  };
});

const projectA = resource('10000000-0000-4000-8000-000000000001', 'project', null, '项目甲');
const projectB = resource('10000000-0000-4000-8000-000000000002', 'project', null, '项目乙');
const folderA = resource('20000000-0000-4000-8000-000000000001', 'folder', projectA.id, '调研资料');
const surveyA = resource('30000000-0000-4000-8000-000000000001', 'survey', folderA.id, '客户反馈');

function resource(id: string, kind: 'project' | 'folder' | 'survey', parentId: string | null, name: string) {
  return { id, kind, parentId, name, createdAt: '2026-10-08T08:00:00Z' };
}

function renderWorkspace(initialEntry = '/workspace', roles = ['tenant_owner']) {
  authState.roles = roles;
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const router = createMemoryRouter(createAppRoutes(false), { initialEntries: [initialEntry] });
  const result = render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { ...result, queryClient, router };
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

  expect(await screen.findByRole('treeitem', { name: '项目甲' })).toBeInTheDocument();
  expect(requests).toEqual([{ parentId: null, cursor: null }]);

  await user.click(screen.getByRole('button', { name: '展开 项目甲' }));
  expect(await screen.findByRole('treeitem', { name: '调研资料' })).toBeInTheDocument();
  expect(requests).toEqual([
    { parentId: null, cursor: null },
    { parentId: projectA.id, cursor: null },
  ]);

  await user.click(screen.getByRole('button', { name: '加载更多 项目甲' }));
  expect(await screen.findByRole('treeitem', { name: '客户反馈' })).toBeInTheDocument();
  expect(requests.at(-1)).toEqual({ parentId: projectA.id, cursor: opaqueCursor });
  expect(requests.some((request) => request.parentId === projectB.id)).toBe(false);
});

test('restoresTheSelectedResourceFromTheUrl', async () => {
  server.use(
    http.get('/v1/resources', () => HttpResponse.json({ items: [projectA, projectB], nextCursor: null })),
  );
  const user = userEvent.setup();
  const { router } = renderWorkspace(`/workspace?resource=${projectA.id}`);

  const selected = await screen.findByRole('treeitem', { name: '项目甲' });
  expect(selected).toHaveAttribute('aria-current', 'true');
  expect(screen.getByRole('heading', { name: '项目甲' })).toBeInTheDocument();

  await user.click(screen.getByRole('treeitem', { name: '项目乙' }));
  await waitFor(() => expect(router.state.location.search).toBe(`?resource=${projectB.id}`));
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
  server.use(
    http.get('/v1/resources', () => HttpResponse.json({ items: [], nextCursor: null })),
    http.post('/v1/projects', async ({ request }) => {
      bodies.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json(createdProject, { status: 201 });
    }),
    http.post('/v1/folders', async ({ request }) => {
      bodies.push((await request.json()) as Record<string, unknown>);
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
  await screen.findByRole('heading', { name: '问卷工作台' });

  await user.click(screen.getByRole('button', { name: '新建项目' }));
  let dialog = screen.getByRole('dialog', { name: '新建项目' });
  await user.type(within(dialog).getByLabelText('名称'), '年度调研');
  await user.click(within(dialog).getByRole('button', { name: '创建项目' }));
  expect(await screen.findByRole('treeitem', { name: '年度调研' })).toHaveAttribute('aria-current', 'true');

  await user.click(screen.getByRole('button', { name: '新建文件夹' }));
  dialog = screen.getByRole('dialog', { name: '新建文件夹' });
  await user.type(within(dialog).getByLabelText('名称'), '客户组');
  await user.click(within(dialog).getByRole('button', { name: '创建文件夹' }));
  expect(await screen.findByRole('treeitem', { name: '客户组' })).toHaveAttribute('aria-current', 'true');

  await user.click(screen.getByRole('button', { name: '新建问卷' }));
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

test('keepsForbiddenActionsOutOfTheTabOrder', async () => {
  server.use(
    http.get('/v1/resources', () => HttpResponse.json({ items: [projectA], nextCursor: null })),
  );
  renderWorkspace('/workspace', ['statistics_viewer']);

  expect(await screen.findByRole('treeitem', { name: '项目甲' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '新建项目' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '新建文件夹' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '新建问卷' })).not.toBeInTheDocument();
});

test('shows404AsUnavailableWithoutRevealingAnotherTenant', async () => {
  server.use(
    http.get('/v1/resources', () =>
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
