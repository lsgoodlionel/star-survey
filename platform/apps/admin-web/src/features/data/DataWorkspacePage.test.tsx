import { fireEvent, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, test, vi } from 'vitest';
import type { DashboardView } from '../../shared/api/dashboard';
import { ApiError } from '../../shared/api/errors';
import type { ApiClient, ApiRequest } from '../../shared/api/http';
import { renderWithQuery } from '../../test/render';
import { DataWorkspacePage } from './DataWorkspacePage';

const surveyId = '10000000-0000-4000-8000-000000000001';
const hiddenSurveyId = '10000000-0000-4000-8000-000000000002';

const authState = vi.hoisted(() => ({ api: null as ApiClient | null }));

vi.mock('../auth/AuthProvider', () => ({
  AuthProvider: ({ children }: { children: ReactNode }) => children,
  useAuth: () => ({
    api: authState.api,
    session: {
      token: 'data-token',
      expiresAt: Date.now() + 60_000,
      me: { tenantId: 'tenant-a', actorId: 'data-user', roles: [] },
    },
  }),
}));

const dashboard: DashboardView = {
  generatedAt: '2026-10-30T08:00:00Z',
  visibleSections: ['export', 'data', 'survey'],
  summary: { pendingApprovals: 0, publishExceptions: 0, activePreviews: 0, activeExports: 2 },
  tasks: [{
    taskKey: 'export:one',
    kind: 'export_exception',
    surveyId,
    surveyName: '客户满意度',
    status: '文件已过期',
    updatedAt: '2026-10-30T07:00:00Z',
    targetPath: `/surveys/${surveyId}/responses`,
  }],
  surveys: [{
    surveyId,
    name: '客户满意度',
    draftVersion: 2,
    publishedVersion: 1,
    publishState: 'published',
    completedResponses: 12,
    updatedAt: '2026-10-30T08:00:00Z',
    actions: ['responses'],
  }, {
    surveyId: hiddenSurveyId,
    name: '无数据权限问卷',
    draftVersion: 1,
    publishedVersion: null,
    publishState: 'draft',
    completedResponses: null,
    updatedAt: '2026-10-30T08:00:00Z',
    actions: ['edit'],
  }],
  recentWork: [],
};

function apiFrom(handler: (request: ApiRequest<unknown>) => unknown): ApiClient {
  return { request: (request) => Promise.resolve(handler(request as ApiRequest<unknown>)) as never };
}

function renderPage(api: ApiClient) {
  authState.api = api;
  return renderWithQuery(<MemoryRouter><DataWorkspacePage /></MemoryRouter>);
}

beforeEach(() => { authState.api = null; });

describe('DataWorkspacePage', () => {
  test('lists only surveys authorized for response data and exposes real export exceptions', async () => {
    renderPage(apiFrom((request) => {
      expect(request.path).toBe('/v1/dashboard?surveyLimit=50&taskLimit=50');
      return dashboard;
    }));

    expect(await screen.findByText('处理中导出')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '数据' })).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
    const surveys = screen.getByRole('table', { name: '可访问的数据问卷' });
    expect(within(surveys).getByText('客户满意度')).toBeInTheDocument();
    expect(within(surveys).queryByText('无数据权限问卷')).not.toBeInTheDocument();
    expect(within(surveys).getByRole('link', { name: '查看答卷与导出' })).toHaveAttribute(
      'href', `/surveys/${surveyId}/responses`,
    );
    expect(screen.getByRole('link', { name: '处理导出问题' })).toHaveAttribute(
      'href', `/surveys/${surveyId}/responses`,
    );
  });

  test('renders a truthful empty state', async () => {
    renderPage(apiFrom(() => ({ ...dashboard, summary: { ...dashboard.summary, activeExports: 0 }, tasks: [], surveys: [] })));

    expect(await screen.findByText('当前没有可访问的答卷数据。')).toBeInTheDocument();
    expect(screen.getByText('当前没有需要处理的导出问题。')).toBeInTheDocument();
  });

  test('renders forbidden and retryable unavailable states without fake data', async () => {
    const forbidden = renderPage(apiFrom(() => Promise.reject(new ApiError('forbidden', '无权执行当前操作', 403))));
    expect(await screen.findByRole('alert')).toHaveTextContent('你没有访问数据工作区的权限');
    expect(screen.queryByRole('button', { name: '重新加载数据工作区' })).not.toBeInTheDocument();
    forbidden.unmount();

    renderPage(apiFrom(() => Promise.reject(new ApiError('unavailable', '服务暂时不可用', 503))));
    expect(await screen.findByRole('alert')).toHaveTextContent('数据工作区暂时不可用');
    fireEvent.click(screen.getByRole('button', { name: '重新加载数据工作区' }));
  });

  test('treats a successful dashboard without the server data capability as forbidden', async () => {
    renderPage(apiFrom(() => ({ ...dashboard, visibleSections: ['survey'] })));

    expect(await screen.findByRole('alert')).toHaveTextContent('你没有访问数据工作区的权限');
    expect(screen.queryByRole('table', { name: '可访问的数据问卷' })).not.toBeInTheDocument();
  });
});
