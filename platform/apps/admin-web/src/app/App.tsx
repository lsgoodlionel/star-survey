import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { Database, FileText, LayoutDashboard, LayoutTemplate, LogOut, Mail, Menu, Send, X } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { Link, NavLink, Outlet } from 'react-router-dom';
import { orgCapabilitiesSchema } from '../shared/api/organization';
import { z } from 'zod';
import { AuthProvider, useAuth } from '../features/auth/AuthProvider';
import { captureEditorRecovery } from '../features/editor/recovery';
import { getDashboard } from '../shared/api/dashboard';
import { useSubscription } from '../features/subscription/SubscriptionPage';
import { ApiError } from '../shared/api/errors';

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
  const { api, logout, session } = useAuth();
  const [navigationOpen, setNavigationOpen] = useState(false);
  const role = roleLabel(session?.me.roles ?? []);
  const navigationCapabilities = useQuery({
    queryKey: ['navigation-capabilities', session?.me.tenantId, session?.me.actorId],
    queryFn: ({ signal }) => getDashboard(api, { surveyLimit: 1, taskLimit: 1 }, signal),
    enabled: Boolean(session),
    retry: false,
    staleTime: 30_000,
  });
  const organizationCapabilities = useQuery({
    queryKey: ['organization-capabilities', session?.me.tenantId, session?.me.actorId],
    queryFn: ({ signal }) => api.request({ path: '/v1/org-connections/capabilities', schema: orgCapabilitiesSchema, signal }),
    enabled: Boolean(session), retry: false, staleTime: 0,
  });
  const canManageOrganization = !organizationCapabilities.isError && !organizationCapabilities.isFetching && organizationCapabilities.data?.canManageSettings === true;
  const memberCapabilities = useQuery({
    queryKey: ['member-capabilities', session?.me.tenantId, session?.me.actorId],
    queryFn: ({ signal }) => api.request({ path: '/v1/members/capabilities', schema: z.object({ canManageMembers: z.boolean() }), signal }),
    enabled: Boolean(session), retry: false, staleTime: 0,
  });
  const canManageMembers = !memberCapabilities.isError && !memberCapabilities.isFetching && memberCapabilities.data?.canManageMembers === true;
  const canOpenDataWorkspace = navigationCapabilities.data?.visibleSections.includes('data') ?? false;
  const subscription = useSubscription();
  const canViewSubscription = Boolean(subscription.data && !subscription.isError)
    || (subscription.error instanceof ApiError && subscription.error.kind === 'not_found');

  return (
    <div className="app-shell">
      <header className="app-header">
        <Link className="app-brand" to="/dashboard" onClick={() => setNavigationOpen(false)}>
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
          <NavLink to="/dashboard" onClick={() => setNavigationOpen(false)}>
            <LayoutDashboard aria-hidden="true" />
            工作台
          </NavLink>
          <NavLink to="/workspace" onClick={() => setNavigationOpen(false)}>
            <FileText aria-hidden="true" />
            项目与问卷
          </NavLink>
          <NavLink to="/templates" onClick={() => setNavigationOpen(false)}>
            <LayoutTemplate aria-hidden="true" />
            模板中心
          </NavLink>
          <NavLink to="/delivery" onClick={() => setNavigationOpen(false)}>
            <Send aria-hidden="true" />
            投放
          </NavLink>
          {canOpenDataWorkspace ? (
            <NavLink to="/data" onClick={() => setNavigationOpen(false)}>
              <Database aria-hidden="true" />
              数据
            </NavLink>
          ) : null}
          {canManageOrganization ? <NavLink to="/settings/organization" onClick={() => setNavigationOpen(false)}>组织连接</NavLink> : null}
          {canManageMembers ? <NavLink to="/settings/members" onClick={() => setNavigationOpen(false)}>成员管理</NavLink> : null}
          <NavLink to="/settings/email" onClick={() => setNavigationOpen(false)}>
            <Mail aria-hidden="true" />
            邮件服务
          </NavLink>
          {canViewSubscription && !subscription.isFetching ? (
            <NavLink to="/settings/subscription" onClick={() => setNavigationOpen(false)}>当前订阅</NavLink>
          ) : null}
        </nav>
        <div className="account-actions">
          <details className="account-menu">
            <summary>
              <span>{session?.me.actorId}</span>
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
