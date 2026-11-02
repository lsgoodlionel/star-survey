import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { beforeEach, expect, test, vi } from 'vitest';
const identity = vi.hoisted(() => ({ tenant: 'tenant-a', actor: 'owner' }));
beforeEach(() => { identity.tenant = 'tenant-a'; identity.actor = 'owner'; });
import { createAppRoutes } from '../../app/router';
import { server } from '../../test/server';

vi.mock('../auth/AuthProvider', async () => {
  const { createApiClient } = await import('../../shared/api/http');
  return { useAuth: () => ({ api: createApiClient(), logout: vi.fn(), session: { me: { tenantId: identity.tenant, actorId: identity.actor, roles: [] } } }) };
});
const connection = { id: '10000000-0000-4000-8000-000000000001', provider: 'wecom', corpId: 'corp-a', appId: 'app-a', secretRef: 'ORG_SECRET', unknownUserPolicy: 'require_preauthorized', enabled: true, redirectUri: null, eventTokenRef: null, eventKeyRef: null, eventUrl: null };
function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><RouterProvider router={createMemoryRouter(createAppRoutes(false), { initialEntries: ['/settings/organization'] })} /></QueryClientProvider>);
}
test('settings administrator can create a connection using a secret reference, not a secret', async () => {
  let submitted: unknown;
  server.use(http.get('/v1/org-connections/capabilities', () => HttpResponse.json({ canManageSettings: true, canManageMembers: false })),
    http.get('/v1/org-connections', () => HttpResponse.json([connection])),
    http.post('/v1/org-connections', async ({ request }) => { submitted = await request.json(); return HttpResponse.json(connection, { status: 201 }); }));
  renderPage();
  expect(await screen.findByText('corp-a')).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText('组织标识'), 'new-corp');
  await userEvent.type(screen.getByLabelText('应用标识'), 'new-app');
  await userEvent.type(screen.getByLabelText('密钥引用名'), 'NEW_SECRET');
  await userEvent.click(screen.getByRole('button', { name: '创建连接' }));
  expect(submitted).toEqual({ provider: 'wecom', corpId: 'new-corp', appId: 'new-app', secretRef: 'NEW_SECRET', unknownUserPolicy: 'require_preauthorized' });
  expect(screen.queryByRole('button', { name: '同步组织' })).not.toBeInTheDocument();
});
test('member management alone cannot fetch settings or see create actions', async () => {
  let requests = 0;
  server.use(http.get('/v1/org-connections/capabilities', () => HttpResponse.json({ canManageSettings: false, canManageMembers: true })),
    http.get('/v1/org-connections', () => { requests++; return HttpResponse.json([connection]); }));
  renderPage();
  expect(await screen.findByRole('alert')).toHaveTextContent('无权管理组织连接');
  expect(screen.queryByRole('button', { name: '创建连接' })).not.toBeInTheDocument();
  expect(requests).toBe(0);
});

test('refresh after losing settings permission hides prior connection data', async () => {
  let allowed = true;
  server.use(http.get('/v1/org-connections/capabilities', () => HttpResponse.json({ canManageSettings: allowed, canManageMembers: false })),
    http.get('/v1/org-connections', () => HttpResponse.json([connection])));
  renderPage();
  expect(await screen.findByText('corp-a')).toBeInTheDocument();
  allowed = false;
  await userEvent.click(screen.getByRole('button', { name: '刷新连接' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('无权管理组织连接');
  expect(screen.queryByText('corp-a')).not.toBeInTheDocument();
});

test('settings administrator can edit connection references and policy', async () => {
  let submitted: unknown;
  server.use(http.get('/v1/org-connections/capabilities', () => HttpResponse.json({ canManageSettings: true, canManageMembers: false })),
    http.get('/v1/org-connections', () => HttpResponse.json([connection])),
    http.put('/v1/org-connections/:id', async ({ request }) => { submitted = await request.json(); return HttpResponse.json(connection); }));
  renderPage();
  await userEvent.click(await screen.findByRole('button', { name: '编辑连接' }));
  await userEvent.clear(screen.getByLabelText('新的密钥引用名'));
  await userEvent.type(screen.getByLabelText('新的密钥引用名'), 'ROTATED_REF');
  await userEvent.click(screen.getByRole('button', { name: '保存连接设置' }));
  expect(submitted).toEqual({ secretRef: 'ROTATED_REF', unknownUserPolicy: 'require_preauthorized', eventTokenRef: null, eventKeyRef: null });
});

test('sync requires member capability and explicit confirmation, and reports unknown results', async () => {
  let requests = 0;
  server.use(http.get('/v1/org-connections/capabilities', () => HttpResponse.json({ canManageSettings: true, canManageMembers: true })),
    http.get('/v1/org-connections', () => HttpResponse.json([connection])),
    http.post('/v1/org-connections/:id/sync', () => { requests++; return HttpResponse.json({ checked: 3, revoked: 1, unknown: 2 }); }));
  renderPage();
  await userEvent.click(await screen.findByRole('button', { name: '同步组织' }));
  expect(requests).toBe(0);
  expect(screen.getByText(/可能撤销已离职成员的权限/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '确认操作' }));
  expect(await screen.findByRole('status')).toHaveTextContent('检查 3，撤销 1，无法确认 2');
  expect(requests).toBe(1);
});


test('changing identity never shows the previous tenant connection', async () => {
  let permitted = true;
  server.use(http.get('/v1/org-connections/capabilities', () => HttpResponse.json({ canManageSettings: permitted, canManageMembers: false })),
    http.get('/v1/org-connections', () => HttpResponse.json([connection])));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(createAppRoutes(false), { initialEntries: ['/settings/organization'] });
  const view = (key: string) => <QueryClientProvider client={client}><RouterProvider key={key} router={router} /></QueryClientProvider>;
  const rendered = render(view('a'));
  expect(await screen.findByText('corp-a')).toBeInTheDocument();
  identity.tenant = 'tenant-b'; identity.actor = 'other'; permitted = false;
  rendered.rerender(view('b'));
  expect(await screen.findByRole('alert')).toHaveTextContent('无权管理组织连接');
  expect(screen.queryByText('corp-a')).not.toBeInTheDocument();
});
