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
function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><RouterProvider router={createMemoryRouter(createAppRoutes(false), { initialEntries: ['/settings/members'] })} /></QueryClientProvider>);
}
test('manager sees members and explicitly invites a member', async () => {
  let invited = '';
  server.use(http.get('/v1/members', () => HttpResponse.json({ items: [{ actorId: 'owner', status: 'active', usesSeat: true }], nextCursor: null, seatsInUse: 1 })),
    http.post('/v1/members', async ({ request }) => { invited = (await request.json() as { actorId: string }).actorId; return new HttpResponse(null, { status: 201 }); }));
  renderPage();
  expect(await screen.findByRole('cell', { name: 'owner' })).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText('成员标识'), 'member-a');
  await userEvent.click(screen.getByRole('button', { name: '邀请成员' }));
  expect(invited).toBe('member-a');
});
test('forbidden response shows a recoverable denial without member rows', async () => {
  server.use(http.get('/v1/members', () => new HttpResponse(null, { status: 403 })));
  renderPage();
  expect(await screen.findByRole('alert')).toHaveTextContent('无权管理成员');
  expect(screen.queryByRole('table', { name: '成员列表' })).not.toBeInTheDocument();
});

test('manager can assign a tenant role to a member', async () => {
  let grant: unknown;
  server.use(http.get('/v1/members', () => HttpResponse.json({ items: [{ actorId: 'member-a', status: 'active', usesSeat: true }], nextCursor: null, seatsInUse: 1 })),
    http.get('/v1/member-grants', () => HttpResponse.json({ items: [], nextCursor: null })),
    http.get('/v1/member-grants/roles', () => HttpResponse.json([{ code: 'editor', tenantWideOnly: false, maxGrantHours: null, requiresSeat: true, permissions: [] }])),
    http.post('/v1/member-grants', async ({ request }) => { grant = await request.json(); return HttpResponse.json({ id: '10000000-0000-4000-8000-000000000001' }, { status: 201 }); }));
  renderPage();
  await userEvent.selectOptions(await screen.findByLabelText('授权成员'), 'member-a');
  await userEvent.selectOptions(screen.getByLabelText('角色', { exact: true }), 'editor');
  await userEvent.click(screen.getByRole('button', { name: '授予角色' }));
  expect(grant).toEqual({ actorId: 'member-a', roleCode: 'editor', resourceId: null, expiresAt: null });
});


test('same-page refresh after permission removal hides previously loaded members', async () => {
  let allowed = true;
  server.use(http.get('/v1/members', () => allowed
    ? HttpResponse.json({ items: [{ actorId: 'sensitive-member', status: 'active', usesSeat: true }], nextCursor: null, seatsInUse: 1 })
    : new HttpResponse(null, { status: 403 })));
  renderPage();
  expect(await screen.findByRole('cell', { name: 'sensitive-member' })).toBeInTheDocument();
  allowed = false;
  await userEvent.click(screen.getByRole('button', { name: '刷新成员' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('无权管理成员');
  expect(screen.queryByRole('cell', { name: 'sensitive-member' })).not.toBeInTheDocument();
  expect(screen.getByRole('link', { name: '返回工作台' })).toHaveAttribute('href', '/dashboard');
});


test('switching identities does not reveal a previous tenant member list', async () => {
  let currentTenant = 'tenant-a';
  server.use(http.get('/v1/members', () => currentTenant === 'tenant-a'
    ? HttpResponse.json({ items: [{ actorId: 'tenant-a-private', status: 'active', usesSeat: true }], nextCursor: null, seatsInUse: 1 })
    : new HttpResponse(null, { status: 403 })));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(createAppRoutes(false), { initialEntries: ['/settings/members'] });
  const view = (key: string) => <QueryClientProvider client={client}><RouterProvider key={key} router={router} /></QueryClientProvider>;
  const rendered = render(view('a'));
  expect(await screen.findByRole('cell', { name: 'tenant-a-private' })).toBeInTheDocument();
  identity.tenant = 'tenant-b'; identity.actor = 'other'; currentTenant = 'tenant-b';
  rendered.rerender(view('b'));
  expect(await screen.findByRole('alert')).toHaveTextContent('无权管理成员');
  expect(screen.queryByRole('cell', { name: 'tenant-a-private' })).not.toBeInTheDocument();
});
