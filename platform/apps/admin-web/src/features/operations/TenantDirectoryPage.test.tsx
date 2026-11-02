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

test('confirms an allowed status change and refreshes the authoritative tenant', async () => {
  let current = { ...tenant }, calls = 0;
  server.use(http.get('/v1/platform/tenants/:id', () => HttpResponse.json(current)),
    http.post('/v1/platform/tenants/:id/status', async ({ request }) => {
      calls++;
      expect(await request.json()).toEqual({ status: 'suspended' });
      current = { ...current, status: 'suspended' };
      return HttpResponse.json(current);
    }));
  renderPage(`/operations/tenants/${tenant.id}`);
  await userEvent.click(await screen.findByRole('button', { name: '暂停租户' }));
  expect(calls).toBe(0);
  await userEvent.click(screen.getByRole('button', { name: '确认暂停租户' }));
  expect(await screen.findByText('租户状态已更新')).toBeInTheDocument();
  expect(await screen.findByRole('button', { name: '启用租户' })).toBeInTheDocument();
  expect(calls).toBe(1);
});

test('closed tenant is terminal and offers no status action', async () => {
  server.use(http.get('/v1/platform/tenants/:id', () => HttpResponse.json({ ...tenant, status: 'closed' })));
  renderPage(`/operations/tenants/${tenant.id}`);
  expect(await screen.findByText('关闭为终态，无法重新启用')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /暂停租户|启用租户|关闭租户/ })).not.toBeInTheDocument();
});

test('closing requires a separate confirmation and explains the terminal state', async () => {
  let calls = 0;
  server.use(http.get('/v1/platform/tenants/:id', () => HttpResponse.json(tenant)),
    http.post('/v1/platform/tenants/:id/status', () => { calls++; return HttpResponse.json({ ...tenant, status: 'closed' }); }));
  renderPage(`/operations/tenants/${tenant.id}`);
  await userEvent.click(await screen.findByRole('button', { name: '关闭租户' }));
  expect(screen.getByText('关闭为终态，无法重新启用。')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '取消' }));
  expect(calls).toBe(0);
});

test('a forbidden status change clears tenant details after permission loss', async () => {
  server.use(http.get('/v1/platform/tenants/:id', () => HttpResponse.json(tenant)),
    http.post('/v1/platform/tenants/:id/status', () => new HttpResponse(null, { status: 403 })));
  renderPage(`/operations/tenants/${tenant.id}`);
  await userEvent.click(await screen.findByRole('button', { name: '暂停租户' }));
  await userEvent.click(screen.getByRole('button', { name: '确认暂停租户' }));
  expect(await screen.findByRole('heading', { name: '需要平台运营权限' })).toBeInTheDocument();
  expect(screen.queryByText('acme')).not.toBeInTheDocument();
});

test('a conflict reloads current status and does not retry the mutation', async () => {
  let calls = 0, reads = 0;
  server.use(http.get('/v1/platform/tenants/:id', () => HttpResponse.json({ ...tenant, status: ++reads === 1 ? 'active' : 'closed' })),
    http.post('/v1/platform/tenants/:id/status', () => { calls++; return new HttpResponse(null, { status: 409 }); }));
  renderPage(`/operations/tenants/${tenant.id}`);
  await userEvent.click(await screen.findByRole('button', { name: '暂停租户' }));
  await userEvent.click(screen.getByRole('button', { name: '确认暂停租户' }));
  expect(await screen.findByText('关闭为终态，无法重新启用')).toBeInTheDocument();
  expect(calls).toBe(1);
});
