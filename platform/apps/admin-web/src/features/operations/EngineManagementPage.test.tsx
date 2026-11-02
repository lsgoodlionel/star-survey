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
const tenant = { id: '10000000-0000-4000-8000-000000000001', code: 'acme', name: '示例租户', status: 'active', createdAt: '2026-11-02T00:00:00Z' };
const engine = { id: 'engine-test-01', tenantId: tenant.id, baseUrl: 'https://engine.invalid', status: 'active', createdAt: '2026-11-02T00:00:00Z' };
function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><RouterProvider router={createMemoryRouter(createAppRoutes(false), { initialEntries: [`/operations/tenants/${tenant.id}/engines`] })} /></QueryClientProvider>);
  return client;
}
beforeEach(() => {
  auth.allowed = true;
  server.use(http.get('/v1/platform-admin/capabilities', () => HttpResponse.json({ managePlatform: auth.allowed })),
    http.get('/v1/platform/tenants/:id', () => HttpResponse.json(tenant)));
});

test('registers an isolated instance only after confirmation and handles lifecycle', async () => {
  let rows: typeof engine[] = [], writes = 0;
  server.use(http.get('/v1/platform/tenants/:tenant/engine-instances', () => HttpResponse.json(rows)),
    http.post('/v1/platform/tenants/:tenant/engine-instances', async ({ request }) => {
      writes++; expect(await request.json()).toEqual({ id: engine.id, baseUrl: engine.baseUrl }); rows = [engine]; return HttpResponse.json(engine, { status: 201 });
    }),
    http.post('/v1/platform/tenants/:tenant/engine-instances/:id/status', async ({ request }) => {
      writes++; const body = await request.json() as { status: string }; rows = [{ ...engine, status: body.status }]; return HttpResponse.json(rows[0]);
    }));
  renderPage();
  await userEvent.type(await screen.findByLabelText('实例标识'), engine.id);
  await userEvent.type(screen.getByLabelText('引擎基础地址'), engine.baseUrl);
  await userEvent.click(screen.getByRole('button', { name: '检查登记内容' }));
  expect(writes).toBe(0);
  await userEvent.click(screen.getByRole('button', { name: '确认登记实例' }));
  expect(await screen.findByText(engine.id)).toBeInTheDocument();
  await userEvent.click(await screen.findByRole('button', { name: '停止接收新流量' }));
  await userEvent.click(screen.getByRole('button', { name: '确认停止接收新流量' }));
  await userEvent.click(await screen.findByRole('button', { name: '退役实例' }));
  expect(screen.getByText('退役后无法恢复。')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '确认退役实例' }));
  expect(await screen.findByText('退役为终态，无法恢复')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '恢复接收流量' })).not.toBeInTheDocument();
  expect(writes).toBe(3);
});

test('does not load or display instances without the server capability', async () => {
  auth.allowed = false; let calls = 0;
  server.use(http.get('/v1/platform/tenants/:tenant/engine-instances', () => { calls++; return HttpResponse.json([engine]); }));
  renderPage();
  expect(await screen.findByRole('heading', { name: '需要平台运营权限' })).toBeInTheDocument();
  expect(calls).toBe(0);
});

test('revocation clears previously displayed instances and registration controls', async () => {
  server.use(http.get('/v1/platform/tenants/:tenant/engine-instances', () => HttpResponse.json([engine])));
  const client = renderPage();
  expect(await screen.findByText(engine.id)).toBeInTheDocument();
  auth.allowed = false;
  await client.invalidateQueries({ queryKey: ['platform-capabilities'] });
  expect(await screen.findByRole('heading', { name: '需要平台运营权限' })).toBeInTheDocument();
  expect(screen.queryByText(engine.id)).not.toBeInTheDocument();
  expect(screen.queryByLabelText('实例标识')).not.toBeInTheDocument();
});

test('conflict refreshes state and never repeats the write', async () => {
  let reads = 0, writes = 0;
  server.use(http.get('/v1/platform/tenants/:tenant/engine-instances', () => HttpResponse.json([{ ...engine, status: ++reads === 1 ? 'active' : 'retired' }])),
    http.post('/v1/platform/tenants/:tenant/engine-instances/:id/status', () => { writes++; return new HttpResponse(null, { status: 409 }); }));
  renderPage();
  await userEvent.click(await screen.findByRole('button', { name: '停止接收新流量' }));
  await userEvent.click(screen.getByRole('button', { name: '确认停止接收新流量' }));
  expect(await screen.findByText('退役为终态，无法恢复')).toBeInTheDocument();
  expect(writes).toBe(1);
});

test('rejects addresses containing credentials or query parameters before any write', async () => {
  let writes = 0;
  server.use(http.get('/v1/platform/tenants/:tenant/engine-instances', () => HttpResponse.json([])),
    http.post('/v1/platform/tenants/:tenant/engine-instances', () => { writes++; return new HttpResponse(null, { status: 201 }); }));
  renderPage();
  await userEvent.type(await screen.findByLabelText('实例标识'), engine.id);
  for (const url of ['https://user:password@engine.invalid', 'https://engine.invalid/?token=value']) {
    const input = screen.getByLabelText('引擎基础地址');
    await userEvent.clear(input); await userEvent.type(input, url);
    await userEvent.click(screen.getByRole('button', { name: '检查登记内容' }));
    expect(screen.getByText('请检查实例标识和不含凭据的 HTTP(S) 地址')).toBeInTheDocument();
  }
  expect(writes).toBe(0);
});

test('a closed tenant cannot register a new instance', async () => {
  server.use(http.get('/v1/platform/tenants/:id', () => HttpResponse.json({ ...tenant, status: 'closed' })),
    http.get('/v1/platform/tenants/:tenant/engine-instances', () => HttpResponse.json([])));
  renderPage();
  expect(await screen.findByText('租户已关闭，不能登记新实例。')).toBeInTheDocument();
  expect(screen.queryByLabelText('实例标识')).not.toBeInTheDocument();
});

test('a denied instance read hides the tenant and old instance data', async () => {
  server.use(http.get('/v1/platform/tenants/:tenant/engine-instances', () => new HttpResponse(null, { status: 403 })));
  renderPage();
  expect(await screen.findByRole('heading', { name: '需要平台运营权限' })).toBeInTheDocument();
  expect(screen.queryByText('示例租户')).not.toBeInTheDocument();
});

test('does not render credentials or query strings in an existing address', async () => {
  server.use(http.get('/v1/platform/tenants/:tenant/engine-instances', () => HttpResponse.json([{ ...engine, baseUrl: 'https://user:password@engine.invalid/path?token=value' }])));
  renderPage();
  expect(await screen.findByText('地址：https://engine.invalid/path')).toBeInTheDocument();
  expect(screen.queryByText(/password|token=value/)).not.toBeInTheDocument();
});
