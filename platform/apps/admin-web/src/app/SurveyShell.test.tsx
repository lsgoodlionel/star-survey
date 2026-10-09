import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { beforeEach, expect, test, vi } from 'vitest';
import type { ApiClient, ApiRequest } from '../shared/api/http';
import { createAppRoutes } from './router';
import { SurveyShell } from './SurveyShell';

const surveyId = '30000000-0000-4000-8000-000000000001';
const projectId = '10000000-0000-4000-8000-000000000001';
const folderId = '20000000-0000-4000-8000-000000000001';
const questionId = '40000000-0000-4000-8000-000000000001';

const api: ApiClient = {
  request: (request) => Promise.resolve(handleRequest(request as ApiRequest<unknown>)) as never,
};

function handleRequest(request: ApiRequest<unknown>) {
  if (request.path === `/v1/surveys/${surveyId}`) {
    return {
      id: surveyId,
      title: '客户反馈问卷',
      status: 'draft',
      draftVersion: 7,
      publishedVersion: null,
      lastPublish: null,
    };
  }
  const resources = {
    [surveyId]: {
      id: surveyId,
      kind: 'survey',
      parentId: folderId,
      name: '客户反馈问卷',
      createdAt: '2026-10-09T08:00:00Z',
    },
    [folderId]: {
      id: folderId,
      kind: 'folder',
      parentId: projectId,
      name: '调研资料',
      createdAt: '2026-10-09T08:00:00Z',
    },
    [projectId]: {
      id: projectId,
      kind: 'project',
      parentId: null,
      name: '客户体验项目',
      createdAt: '2026-10-09T08:00:00Z',
    },
  } as const;
  const id = request.path.match(/^\/v1\/resources\/(.+)$/)?.[1];
  if (id && id in resources) return resources[id as keyof typeof resources];
  throw new Error(`Unhandled request: ${request.path}`);
}

vi.mock('../features/auth/AuthProvider', () => ({
  AuthProvider: ({ children }: { children: ReactNode }) => children,
  useAuth: () => ({
    api,
    logout: vi.fn(),
    session: {
      token: 'survey-shell-token',
      expiresAt: Date.now() + 60_000,
      me: { tenantId: 'tenant-a', actorId: 'author-7', roles: ['tenant_owner'] },
    },
  }),
}));

beforeEach(() => vi.clearAllMocks());

function renderSurveyShell(initialEntry: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const router = createMemoryRouter(
    [{
      path: '/surveys/:surveyId',
      element: <SurveyShell />,
      children: [
        { path: 'edit', element: <p>编辑内容</p> },
        { path: 'import', element: <p>导入内容</p> },
        { path: 'preview', element: <p>预览内容</p> },
        { path: 'publish', element: <p>发布内容</p> },
        { path: 'versions/:version', element: <p>版本内容</p> },
      ],
    }],
    { initialEntries: [initialEntry] },
  );
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

test('loadsSurveyContextAndPreservesTheSelectedQuestionAcrossWorkflowLinks', async () => {
  renderSurveyShell(`/surveys/${surveyId}/edit?question=${questionId}`);

  expect(screen.getByText('正在加载问卷上下文')).toBeInTheDocument();
  expect(await screen.findByRole('heading', { name: '客户反馈问卷' })).toBeInTheDocument();
  const breadcrumb = screen.getByRole('navigation', { name: '面包屑' });
  expect(within(breadcrumb).getByRole('link', { name: '工作台' })).toHaveAttribute(
    'href',
    '/workspace',
  );
  expect(within(breadcrumb).getByRole('link', { name: '客户体验项目' })).toHaveAttribute(
    'href',
    `/workspace?resource=${projectId}`,
  );
  expect(within(breadcrumb).getByRole('link', { name: '调研资料' })).toHaveAttribute(
    'href',
    `/workspace?resource=${folderId}`,
  );
  expect(within(breadcrumb).getByText('客户反馈问卷')).toBeInTheDocument();
  expect(screen.getByText('草稿版本 7')).toBeInTheDocument();
  expect(screen.getByText('草稿')).toBeInTheDocument();
  expect(screen.getByRole('link', { name: '返回工作区' })).toHaveAttribute(
    'href',
    `/workspace?resource=${surveyId}`,
  );

  const tabs = screen.getByRole('navigation', { name: '问卷工作流' });
  for (const [name, path] of [
    ['编辑', 'edit'],
    ['批量导入', 'import'],
    ['真实预览', 'preview'],
    ['发布与版本', 'publish'],
  ] as const) {
    expect(within(tabs).getByRole('link', { name })).toHaveAttribute(
      'href',
      `/surveys/${surveyId}/${path}?question=${questionId}`,
    );
  }
  expect(within(tabs).getByRole('link', { name: '编辑' })).toHaveAttribute(
    'aria-current',
    'page',
  );
  expect(within(tabs).queryByRole('link', { name: /admin/i })).not.toBeInTheDocument();
});

test('keepsVersionDetailsInsideThePublishAndVersionsTab', async () => {
  renderSurveyShell(`/surveys/${surveyId}/versions/3?question=${questionId}`);

  await screen.findByRole('heading', { name: '客户反馈问卷' });
  const tabs = screen.getByRole('navigation', { name: '问卷工作流' });
  expect(within(tabs).getByRole('link', { name: '发布与版本' })).toHaveAttribute(
    'aria-current',
    'page',
  );
  expect(screen.getByText('版本内容')).toBeInTheDocument();
});

test('registersSurveyPagesAsChildrenOfTheProtectedSurveyShell', () => {
  const routes = createAppRoutes(false);
  const protectedRoute = routes.find((route) => route.children?.some((child) => child.children));
  const appShell = protectedRoute?.children?.find((route) => route.children);
  const surveyShell = appShell?.children?.find((route) => route.path === 'surveys/:surveyId');

  expect(surveyShell?.children?.map((route) => route.path).filter(Boolean)).toEqual([
    'edit',
    'import',
    'preview',
    'publish',
    'versions/:version',
  ]);
  expect(appShell?.children?.some((route) => route.path === 'surveys/:surveyId/edit')).toBe(false);
});
