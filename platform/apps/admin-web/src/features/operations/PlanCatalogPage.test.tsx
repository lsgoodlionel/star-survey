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
  capabilities: ['survey.read'], quotas: { 'member.seats': '5', 'response.valid_completed': '9223372036854775807' }, exportWindowDays: 30, publishedAt: '2026-11-02T00:00:00Z' };
function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><RouterProvider router={createMemoryRouter(createAppRoutes(false), { initialEntries: ['/operations/plans'] })} /></QueryClientProvider>);
  return client;
}
beforeEach(() => {
  auth.allowed = true;
  server.use(http.get('/v1/platform-admin/capabilities', () => HttpResponse.json({ managePlatform: auth.allowed })),
    http.get('/v1/platform/plan-catalog/definitions', () => HttpResponse.json({ capabilities: [{ code: 'survey.read', description: '查看问卷' }], meters: [{ code: 'member.seats', description: '成员席位' }] })));
});

test('shows the latest immutable version and its actual capabilities and limits', async () => {
  server.use(http.get('/v1/platform/plan-catalog', () => HttpResponse.json({ items: [plan], nextCursor: null })));
  renderPage();
  expect(await screen.findByRole('heading', { name: '套餐目录' })).toBeInTheDocument();
  expect(await screen.findByText('standard')).toBeInTheDocument();
  expect(screen.getByText('survey.read')).toBeInTheDocument();
  expect(screen.getByText('member.seats: 5')).toBeInTheDocument();
  expect(screen.getByText('response.valid_completed: 9223372036854775807')).toBeInTheDocument();
  expect(screen.getByText('30 天')).toBeInTheDocument();
});

test('follows server cursor and hides old data when denied', async () => {
  server.use(http.get('/v1/platform/plan-catalog', ({ request }) => new URL(request.url).searchParams.get('after')
    ? new HttpResponse(null, { status: 403 }) : HttpResponse.json({ items: [plan], nextCursor: 'standard' })));
  renderPage();
  await userEvent.click(await screen.findByRole('button', { name: '下一页' }));
  expect(await screen.findByRole('heading', { name: '需要平台运营权限' })).toBeInTheDocument();
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

test('browses immutable versions and confirms exact quota strings when publishing', async () => {
  let published: unknown;
  server.use(http.get('/v1/platform/plan-catalog', () => HttpResponse.json({ items: [plan], nextCursor: null })),
    http.get('/v1/platform/plan-catalog/:code/versions', () => HttpResponse.json({ items: [plan], nextCursor: null })),
    http.post('/v1/platform/plans', async ({ request }) => { published = await request.json(); return HttpResponse.json({ id: plan.id, planCode: plan.planCode, version: 3 }, { status: 201 }); }));
  renderPage();
  await userEvent.click(await screen.findByRole('button', { name: '查看 standard 版本' }));
  expect(await screen.findByRole('heading', { name: 'standard 版本历史' })).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: '发布 standard 新版本' }));
  await userEvent.clear(screen.getByLabelText('额度 JSON'));
  await userEvent.click(screen.getByLabelText('额度 JSON'));
  await userEvent.paste('{"member.seats":"9223372036854775807"}');
  await userEvent.click(screen.getByRole('button', { name: '检查发布内容' }));
  expect(published).toBeUndefined();
  await userEvent.click(screen.getByRole('button', { name: '确认发布版本' }));
  expect(await screen.findByText('套餐版本已发布')).toBeInTheDocument();
  expect(published).toMatchObject({ planCode: 'standard', expectedVersion: 2, quotas: { 'member.seats': '9223372036854775807' } });
});

test('opens a selected version for a provisioning tenant only after confirmation', async () => {
  let body: unknown;
  server.use(http.get('/v1/platform/plan-catalog', () => HttpResponse.json({ items: [plan], nextCursor: null })),
    http.get('/v1/platform/plan-catalog/:code/versions', () => HttpResponse.json({ items: [plan], nextCursor: null })),
    http.post('/v1/platform/tenants/:tenantId/onboarding', async ({ request }) => { body = await request.json(); return HttpResponse.json({ tenantId: '20000000-0000-4000-8000-000000000001', ownerActorId: 'owner-1' }); }));
  renderPage();
  await userEvent.click(await screen.findByRole('button', { name: '查看 standard 版本' }));
  await userEvent.click(await screen.findByRole('button', { name: '选择版本 2 开通租户' }));
  await userEvent.type(screen.getByLabelText('租户 ID'), '20000000-0000-4000-8000-000000000001');
  await userEvent.type(screen.getByLabelText('所有者标识'), 'owner-1');
  await userEvent.click(screen.getByRole('button', { name: '检查开通内容' }));
  expect(body).toBeUndefined();
  await userEvent.click(screen.getByRole('button', { name: '确认开通租户' }));
  expect(await screen.findByText('租户订阅与所有者已开通')).toBeInTheDocument();
  expect(body).toMatchObject({ planVersionId: plan.id, ownerActorId: 'owner-1', kind: 'TRIAL', days: 30 });
});

test('publication conflict clears the stale editor and requires fresh review', async () => {
  let calls = 0;
  server.use(http.get('/v1/platform/plan-catalog', () => HttpResponse.json({ items: [plan], nextCursor: null })),
    http.post('/v1/platform/plans', () => { calls++; return new HttpResponse(null, { status: 409 }); }));
  renderPage();
  await userEvent.click(await screen.findByRole('button', { name: '发布 standard 新版本' }));
  await userEvent.click(screen.getByRole('button', { name: '检查发布内容' }));
  await userEvent.click(screen.getByRole('button', { name: '确认发布版本' }));
  expect(await screen.findByText('数据已变化，请刷新后核对')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '确认发布版本' })).not.toBeInTheDocument();
  expect(calls).toBe(1);
});

test('rejects numeric or out-of-range quota input without a write', async () => {
  let calls = 0;
  server.use(http.get('/v1/platform/plan-catalog', () => HttpResponse.json({ items: [plan], nextCursor: null })),
    http.post('/v1/platform/plans', () => { calls++; return new HttpResponse(null, { status: 201 }); }));
  renderPage();
  await userEvent.click(await screen.findByRole('button', { name: '发布 standard 新版本' }));
  const field = screen.getByLabelText('额度 JSON');
  for (const input of ['{"member.seats":9007199254740993}', '{"member.seats":"9223372036854775808"}']) {
    await userEvent.clear(field); await userEvent.click(field); await userEvent.paste(input);
    await userEvent.click(screen.getByRole('button', { name: '检查发布内容' }));
    expect(screen.getByText('请检查套餐编码、能力、额度字符串和导出窗口')).toBeInTheDocument();
  }
  expect(calls).toBe(0);
});

test('seat failure reports no onboarding and closes the stale confirmation', async () => {
  let calls = 0;
  server.use(http.get('/v1/platform/plan-catalog', () => HttpResponse.json({ items: [plan], nextCursor: null })),
    http.get('/v1/platform/plan-catalog/:code/versions', () => HttpResponse.json({ items: [plan], nextCursor: null })),
    http.post('/v1/platform/tenants/:tenantId/onboarding', () => { calls++; return new HttpResponse(null, { status: 422 }); }));
  renderPage();
  await userEvent.click(await screen.findByRole('button', { name: '查看 standard 版本' }));
  await userEvent.click(await screen.findByRole('button', { name: '选择版本 2 开通租户' }));
  await userEvent.type(screen.getByLabelText('租户 ID'), '20000000-0000-4000-8000-000000000001');
  await userEvent.type(screen.getByLabelText('所有者标识'), 'owner-1');
  await userEvent.click(screen.getByRole('button', { name: '检查开通内容' }));
  await userEvent.click(screen.getByRole('button', { name: '确认开通租户' }));
  expect(await screen.findByText('套餐席位不足或开通条件不满足，未开通')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '确认开通租户' })).not.toBeInTheDocument();
  expect(calls).toBe(1);
});
