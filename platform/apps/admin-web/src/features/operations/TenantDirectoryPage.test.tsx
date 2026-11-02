import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { beforeEach, expect, test, vi } from 'vitest';
import { createAppRoutes } from '../../app/router';
import { server } from '../../test/server';

const auth = vi.hoisted(() => ({ roles: [] as string[], allowed: true }));
vi.mock('../auth/AuthProvider', async () => {
  const { createApiClient } = await import('../../shared/api/http');
  return { useAuth: () => ({ api: createApiClient(), logout: vi.fn(), session: { me: { tenantId: 't', actorId: 'a', roles: auth.roles } } }) };
});
const tenant = { id: '10000000-0000-4000-8000-000000000001', code: 'acme', name: '示例租户', status: 'active', createdAt: '2026-11-02T00:00:00Z' };
function renderPage(path = '/operations/tenants') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(createAppRoutes(false), { initialEntries: [path] });
  render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
  return client;
}
beforeEach(() => {
  auth.roles = []; auth.allowed = true;
  server.use(http.get('/v1/platform-admin/capabilities', () => HttpResponse.json({ managePlatform: auth.allowed })));
});

test('operator browses a bounded directory and opens a tenant detail', async () => {
  server.use(http.get('/v1/platform/tenants/directory', () => HttpResponse.json({ items: [tenant], nextCursor: null })),
    http.get('/v1/platform/tenants/:id', () => HttpResponse.json(tenant)));
  renderPage();
  await userEvent.click(await screen.findByRole('link', { name: /示例租户/ }));
  expect(await screen.findByRole('heading', { name: '示例租户' })).toBeInTheDocument();
  expect(screen.getByText('acme')).toBeInTheDocument();
});

test('tenant administrator cannot fetch the directory by direct navigation', async () => {
  auth.roles = ['tenant_owner'];
  auth.allowed = false;
  let requests = 0;
  server.use(http.get('/v1/platform/tenants/directory', () => { requests++; return HttpResponse.json({ items: [], nextCursor: null }); }));
  renderPage();
  expect(await screen.findByText('需要平台运营权限')).toBeInTheDocument();
  expect(requests).toBe(0);
});


test('directory follows the server cursor and handles a revoked request without stale rows', async () => {
  let calls = 0;
  server.use(http.get('/v1/platform/tenants/directory', ({ request }) => {
    calls++;
    if (new URL(request.url).searchParams.get('after') === 'acme') return new HttpResponse(null, { status: 403 });
    return HttpResponse.json({ items: [tenant], nextCursor: 'acme' });
  }));
  renderPage();
  await userEvent.click(await screen.findByRole('button', { name: '下一页' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('无法加载租户');
  expect(screen.queryByText('示例租户')).not.toBeInTheDocument();
  expect(calls).toBe(2);
});

test('operator signs in to the separate operations entry', async () => {
  server.use(http.get('/v1/platform/tenants/directory', () => HttpResponse.json({ items: [tenant], nextCursor: null })));
  renderPage('/');
  expect(await screen.findByRole('heading', { name: '平台租户' })).toBeInTheDocument();
  expect(screen.queryByRole('navigation', { name: '全局导航' })).not.toBeInTheDocument();
});

test('operator can reach the existing platform template management journey from operations', async () => {
  server.use(http.get('/v1/platform/tenants/directory', () => HttpResponse.json({ items: [tenant], nextCursor: null })));
  renderPage();
  expect(await screen.findByRole('link', { name: '模板中心' })).toHaveAttribute('href', '/templates');
});

test('removes directory rows when same-page platform capability is revoked', async () => {
  server.use(http.get('/v1/platform/tenants/directory', () => HttpResponse.json({ items: [tenant], nextCursor: null })));
  const client = renderPage();
  expect(await screen.findByRole('link', { name: /示例租户/ })).toBeInTheDocument();
  auth.allowed = false;
  await client.invalidateQueries({ queryKey: ['platform-capabilities'] });
  expect(await screen.findByRole('heading', { name: '需要平台运营权限' })).toBeInTheDocument();
  expect(screen.queryByRole('link', { name: /示例租户/ })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '退出登录' })).toBeInTheDocument();
});
