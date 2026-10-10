import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import { StrictMode, useSyncExternalStore, type ReactNode } from 'react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { beforeEach, expect, test, vi } from 'vitest';
import type { ApiClient, ApiRequest } from '../shared/api/http';
import { VersionDetailPage } from '../features/publish/VersionDetailPage';
import { PublishPage } from '../features/publish/PublishPage';
import { PreviewPage } from '../features/preview/PreviewPage';
import { ResponsesPage } from '../features/responses/ResponsesPage';
import type { PreviewClient } from '../shared/api/previews';
import type { ExportClient } from '../shared/api/exports';
import { createAppRoutes } from './router';
import { SurveyShell, useSurveyPageReady } from './SurveyShell';

const surveyId = '30000000-0000-4000-8000-000000000001';
const projectId = '10000000-0000-4000-8000-000000000001';
const folderId = '20000000-0000-4000-8000-000000000001';
const questionId = '40000000-0000-4000-8000-000000000001';

let failSurveyLoad = false;
let failRecentWork = false;
let failVersionLoad = false;
let recentWorkRequest: ((apiRequest: ApiRequest<unknown>) => Promise<unknown>) | null = null;
const authState = {
  actorId: 'author-7',
  listeners: new Set<() => void>(),
  subscribe(listener: () => void) {
    authState.listeners.add(listener);
    return () => authState.listeners.delete(listener);
  },
  setActor(actorId: string) {
    authState.actorId = actorId;
    authState.listeners.forEach((listener) => listener());
  },
};
const request = vi.fn((apiRequest: ApiRequest<unknown>) =>
  apiRequest.path === '/v1/dashboard/recent-work' && recentWorkRequest
    ? recentWorkRequest(apiRequest) as never
    : Promise.resolve(handleRequest(apiRequest)) as never);
const api: ApiClient = { request };

function handleRequest(request: ApiRequest<unknown>) {
  if (request.path === '/v1/dashboard/recent-work') {
    if (failRecentWork) throw new Error('recent work unavailable');
    return undefined;
  }
  if (request.path === `/v1/surveys/${surveyId}/versions/3`) {
    if (failVersionLoad) throw new Error('version unavailable');
    return {
      surveyId,
      version: 3,
      requestId: '50000000-0000-4000-8000-000000000001',
      draftVersion: 7,
      engineInstanceId: 'survey-shell-engine',
      engineSid: 876543,
      compilerVersion: '2.1.0',
      fingerprintVersion: '1',
      fingerprint: 'sha256:survey-shell-version',
      language: 'zh-Hans',
      enginePublishedAt: '2026-10-09T08:30:00Z',
      publishedBy: 'publisher-1',
      publishedAt: '2026-10-09T08:30:02Z',
      fields: [{
        questionUuid: questionId,
        code: 'Q1',
        type: 'L',
        fieldname: '876543X1X1Q1',
        aid: '',
        scale: 0,
      }],
      definition: { definitionVersion: 2, title: '客户反馈问卷' },
      live: true,
      supersededAt: null,
      engineClosedAt: null,
    };
  }
  if (request.path === `/v1/surveys/${surveyId}`) {
    if (failSurveyLoad) throw new Error('survey unavailable');
    return {
      id: surveyId,
      title: `客户反馈问卷-${authState.actorId}`,
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
  useAuth: () => {
    const actorId = useSyncExternalStore(authState.subscribe, () => authState.actorId);
    return {
      api,
      logout: vi.fn(),
      session: {
        token: `survey-shell-token-${actorId}`,
        expiresAt: Date.now() + 60_000,
        me: { tenantId: 'tenant-a', actorId, roles: ['tenant_owner'] },
      },
    };
  },
}));

beforeEach(() => {
  vi.clearAllMocks();
  failSurveyLoad = false;
  failRecentWork = false;
  failVersionLoad = false;
  recentWorkRequest = null;
  authState.actorId = 'author-7';
});

function ReadyPage({ page }: { page: 'edit' | 'import' | 'preview' | 'publish' | 'responses' }) {
  useSurveyPageReady(page, true);
  return <p>{page}内容</p>;
}

function renderSurveyShell(
  initialEntry: string,
  options: {
    queryClient?: QueryClient;
    routeElements?: Partial<Record<'preview' | 'publish' | 'responses', ReactNode>>;
    strict?: boolean;
  } = {},
) {
  const queryClient = options.queryClient ?? new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const router = createMemoryRouter(
    [{
      path: '/surveys/:surveyId',
      element: <SurveyShell />,
      children: [
        { path: 'edit', element: <ReadyPage page="edit" /> },
        { path: 'import', element: <ReadyPage page="import" /> },
        { path: 'preview', element: options.routeElements?.preview ?? <ReadyPage page="preview" /> },
        { path: 'publish', element: options.routeElements?.publish ?? <ReadyPage page="publish" /> },
        { path: 'responses', element: options.routeElements?.responses ?? <ReadyPage page="responses" /> },
        { path: '*', element: <p>未知页面</p> },
        {
          path: 'versions/:version',
          element: (
            <VersionDetailPage
              api={api}
              surveyId={surveyId}
              tenantId="tenant-a"
              version={3}
            />
          ),
        },
      ],
    }],
    { initialEntries: [initialEntry] },
  );
  const tree = (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
  const result = render(options.strict ? <StrictMode>{tree}</StrictMode> : tree);
  return { ...result, queryClient, router };
}

test('loadsSurveyContextAndPreservesTheSelectedQuestionAcrossWorkflowLinks', async () => {
  renderSurveyShell(`/surveys/${surveyId}/edit?question=${questionId}`);

  expect(screen.getByText('正在加载问卷上下文')).toBeInTheDocument();
  expect(await screen.findByRole('heading', { name: '客户反馈问卷-author-7' })).toBeInTheDocument();
  const breadcrumb = screen.getByRole('navigation', { name: '面包屑' });
  expect(within(breadcrumb).getByRole('link', { name: '项目与问卷' })).toHaveAttribute(
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
  expect(within(breadcrumb).getByText('客户反馈问卷-author-7')).toBeInTheDocument();
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
    ['快速预览', 'preview'],
    ['发布与版本', 'publish'],
    ['答卷与导出', 'responses'],
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

test('rendersVersionDetailsAsShellContentWithCollapsedTechnicalInformation', async () => {
  renderSurveyShell(`/surveys/${surveyId}/versions/3?question=${questionId}`);

  await screen.findByRole('heading', { name: '客户反馈问卷-author-7' });
  const tabs = screen.getByRole('navigation', { name: '问卷工作流' });
  expect(within(tabs).getByRole('link', { name: '发布与版本' })).toHaveAttribute(
    'aria-current',
    'page',
  );
  await screen.findByRole('heading', { name: '已发布版本 3' });
  expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
  expect(screen.getByRole('heading', { level: 2, name: '已发布版本 3' })).toBeInTheDocument();

  const technicalDetails = screen.getByText('技术信息').closest('details');
  expect(technicalDetails).not.toBeNull();
  expect(technicalDetails).not.toHaveAttribute('open');
  for (const value of [
    'survey-shell-engine',
    '876543',
    '2.1.0',
    'sha256:survey-shell-version',
    'publisher-1',
  ]) {
    expect(within(technicalDetails!).getByText(value)).toBeInTheDocument();
  }
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
    'responses',
    'versions/:version',
  ]);
  expect(appShell?.children?.some((route) => route.path === 'surveys/:surveyId/edit')).toBe(false);
});

test.each([
  ['edit', null],
  ['import', null],
  ['preview', null],
  ['publish', null],
  ['responses', null],
  ['versions/3', 3],
] as const)('registers valid %s recent work once after the survey context loads', async (path, version) => {
  renderSurveyShell(`/surveys/${surveyId}/${path}?question=${questionId}&next=https://invalid.example`);

  await screen.findByRole('heading', { name: '客户反馈问卷-author-7' });
  const page = path.startsWith('versions/') ? 'version' : path;
  await waitFor(() => expect(request).toHaveBeenCalledWith(expect.objectContaining({
    path: '/v1/dashboard/recent-work',
    method: 'POST',
    body: { surveyId, page, version },
  })));
  expect(request.mock.calls.filter(([call]) => call.path === '/v1/dashboard/recent-work')).toHaveLength(1);
});

test('does not register recent work when the survey context fails to load', async () => {
  failSurveyLoad = true;
  renderSurveyShell(`/surveys/${surveyId}/edit`);

  expect(await screen.findByRole('alert')).toHaveTextContent('问卷上下文暂时不可用');
  expect(request.mock.calls.some(([call]) => call.path === '/v1/dashboard/recent-work')).toBe(false);
});

test.each(['versions/0', 'versions/01', 'versions/1e2', 'versions/+1', 'versions/not-a-version', 'unknown']) (
  'does not register arbitrary survey route %s',
  async (path) => {
    renderSurveyShell(`/surveys/${surveyId}/${path}?page=publish&version=7`);

    await screen.findByRole('heading', { name: '客户反馈问卷-author-7' });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(request.mock.calls.some(([call]) => call.path === '/v1/dashboard/recent-work')).toBe(false);
  },
);

test('keeps the business page available when recent-work registration fails', async () => {
  failRecentWork = true;
  renderSurveyShell(`/surveys/${surveyId}/edit`);

  expect(await screen.findByText('edit内容')).toBeInTheDocument();
  await waitFor(() => expect(request.mock.calls.some(
    ([call]) => call.path === '/v1/dashboard/recent-work',
  )).toBe(true));
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

test('invalidates only the current tenant dashboard key after successful registration', async () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const invalidateQueries = vi.spyOn(queryClient, 'invalidateQueries');
  renderSurveyShell(`/surveys/${surveyId}/edit`, { queryClient });

  await waitFor(() => expect(invalidateQueries).toHaveBeenCalledWith({
    queryKey: ['dashboard', 'tenant-a'],
  }));
  expect(invalidateQueries).toHaveBeenCalledTimes(1);
});

test('waits for the concrete outlet to report successful readiness', async () => {
  const { router } = renderSurveyShell(`/surveys/${surveyId}/unknown`);
  await screen.findByRole('heading', { name: '客户反馈问卷-author-7' });
  expect(request.mock.calls.some(([call]) => call.path === '/v1/dashboard/recent-work')).toBe(false);

  await act(() => router.navigate(`/surveys/${surveyId}/edit`));
  await waitFor(() => expect(request.mock.calls.filter(
    ([call]) => call.path === '/v1/dashboard/recent-work',
  )).toHaveLength(1));
});

test('does not register an immutable version whose detail request fails', async () => {
  failVersionLoad = true;
  renderSurveyShell(`/surveys/${surveyId}/versions/3`);

  expect(await screen.findByRole('alert')).toHaveTextContent('该版本不存在或不可访问');
  expect(request.mock.calls.some(([call]) => call.path === '/v1/dashboard/recent-work')).toBe(false);
});

test.each([
  ['preview', '快速预览暂时不可用'],
  ['publish', '发布信息暂时不可用'],
  ['responses', '答卷数据加载失败'],
] as const)('does not register when the real %s page fails to load', async (page, errorText) => {
  const previewClient: PreviewClient = {
    create: vi.fn(),
    get: vi.fn(),
    close: vi.fn(),
  };
  const exportClient: ExportClient = {
    create: vi.fn(),
    get: vi.fn(),
    cancel: vi.fn(),
    download: vi.fn(),
  };
  const routeElements = {
    preview: (
      <PreviewPage
        api={api}
        previewClient={previewClient}
        surveyId={surveyId}
        tenantId="tenant-a"
      />
    ),
    publish: (
      <PublishPage
        actorId="author-7"
        accessToken="test-token"
        api={api}
        onUnauthorized={vi.fn()}
        surveyId={surveyId}
        tenantId="tenant-a"
      />
    ),
    responses: (
      <ResponsesPage
        api={api}
        exportClient={exportClient}
        surveyId={surveyId}
        tenantId="tenant-a"
      />
    ),
  };
  renderSurveyShell(`/surveys/${surveyId}/${page}`, { routeElements });

  expect(await screen.findByRole('alert')).toHaveTextContent(errorText);
  expect(request.mock.calls.some(([call]) => call.path === '/v1/dashboard/recent-work')).toBe(false);
});

test('isolates shell queries and registration when the actor changes inside one tenant', async () => {
  renderSurveyShell(`/surveys/${surveyId}/edit`);
  expect(await screen.findByRole('heading', { name: '客户反馈问卷-author-7' })).toBeInTheDocument();
  await waitFor(() => expect(request.mock.calls.filter(
    ([call]) => call.path === '/v1/dashboard/recent-work',
  )).toHaveLength(1));

  act(() => authState.setActor('author-8'));

  expect(await screen.findByRole('heading', { name: '客户反馈问卷-author-8' })).toBeInTheDocument();
  await waitFor(() => expect(request.mock.calls.filter(
    ([call]) => call.path === '/v1/dashboard/recent-work',
  )).toHaveLength(2));
  expect(request.mock.calls.filter(
    ([call]) => call.path === `/v1/surveys/${surveyId}`,
  )).toHaveLength(2);
});

test('serializes registrations across fast navigation and keeps the newest target last', async () => {
  let releaseEdit!: () => void;
  const editPending = new Promise<void>((resolve) => { releaseEdit = resolve; });
  const processed: string[] = [];
  recentWorkRequest = async (apiRequest) => {
    const page = (apiRequest.body as { page: string }).page;
    if (page === 'edit') await editPending;
    processed.push(page);
    return undefined;
  };
  const { router } = renderSurveyShell(`/surveys/${surveyId}/edit`);
  await waitFor(() => expect(request.mock.calls.filter(
    ([call]) => call.path === '/v1/dashboard/recent-work',
  )).toHaveLength(1));

  await act(() => router.navigate(`/surveys/${surveyId}/preview`));
  await screen.findByText('preview内容');
  expect(request.mock.calls.filter(
    ([call]) => call.path === '/v1/dashboard/recent-work',
  )).toHaveLength(1);

  releaseEdit();
  await waitFor(() => expect(processed).toEqual(['edit', 'preview']));
  expect(request.mock.calls.filter(
    ([call]) => call.path === '/v1/dashboard/recent-work',
  ).map(([call]) => (call.body as { page: string }).page)).toEqual(['edit', 'preview']);
});

test('deduplicates outlet readiness under StrictMode', async () => {
  renderSurveyShell(`/surveys/${surveyId}/edit`, { strict: true });

  await screen.findByText('edit内容');
  await waitFor(() => expect(request.mock.calls.filter(
    ([call]) => call.path === '/v1/dashboard/recent-work',
  )).toHaveLength(1));
});
