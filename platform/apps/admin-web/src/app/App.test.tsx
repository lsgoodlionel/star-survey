import { fireEvent, render, screen, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { expect, test, vi } from 'vitest';
import { AppShell } from './App';

vi.mock('../features/auth/AuthProvider', () => ({
  AuthProvider: ({ children }: { children: ReactNode }) => children,
  useAuth: () => ({
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
      children: [{ path: '/workspace', element: <p>工作区内容</p> }],
    }],
    { initialEntries: ['/workspace'] },
  );
  return render(<RouterProvider router={router} />);
}

test('rendersOnlyAvailableProductNavigationAndKeepsTechnicalIdentityDetailsSeparate', () => {
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
  expect(new Set(within(navigation).getAllByRole('link').map((link) => link.getAttribute('href'))).size)
    .toBe(2);
  expect(within(navigation).queryByText('模板')).not.toBeInTheDocument();
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
