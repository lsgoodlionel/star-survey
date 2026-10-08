import { lazy, Suspense } from 'react';
import { createBrowserRouter, Navigate, Outlet, type RouteObject } from 'react-router-dom';
import { AppShell } from './App';
import { AuthCallbackPage } from '../features/auth/AuthCallbackPage';
import { useAuth } from '../features/auth/AuthProvider';
import { WorkspacePage } from '../features/workspace/WorkspacePage';

const DevelopmentTokenPage = import.meta.env.DEV
  ? lazy(() =>
      import('../features/auth/DevTokenPage').then((module) => ({ default: module.DevTokenPage })),
    )
  : null;

function ProtectedRoute() {
  const { session } = useAuth();
  if (!session) return <Navigate to={import.meta.env.DEV ? '/dev/token' : '/login'} replace />;
  return <Outlet />;
}

function LoginPage() {
  return (
    <main className="auth-page">
      <h1>请登录后继续</h1>
      <p>请从组织工作台重新进入问卷管理台。</p>
    </main>
  );
}

function NotFoundPage() {
  return (
    <main className="auth-page">
      <h1>页面不存在</h1>
    </main>
  );
}

export function createAppRoutes(isDevelopment: boolean): RouteObject[] {
  const developmentRoutes: RouteObject[] = [];
  if (isDevelopment && DevelopmentTokenPage) {
    developmentRoutes.push({
      path: '/dev/token',
      element: (
        <Suspense fallback={<p>正在加载</p>}>
          <DevelopmentTokenPage />
        </Suspense>
      ),
    });
  }

  return [
    { path: '/auth/callback', element: <AuthCallbackPage /> },
    { path: '/login', element: <LoginPage /> },
    ...developmentRoutes,
    {
      element: <ProtectedRoute />,
      children: [
        {
          element: <AppShell />,
          children: [
            { index: true, element: <Navigate to="/workspace" replace /> },
            { path: 'workspace', element: <WorkspacePage /> },
          ],
        },
      ],
    },
    { path: '*', element: <NotFoundPage /> },
  ];
}

export function createAppRouter() {
  return createBrowserRouter(createAppRoutes(import.meta.env.DEV));
}
