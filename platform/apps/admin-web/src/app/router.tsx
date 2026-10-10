import { lazy, Suspense } from 'react';
import { createBrowserRouter, Navigate, Outlet, type RouteObject } from 'react-router-dom';
import { AppShell } from './App';
import { SurveyShell } from './SurveyShell';
import { AuthCallbackPage } from '../features/auth/AuthCallbackPage';
import { useAuth } from '../features/auth/AuthProvider';
import { WorkspacePage } from '../features/workspace/WorkspacePage';
import { EditorRoutePage } from '../features/editor/EditorPage';

const DashboardPage = lazy(() =>
  import('../features/dashboard/DashboardPage').then((module) => ({ default: module.DashboardPage })),
);

const PublishRoutePage = lazy(() =>
  import('../features/publish/PublishPage').then((module) => ({ default: module.PublishRoutePage })),
);
const VersionDetailRoutePage = lazy(() =>
  import('../features/publish/VersionDetailPage').then((module) => ({
    default: module.VersionDetailRoutePage,
  })),
);
const ImportRoutePage = lazy(() =>
  import('../features/import/ImportPage').then((module) => ({ default: module.ImportRoutePage })),
);
const PreviewRoutePage = lazy(() =>
  import('../features/preview/PreviewPage').then((module) => ({ default: module.PreviewRoutePage })),
);
const ResponsesRoutePage = lazy(() =>
  import('../features/responses/ResponsesPage').then((module) => ({ default: module.ResponsesRoutePage })),
);

const testTokenEnabled = import.meta.env.DEV || import.meta.env.VITE_E2E === 'true';
const DevelopmentTokenPage = testTokenEnabled
  ? lazy(() =>
      import('../features/auth/DevTokenPage').then((module) => ({ default: module.DevTokenPage })),
    )
  : null;

function ProtectedRoute() {
  const { session } = useAuth();
  if (!session) return <Navigate to={testTokenEnabled ? '/dev/token' : '/login'} replace />;
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

export function createAppRoutes(isDevelopment: boolean, isE2E = false): RouteObject[] {
  const developmentRoutes: RouteObject[] = [];
  if ((isDevelopment || isE2E) && DevelopmentTokenPage) {
    developmentRoutes.push({
      path: '/dev/token',
      element: (
        <Suspense fallback={<p>正在加载</p>}>
          <DevelopmentTokenPage mode={isE2E ? 'e2e' : 'development'} />
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
            { index: true, element: <Navigate to="/dashboard" replace /> },
            {
              path: 'dashboard',
              element: (
                <Suspense fallback={<p>正在加载工作台</p>}>
                  <DashboardPage />
                </Suspense>
              ),
            },
            { path: 'workspace', element: <WorkspacePage /> },
            {
              path: 'surveys/:surveyId',
              element: <SurveyShell />,
              children: [
                { index: true, element: <Navigate to="edit" replace /> },
                { path: 'edit', element: <EditorRoutePage /> },
                {
                  path: 'import',
                  element: (
                    <Suspense fallback={<p>正在加载导入工具</p>}>
                      <ImportRoutePage />
                    </Suspense>
                  ),
                },
                {
                  path: 'preview',
                  element: (
                    <Suspense fallback={<p>正在加载快速预览</p>}>
                      <PreviewRoutePage />
                    </Suspense>
                  ),
                },
                {
                  path: 'publish',
                  element: (
                    <Suspense fallback={<p>正在加载发布信息</p>}>
                      <PublishRoutePage />
                    </Suspense>
                  ),
                },
                {
                  path: 'responses',
                  element: (
                    <Suspense fallback={<p>正在加载答卷数据</p>}>
                      <ResponsesRoutePage />
                    </Suspense>
                  ),
                },
                {
                  path: 'versions/:version',
                  element: (
                    <Suspense fallback={<p>正在加载版本</p>}>
                      <VersionDetailRoutePage />
                    </Suspense>
                  ),
                },
              ],
            },
          ],
        },
      ],
    },
    { path: '*', element: <NotFoundPage /> },
  ];
}

export function createAppRouter() {
  return createBrowserRouter(
    createAppRoutes(import.meta.env.DEV, import.meta.env.VITE_E2E === 'true'),
  );
}
