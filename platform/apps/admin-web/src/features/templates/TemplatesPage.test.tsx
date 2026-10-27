import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { expect, test, vi } from 'vitest';
import { createAppRoutes } from '../../app/router';
import { server } from '../../test/server';

vi.mock('../auth/AuthProvider', async () => {
  const { createApiClient } = await import('../../shared/api/http');
  return { AuthProvider: ({ children }: { children: ReactNode }) => children,
    useAuth: () => ({ api: createApiClient(), logout: vi.fn(), session: {
      token: 'test', expiresAt: Date.now() + 60_000,
      me: { tenantId: 'tenant-a', actorId: 'author', roles: ['editor'] },
    } }) };
});

const id = '10000000-0000-4000-8000-000000000001';
const projectId = '20000000-0000-4000-8000-000000000001';
const surveyId = '30000000-0000-4000-8000-000000000001';
const template = { id, scope: 'tenant', name: '客户满意度', description: '标准模板', status: 'published',
  currentVersion: 2, publishedVersion: 2, createdBy: 'author', reviewedBy: 'reviewer',
  reviewedAt: '2026-10-27T08:00:00Z', reviewReason: null, updatedAt: '2026-10-27T09:00:00Z' };
const project = { id: projectId, kind: 'project', parentId: null, name: '客户项目',
  createdAt: '2026-10-27T08:00:00Z', updatedAt: '2026-10-27T09:00:00Z', archivedAt: null };

function renderPage(path: string) {
  const router = createMemoryRouter(createAppRoutes(false), { initialEntries: [path] });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
  return { router, client };
}

test('browses tenant and platform templates with URL-backed scope and status', async () => {
  const scopes: string[] = [];
  server.use(http.get('/v1/survey-templates', ({ request }) => {
    const scope = new URL(request.url).searchParams.get('scope') ?? '';
    scopes.push(scope);
    return HttpResponse.json(scope === 'platform' ? [{ ...template, scope: 'platform', name: '平台模板' }] : [template]);
  }));
  const user = userEvent.setup();
  const { router } = renderPage('/templates');
  expect(await screen.findByRole('link', { name: /客户满意度/ })).toBeInTheDocument();
  expect(screen.getByRole('table', { name: '模板列表' })).toBeInTheDocument();
  await user.selectOptions(screen.getByRole('combobox', { name: '模板范围' }), 'platform');
  expect(await screen.findByRole('link', { name: /平台模板/ })).toBeInTheDocument();
  expect(router.state.location.search).toContain('scope=platform');
  expect(scopes).toEqual(expect.arrayContaining(['all', 'platform']));
});

test('shows detail history and copies a published template into a permitted project once', async () => {
  let copies = 0;
  server.use(
    http.get('/v1/survey-templates/:id', () => HttpResponse.json({ template,
      versions: [{ versionNo: 2, sourceSurveyId: surveyId, sourceDraftVersion: 3,
        createdBy: 'author', createdAt: '2026-10-27T08:00:00Z' }],
      history: [{ event: 'approved', actor: 'reviewer', versionNo: 2,
        reason: null, copiedSurveyId: null, occurredAt: '2026-10-27T08:00:00Z' }] })),
    http.get('/v1/resources', () => HttpResponse.json({ items: [project], nextCursor: null })),
    http.get('/v1/resource-capabilities', () => HttpResponse.json({
      canCreateProject: true, canCreateChildren: true, canEdit: true, canSubmitApproval: false,
      canPublishDirectly: false, canApprovePublish: false, canArchive: false, canRestore: false,
    })),
    http.post('/v1/survey-templates/:id/copies', async ({ request }) => {
      copies++;
      expect(await request.json()).toEqual({ parentId: projectId, title: '我的副本' });
      return HttpResponse.json({ id: surveyId, title: '我的副本', status: 'draft', draftVersion: 1,
        publishedVersion: null, lastPublish: null }, { status: 201 });
    }),
  );
  const user = userEvent.setup();
  const { router } = renderPage(`/templates/${id}`);
  expect(await screen.findByRole('heading', { name: '客户满意度' })).toBeInTheDocument();
  expect(screen.getByText(/版本 2 · author/)).toBeInTheDocument();
  expect(screen.getByText(/审核通过 · reviewer/)).toBeInTheDocument();
  await user.selectOptions(await screen.findByRole('combobox', { name: '目标项目或文件夹' }), projectId);
  await user.type(screen.getByRole('textbox', { name: '新问卷标题' }), '我的副本');
  const button = screen.getByRole('button', { name: '从模板创建问卷' });
  await user.click(button);
  expect(router.state.location.pathname).toBe(`/surveys/${surveyId}/edit`);
  expect(router.state.location.search).toContain('from=');
  expect(copies).toBe(1);
});

test('shows empty and forbidden states without exposing copy', async () => {
  server.use(http.get('/v1/survey-templates', () => HttpResponse.json([])));
  renderPage('/templates?scope=tenant');
  expect(await screen.findByText('暂无模板')).toBeInTheDocument();
  expect(within(screen.getByRole('main')).queryByRole('button', { name: '从模板创建问卷' })).not.toBeInTheDocument();
});

test('shows a permission error and offers retry', async () => {
  server.use(http.get('/v1/survey-templates', () => HttpResponse.json({ code: 'FORBIDDEN' }, { status: 403 })));
  renderPage('/templates');
  expect(await screen.findByRole('alert')).toHaveTextContent('无权查看模板');
  expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();
});

test('does not offer copy for an unpublished template', async () => {
  server.use(http.get('/v1/survey-templates/:id', () => HttpResponse.json({
    template: { ...template, status: 'unpublished', publishedVersion: null },
    versions: [], history: [],
  })));
  renderPage(`/templates/${id}`);
  expect(await screen.findByText('当前状态不可复制；请使用已发布的模板。')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '从模板创建问卷' })).not.toBeInTheDocument();
});
