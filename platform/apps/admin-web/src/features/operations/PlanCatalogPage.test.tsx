import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { beforeEach, expect, test, vi } from 'vitest';
import { createAppRoutes } from '../../app/router';
import { server } from '../../test/server';

const auth = vi.hoisted(() => ({ allowed: true }));
vi.mock('../auth/AuthProvider', async () => {
  const { createApiClient } = await import('../../shared/api/http');
  return { useAuth: () => ({ api: createApiClient(), logout: vi.fn(), session: { me: { tenantId: 't', actorId: 'a', roles: [] } } }) };
});
const plan = { id: '10000000-0000-4000-8000-000000000001', planCode: 'standard', version: 2,
  capabilities: ['survey.read'], quotas: { 'member.seats': 5 }, exportWindowDays: 30, publishedAt: '2026-11-02T00:00:00Z' };
function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><RouterProvider router={createMemoryRouter(createAppRoutes(false), { initialEntries: ['/operations/plans'] })} /></QueryClientProvider>);
  return client;
}
beforeEach(() => {
  auth.allowed = true;
  server.use(http.get('/v1/platform-admin/capabilities', () => HttpResponse.json({ managePlatform: auth.allowed })));
});

test('shows the latest immutable version and its actual capabilities and limits', async () => {
  server.use(http.get('/v1/platform/plan-catalog', () => HttpResponse.json({ items: [plan], nextCursor: null })));
  renderPage();
  expect(await screen.findByRole('heading', { name: '套餐目录' })).toBeInTheDocument();
  expect(await screen.findByText('standard')).toBeInTheDocument();
  expect(screen.getByText('survey.read')).toBeInTheDocument();
  expect(screen.getByText('member.seats: 5')).toBeInTheDocument();
  expect(screen.getByText('30 天')).toBeInTheDocument();
});

test('follows server cursor and hides old data when denied', async () => {
  server.use(http.get('/v1/platform/plan-catalog', ({ request }) => new URL(request.url).searchParams.get('after')
    ? new HttpResponse(null, { status: 403 }) : HttpResponse.json({ items: [plan], nextCursor: 'standard' })));
  renderPage();
  await userEvent.click(await screen.findByRole('button', { name: '下一页' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('无法加载套餐目录');
  expect(screen.queryByText('standard')).not.toBeInTheDocument();
});

test('does not query for an unauthorized tenant or retain data after revocation', async () => {
  let requests = 0;
  server.use(http.get('/v1/platform/plan-catalog', () => { requests++; return HttpResponse.json({ items: [plan], nextCursor: null }); }));
  auth.allowed = false;
  const client = renderPage();
  expect(await screen.findByRole('heading', { name: '需要平台运营权限' })).toBeInTheDocument();
  expect(requests).toBe(0);
  auth.allowed = true;
  await client.invalidateQueries({ queryKey: ['platform-capabilities'] });
  expect(await screen.findByText('standard')).toBeInTheDocument();
  auth.allowed = false;
  await client.invalidateQueries({ queryKey: ['platform-capabilities'] });
  expect(await screen.findByRole('heading', { name: '需要平台运营权限' })).toBeInTheDocument();
  expect(screen.queryByText('standard')).not.toBeInTheDocument();
});
