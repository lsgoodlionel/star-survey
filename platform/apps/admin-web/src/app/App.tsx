import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { FileText, LayoutDashboard, LogOut, Menu, X } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { Link, Outlet } from 'react-router-dom';
import { AuthProvider, useAuth } from '../features/auth/AuthProvider';
import { captureEditorRecovery } from '../features/editor/recovery';

export function AppProviders({ children }: { children: ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { retry: 1, staleTime: 30_000 },
          mutations: { retry: false },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider beforeSessionClear={captureEditorRecovery}>{children}</AuthProvider>
    </QueryClientProvider>
  );
}

export function AppShell() {
  const { logout, session } = useAuth();
  const [navigationOpen, setNavigationOpen] = useState(false);
  const role = roleLabel(session?.me.roles ?? []);

  return (
    <div className="app-shell">
      <header className="app-header">
        <Link className="app-brand" to="/workspace" onClick={() => setNavigationOpen(false)}>
          问卷管理台
        </Link>
        <button
          className="app-navigation-toggle"
          type="button"
          title={navigationOpen ? '关闭主导航' : '打开主导航'}
          aria-label={navigationOpen ? '关闭主导航' : '打开主导航'}
          aria-controls="global-navigation"
          aria-expanded={navigationOpen}
          onClick={() => setNavigationOpen((open) => !open)}
        >
          {navigationOpen ? <X aria-hidden="true" /> : <Menu aria-hidden="true" />}
        </button>
        <nav
          id="global-navigation"
          className="app-navigation"
          aria-label="全局导航"
          data-open={navigationOpen}
        >
          <Link to="/workspace" onClick={() => setNavigationOpen(false)}>
            <LayoutDashboard aria-hidden="true" />
            工作台
          </Link>
          <Link to="/workspace?kind=survey" onClick={() => setNavigationOpen(false)}>
            <FileText aria-hidden="true" />
            问卷
          </Link>
        </nav>
        <div className="account-actions">
          <details className="account-menu">
            <summary>
              <span>测试管理员</span>
              <small>{role}</small>
            </summary>
            <div className="account-menu-panel">
              <strong>技术信息</strong>
              <dl>
                <div><dt>用户标识</dt><dd>{session?.me.actorId}</dd></div>
                <div><dt>租户标识</dt><dd>{session?.me.tenantId}</dd></div>
              </dl>
              <button type="button" onClick={() => void logout()}>
                <LogOut aria-hidden="true" size={18} />
                退出登录
              </button>
            </div>
          </details>
        </div>
      </header>
      <div className="app-content">
        <Outlet />
      </div>
    </div>
  );
}

function roleLabel(roles: string[]) {
  if (roles.includes('tenant_owner')) return '租户管理员';
  if (roles.includes('approver')) return '审批人员';
  if (roles.includes('publisher')) return '发布人员';
  if (roles.includes('editor')) return '编辑人员';
  return '组织成员';
}
