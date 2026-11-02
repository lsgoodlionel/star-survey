import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { expect, test, vi } from 'vitest';
import { server } from '../../test/server';
import { SubscriptionPage } from './SubscriptionPage';

vi.mock('../auth/AuthProvider', async () => {
  const { createApiClient } = await import('../../shared/api/http');
  return { useAuth: () => ({ api: createApiClient(), session: { me: { tenantId: 't', actorId: 'a' } } }) };
});
function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><SubscriptionPage /></QueryClientProvider>);
  return client;
}

test('shows the actual subscription version and quotas', async () => {
  server.use(http.get('/v1/subscription', () => HttpResponse.json({ planVersionId: '10000000-0000-4000-8000-000000000001', planCode: 'standard', planVersion: 2, status: 'TRIAL', startsAt: '2026-11-02T00:00:00Z', endsAt: '2026-12-02T00:00:00Z', capabilities: ['survey.read'], quotas: { 'member.seats': 5 }, exportWindowDays: 30 })));
  renderPage();
  expect(await screen.findByText('standard')).toBeInTheDocument();
  expect(screen.getByText('试用')).toBeInTheDocument();
  expect(screen.getByText('成员席位')).toBeInTheDocument();
  expect(screen.getByText('5')).toBeInTheDocument();
});

test('removes previously displayed data when a same-page permission check is denied', async () => {
  let allowed = true;
  server.use(http.get('/v1/subscription', () => allowed ? HttpResponse.json({ planVersionId: '10000000-0000-4000-8000-000000000001', planCode: 'standard', planVersion: 2, status: 'ACTIVE', startsAt: '2026-11-02T00:00:00Z', endsAt: '2026-12-02T00:00:00Z', capabilities: [], quotas: {}, exportWindowDays: 30 }) : new HttpResponse(null, { status: 403 })));
  const client = renderPage();
  expect(await screen.findByText('standard')).toBeInTheDocument();
  allowed = false;
  await client.invalidateQueries({ queryKey: ['subscription'] });
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('无法查看订阅'));
  expect(screen.queryByText('standard')).not.toBeInTheDocument();
});

test('does not expose subscription data when access is denied', async () => {
  server.use(http.get('/v1/subscription', () => new HttpResponse(null, { status: 403 })));
  renderPage();
  expect(await screen.findByRole('alert')).toHaveTextContent('无法查看订阅');
  expect(screen.queryByText('standard')).not.toBeInTheDocument();
});
