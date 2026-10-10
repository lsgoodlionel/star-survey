import { act, screen, waitFor, within } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, test, vi } from 'vitest';
import { ApiError } from '../../shared/api/errors';
import type { DashboardView } from '../../shared/api/dashboard';
import type { ApiClient, ApiRequest } from '../../shared/api/http';
import { renderWithQuery } from '../../test/render';
import { DashboardPage } from './DashboardPage';

const surveyId = '10000000-0000-4000-8000-000000000001';

const authState = vi.hoisted(() => ({
  actorId: 'actor-a',
  api: null as ApiClient | null,
  tenantId: 'tenant-a',
}));

vi.mock('../auth/AuthProvider', () => ({
  AuthProvider: ({ children }: { children: ReactNode }) => children,
  useAuth: () => ({
    api: authState.api,
    logout: vi.fn(),
    session: {
      token: 'dashboard-token',
      expiresAt: Date.now() + 60_000,
      me: {
        tenantId: authState.tenantId,
        actorId: authState.actorId,
        roles: ['tenant_owner'],
      },
    },
  }),
}));

const emptyDashboard: DashboardView = {
  generatedAt: '2026-10-10T00:00:00Z',
  visibleSections: ['approval', 'publish', 'preview', 'export', 'survey'],
  summary: {
    pendingApprovals: 0,
    publishExceptions: 0,
    activePreviews: 0,
    activeExports: 0,
  },
  tasks: [],
  surveys: [],
  recentWork: [],
};

function apiFrom(handler: (request: ApiRequest<unknown>) => Promise<unknown> | unknown): ApiClient {
  return {
    request: (request) => Promise.resolve(handler(request as ApiRequest<unknown>)) as never,
  };
}

function renderDashboard(api: ApiClient) {
  authState.api = api;
  return renderWithQuery(
    <MemoryRouter>
      <DashboardPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  authState.actorId = 'actor-a';
  authState.api = null;
  authState.tenantId = 'tenant-a';
});

describe('DashboardPage', () => {
  test('shows a stable loading skeleton and an accessible icon-only refresh action', () => {
    renderDashboard(apiFrom(() => new Promise(() => undefined)));

    expect(screen.getByRole('status', { name: '正在加载工作台' })).toBeInTheDocument();
    const refresh = screen.getByRole('button', { name: '刷新工作台' });
    expect(refresh).toBeDisabled();
    expect(refresh).toHaveTextContent('');
  });

  test('renders all-empty success without inventing an identity', async () => {
    authState.tenantId = 'tenant-from-session';
    renderDashboard(apiFrom(() => emptyDashboard));

    expect(await screen.findByText('当前没有需要处理的事项。')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '工作台' })).toBeInTheDocument();
    expect(screen.getByText('当前租户：tenant-from-session')).toBeInTheDocument();
    expect(screen.queryByText('测试管理员')).not.toBeInTheDocument();
    expect(screen.queryByText('actor-a')).not.toBeInTheDocument();
    expect(screen.getByText('当前没有可查看的问卷。')).toBeInTheDocument();
    expect(screen.getByText('还没有可恢复的最近工作。')).toBeInTheDocument();
    expect(screen.getAllByText('0')).toHaveLength(4);
  });

  test('hides unauthorized summaries, tasks and survey section', async () => {
    const hiddenData: DashboardView = {
      ...emptyDashboard,
      visibleSections: ['publish'],
      summary: { ...emptyDashboard.summary, pendingApprovals: 9, publishExceptions: 2 },
      tasks: [
        task('approval', 'pending_approval', 'pending', `/surveys/${surveyId}/publish`),
        task('publish', 'publish_exception', 'failed', `/surveys/${surveyId}/publish`),
      ],
      surveys: [survey('published', ['edit'])],
    };
    renderDashboard(apiFrom(() => hiddenData));

    expect(await screen.findByText('发布异常', { selector: 'dt' })).toBeInTheDocument();
    expect(screen.queryByText('待我审批')).not.toBeInTheDocument();
    expect(screen.queryByText('问卷运行情况')).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: '查看发布' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '查看审批' })).not.toBeInTheDocument();
  });

  test('hides summary and task regions when no business task section is visible', async () => {
    renderDashboard(apiFrom(() => ({
      ...emptyDashboard,
      visibleSections: ['survey'],
      summary: { ...emptyDashboard.summary, pendingApprovals: 8 },
      tasks: [task('approval', 'pending_approval', 'pending', `/surveys/${surveyId}/publish`)],
    })));

    expect(await screen.findByRole('heading', { name: '问卷运行情况' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '业务摘要' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '需要处理' })).not.toBeInTheDocument();
    expect(screen.queryByText('8')).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '查看审批' })).not.toBeInTheDocument();
  });

  test('isolates cached dashboard data by tenant and stable actor identity', async () => {
    const api = apiFrom(() => ({
      ...emptyDashboard,
      summary: {
        ...emptyDashboard.summary,
        publishExceptions: authState.actorId === 'actor-a' ? 3 : 6,
      },
    }));
    const view = renderDashboard(api);

    expect(await screen.findByText('3')).toBeInTheDocument();
    expect(view.queryClient.getQueryData(['dashboard', 'tenant-a', 'actor-a'])).toBeDefined();

    authState.actorId = 'actor-b';
    view.rerender(
      <QueryClientProvider client={view.queryClient}>
        <MemoryRouter>
          <DashboardPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText('6')).toBeInTheDocument();
    expect(view.queryClient.getQueryData(['dashboard', 'tenant-a', 'actor-a'])).toBeDefined();
    expect(view.queryClient.getQueryData(['dashboard', 'tenant-a', 'actor-b'])).toBeDefined();
  });

  test('uses every server task route and real survey action route with Chinese labels', async () => {
    const routeData: DashboardView = {
      ...emptyDashboard,
      summary: {
        pendingApprovals: 1,
        publishExceptions: 1,
        activePreviews: 1,
        activeExports: 1,
      },
      tasks: [
        task('approval', 'pending_approval', 'pending', `/surveys/${surveyId}/publish`),
        task('publish', 'publish_exception', 'failed', `/surveys/${surveyId}/publish`),
        task('preview', 'preview_exception', 'close_failed', `/surveys/${surveyId}/preview`),
        task('export', 'export_exception', 'expired', `/surveys/${surveyId}/responses`),
        task('draft', 'draft_pending_publish', 'draft', `/surveys/${surveyId}/publish`),
      ],
      surveys: [
        survey('draft', ['edit', 'preview', 'publish', 'responses']),
        survey('pending_approval'),
        survey('approved'),
        survey('publishing'),
        survey('published'),
        survey('failed'),
        survey('needs_reconciliation'),
      ],
      recentWork: [{
        surveyId,
        surveyName: '客户满意度',
        page: 'version',
        targetPath: `/surveys/${surveyId}/versions/3`,
        visitedAt: '2026-10-10T00:00:00Z',
      }],
    };
    renderDashboard(apiFrom(() => routeData));

    const taskList = await screen.findByRole('table', { name: '需要处理' });
    const expectedTaskRoutes = [
      ['查看审批', `/surveys/${surveyId}/publish`],
      ['查看发布', `/surveys/${surveyId}/publish`],
      ['查看预览', `/surveys/${surveyId}/preview`],
      ['查看导出', `/surveys/${surveyId}/responses`],
      ['前往发布', `/surveys/${surveyId}/publish`],
    ];
    for (const [name, href] of expectedTaskRoutes) {
      expect(within(taskList).getByRole('link', { name })).toHaveAttribute('href', href);
    }

    const surveyTable = screen.getByRole('table', { name: '问卷运行情况' });
    for (const [name, href] of [
      ['编辑', `/surveys/${surveyId}/edit`],
      ['真实预览', `/surveys/${surveyId}/preview`],
      ['发布与版本', `/surveys/${surveyId}/publish`],
      ['答卷与导出', `/surveys/${surveyId}/responses`],
    ]) {
      expect(within(surveyTable).getByRole('link', { name })).toHaveAttribute('href', href);
    }
    expect(screen.getByRole('link', { name: '继续查看版本' })).toHaveAttribute(
      'href',
      `/surveys/${surveyId}/versions/3`,
    );
    for (const label of ['待审批', '审批通过', '发布中', '已发布', '发布失败', '待核对']) {
      expect(within(surveyTable).getByText(label)).toBeInTheDocument();
    }
    expect(screen.queryByText('pending')).not.toBeInTheDocument();
    expect(screen.queryByText('close_failed')).not.toBeInTheDocument();
    expect(screen.queryByText('needs_reconciliation')).not.toBeInTheDocument();
  });

  test('renders a forbidden state without leaking dashboard data', async () => {
    renderDashboard(apiFrom(() => Promise.reject(new ApiError('forbidden', '无权执行当前操作'))));

    expect(await screen.findByRole('alert')).toHaveTextContent('你没有访问工作台的权限。');
    expect(screen.queryByRole('button', { name: '重新加载工作台' })).not.toBeInTheDocument();
    expect(screen.queryByText('待我审批')).not.toBeInTheDocument();
  });

  test('renders an initial unavailable state with a retry action and no zero summaries', async () => {
    renderDashboard(apiFrom(() => Promise.reject(new ApiError('unavailable', '服务暂时不可用'))));

    expect(await screen.findByRole('alert')).toHaveTextContent('工作台数据暂时不可用，请稍后重试。');
    expect(screen.getByRole('button', { name: '重新加载工作台' })).toBeInTheDocument();
    expect(screen.queryByText('待我审批')).not.toBeInTheDocument();
  });

  test('derives stale state from any refetch failure and clears it after a successful refetch', async () => {
    let requests = 0;
    const view = renderDashboard(apiFrom(() => {
      requests += 1;
      if (requests === 2) return Promise.reject(new ApiError('unavailable', '服务暂时不可用'));
      return {
        ...emptyDashboard,
        summary: { ...emptyDashboard.summary, publishExceptions: requests === 1 ? 7 : 9 },
      };
    }));

    expect(await screen.findByText('7')).toBeInTheDocument();
    await act(() => view.queryClient.refetchQueries({
      queryKey: ['dashboard', 'tenant-a', 'actor-a'],
      exact: true,
    }));

    expect(await screen.findByRole('status', { name: '工作台数据状态' })).toHaveTextContent(
      '刷新失败，当前显示上次加载的数据，数据可能已过期。',
    );
    expect(screen.getByText('7')).toBeInTheDocument();

    await act(() => view.queryClient.refetchQueries({
      queryKey: ['dashboard', 'tenant-a', 'actor-a'],
      exact: true,
    }));

    expect(await screen.findByText('9')).toBeInTheDocument();
    await waitFor(() => expect(
      screen.queryByRole('status', { name: '工作台数据状态' }),
    ).not.toBeInTheDocument());
  });
});

function task(
  key: string,
  kind: DashboardView['tasks'][number]['kind'],
  status: string,
  targetPath: string,
): DashboardView['tasks'][number] {
  return {
    taskKey: key,
    kind,
    surveyId,
    surveyName: `问卷-${key}`,
    status,
    updatedAt: '2026-10-10T00:00:00Z',
    targetPath,
  };
}

function survey(
  publishState: DashboardView['surveys'][number]['publishState'],
  actions: DashboardView['surveys'][number]['actions'] = [],
): DashboardView['surveys'][number] {
  return {
    surveyId,
    name: `客户满意度-${publishState}`,
    draftVersion: 4,
    publishedVersion: publishState === 'draft' ? null : 3,
    publishState,
    completedResponses: publishState === 'approved' ? null : 12,
    updatedAt: '2026-10-10T00:00:00Z',
    actions,
  };
}
