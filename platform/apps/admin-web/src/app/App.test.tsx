import { fireEvent, render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { expect, test, vi } from 'vitest';
import { AppShell } from './App';

const authState = vi.hoisted(() => ({ visibleSections: ['data'] as string[] }));

vi.mock('../features/auth/AuthProvider', () => ({
  AuthProvider: ({ children }: { children: ReactNode }) => children,
  useAuth: () => ({
    api: {
      request: vi.fn().mockImplementation(() => Promise.resolve({
        generatedAt: '2026-10-30T08:00:00Z',
        visibleSections: authState.visibleSections,
        summary: { pendingApprovals: 0, publishExceptions: 0, activePreviews: 0, activeExports: 0 },
        tasks: [], surveys: [], recentWork: [],
      })),
    },
    logout: vi.fn(),
    session: {
      token: 'app-shell-token',
      expiresAt: Date.now() + 60_000,
      me: {
        tenantId: 'tenant-a',
        actorId: 'author-7',
        roles: ['tenant_owner'],
      },
    },
  }),
}));

function renderAppShell() {
  const router = createMemoryRouter(
    [{
      element: <AppShell />,
      children: [
        { path: '/workspace', element: <p>工作区内容</p> },
        { path: '/data', element: <p>数据工作区内容</p> },
        { path: '/templates', element: <p>模板中心内容</p> },
      ],
    }],
    { initialEntries: ['/workspace'] },
  );
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

test('rendersOnlyAvailableProductNavigationAndKeepsTechnicalIdentityDetailsSeparate', async () => {
  renderAppShell();

  const navigation = screen.getByRole('navigation', { name: '全局导航' });
  expect(within(navigation).getByRole('link', { name: '工作台' })).toHaveAttribute(
    'href',
    '/dashboard',
  );
  expect(within(navigation).getByRole('link', { name: '项目与问卷' })).toHaveAttribute(
    'href',
    '/workspace',
  );
  expect(within(navigation).getByRole('link', { name: '项目与问卷' })).toHaveAttribute(
    'aria-current',
    'page',
  );
  expect(within(navigation).getByRole('link', { name: '工作台' })).not.toHaveAttribute(
    'aria-current',
  );
  expect(within(navigation).getByRole('link', { name: '模板中心' })).toHaveAttribute('href', '/templates');
  expect(within(navigation).getByRole('link', { name: '投放' })).toHaveAttribute('href', '/delivery');
  expect(await within(navigation).findByRole('link', { name: '数据' })).toHaveAttribute('href', '/data');
  expect(within(navigation).getByRole('link', { name: '邮件服务' })).toHaveAttribute('href', '/settings/email');
  expect(new Set(within(navigation).getAllByRole('link').map((link) => link.getAttribute('href'))).size)
    .toBe(6);
  expect(within(navigation).queryByText('待审批')).not.toBeInTheDocument();
  expect(within(navigation).queryByRole('link', { name: /admin/i })).not.toBeInTheDocument();

  expect(screen.getByRole('link', { name: '问卷管理台' })).toHaveAttribute('href', '/dashboard');
  const accountSummary = screen.getByText('租户管理员').closest('summary');
  expect(accountSummary).not.toBeNull();
  expect(within(accountSummary!).getByText('author-7')).toBeInTheDocument();
  expect(screen.getByText('租户管理员')).toBeInTheDocument();
  const technicalDetails = screen.getByText('技术信息').closest('details');
  expect(technicalDetails).not.toBeNull();
  expect(within(technicalDetails!).getAllByText('author-7')).toHaveLength(2);
  expect(within(technicalDetails!).getByText('tenant-a')).toBeInTheDocument();
});

test('hidesDataNavigationWhenTheServerDoesNotGrantTheDataSection', async () => {
  authState.visibleSections = ['survey'];
  renderAppShell();

  expect(screen.getByRole('navigation', { name: '全局导航' })).toBeInTheDocument();
  expect(screen.queryByRole('link', { name: '数据' })).not.toBeInTheDocument();
  authState.visibleSections = ['data'];
});

test('exposesAnAccessibleMobileNavigationToggle', () => {
  renderAppShell();

  const toggle = screen.getByRole('button', { name: '打开主导航' });
  expect(toggle).toHaveAttribute('aria-expanded', 'false');
  expect(toggle).toHaveAttribute('aria-controls', 'global-navigation');

  fireEvent.click(toggle);

  expect(toggle).toHaveAttribute('aria-expanded', 'true');
  expect(toggle).toHaveAccessibleName('关闭主导航');
  expect(screen.getByRole('navigation', { name: '全局导航' })).toHaveAttribute(
    'id',
    'global-navigation',
  );
});

test('closes mobile navigation after selecting template center', () => {
  renderAppShell();
  fireEvent.click(screen.getByRole('button', { name: '打开主导航' }));
  fireEvent.click(screen.getByRole('link', { name: '模板中心' }));
  expect(screen.getByRole('navigation', { name: '全局导航' })).toHaveAttribute('data-open', 'false');
});
