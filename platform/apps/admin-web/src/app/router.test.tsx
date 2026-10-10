import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { createMemoryRouter, Outlet, RouterProvider, useLocation } from 'react-router-dom';
import { expect, test, vi } from 'vitest';
import { createAppRoutes } from './router';

vi.mock('../features/auth/AuthProvider', () => ({
  AuthProvider: ({ children }: { children: ReactNode }) => children,
  useAuth: () => ({
    api: { request: vi.fn() },
    logout: vi.fn(),
    session: {
      token: 'router-token',
      expiresAt: Date.now() + 60_000,
      me: { tenantId: 'tenant-a', actorId: 'author-7', roles: ['editor'] },
    },
  }),
}));

vi.mock('../features/dashboard/DashboardPage', () => ({
  DashboardPage: () => <p>业务工作台内容</p>,
}));

vi.mock('../features/workspace/WorkspacePage', () => ({
  WorkspacePage: () => <LocationProbe label="项目与问卷内容" />,
}));

vi.mock('./SurveyShell', () => ({
  SurveyShell: () => <Outlet />,
}));

vi.mock('../features/editor/EditorPage', () => ({
  EditorRoutePage: () => <LocationProbe label="编辑内容" />,
}));

function LocationProbe({ label }: { label: string }) {
  const location = useLocation();
  return <p>{label}:{location.pathname}{location.search}</p>;
}

function renderRoute(initialEntry: string) {
  const router = createMemoryRouter(createAppRoutes(false), { initialEntries: [initialEntry] });
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

test('redirects the protected product root to the business dashboard', async () => {
  const router = renderRoute('/');

  expect(await screen.findByText('业务工作台内容')).toBeInTheDocument();
  expect(router.state.location.pathname).toBe('/dashboard');
});

test('keeps dashboard and workspace as distinct active destinations', async () => {
  const dashboardRouter = renderRoute('/dashboard');
  expect(await screen.findByText('业务工作台内容')).toBeInTheDocument();
  expect(dashboardRouter.state.location.pathname).toBe('/dashboard');

  dashboardRouter.dispose();
});

test('retains workspace query compatibility', async () => {
  const router = renderRoute('/workspace?kind=survey');

  expect(await screen.findByText('项目与问卷内容:/workspace?kind=survey')).toBeInTheDocument();
  expect(router.state.location.search).toBe('?kind=survey');
});

test('redirects a survey index to its edit page', async () => {
  const surveyId = '30000000-0000-4000-8000-000000000001';
  const router = renderRoute(`/surveys/${surveyId}`);

  await waitFor(() => expect(router.state.location.pathname).toBe(`/surveys/${surveyId}/edit`));
  expect(screen.getByText(`编辑内容:/surveys/${surveyId}/edit`)).toBeInTheDocument();
});
