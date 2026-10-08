import { render, screen } from '@testing-library/react';
import { fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { BrowserRouter, Route, Routes, useNavigate } from 'react-router-dom';
import { expect, test, vi } from 'vitest';
import { server } from '../../test/server';
import { AuthCallbackPage } from './AuthCallbackPage';
import { AuthProvider, useAuth } from './AuthProvider';

function SignedInPage() {
  const { session } = useAuth();
  return <h1>{session ? `已登录：${session.me.actorId}` : '未登录'}</h1>;
}

function LeaveCallbackButton() {
  const navigate = useNavigate();
  return <button onClick={() => navigate('/away')}>离开回调</button>;
}

function renderCallback() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrowserRouter>
          <LeaveCallbackButton />
          <Routes>
            <Route path="/auth/callback" element={<AuthCallbackPage />} />
            <Route path="/" element={<SignedInPage />} />
            <Route path="/away" element={<SignedInPage />} />
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

test('exchangesTheHandoffAndLoadsMe', async () => {
  window.history.replaceState({}, '', '/auth/callback?tenant=tenant-a&handoff=one-time-code');
  server.use(
    http.post('/v1/auth/org/tenant-a/handoff', async ({ request }) => {
      expect(window.location.pathname).toBe('/auth/callback');
      expect(window.location.search).toBe('');
      expect(request.credentials).toBe('same-origin');
      expect(await request.json()).toEqual({ handoff: 'one-time-code' });
      return HttpResponse.json({
        accessToken: 'jwt-from-handoff',
        tokenType: 'Bearer',
        expiresIn: 600,
        principalId: 'principal-9',
        tenantId: 'tenant-a',
      });
    }),
    http.get('/v1/me', ({ request }) => {
      expect(request.headers.get('Authorization')).toBe('Bearer jwt-from-handoff');
      return HttpResponse.json({
        tenantId: 'tenant-a',
        actorId: 'principal-9',
        roles: ['editor'],
      });
    }),
  );

  renderCallback();

  expect(await screen.findByRole('heading', { name: '已登录：principal-9' })).toBeInTheDocument();
  expect(screen.queryByText('jwt-from-handoff')).not.toBeInTheDocument();
});

test('removesAnInvalidHandoffFromTheUrlBeforeShowingTheError', async () => {
  window.history.replaceState({}, '', '/auth/callback?tenant=tenant-a&handoff=');
  const request = vi.fn();
  server.use(http.post('/v1/auth/org/:tenant/handoff', request));

  renderCallback();

  expect(await screen.findByRole('alert')).toHaveTextContent('登录回调无效，请重新登录');
  expect(window.location.pathname).toBe('/auth/callback');
  expect(window.location.search).toBe('');
  expect(request).not.toHaveBeenCalled();
});

test('abortsMeAndDoesNotEstablishASessionAfterCallbackUnmount', async () => {
  window.history.replaceState({}, '', '/auth/callback?tenant=tenant-a&handoff=one-time-code');
  let meSignal: AbortSignal | undefined;
  server.use(
    http.post('/v1/auth/org/tenant-a/handoff', () =>
      HttpResponse.json({
        accessToken: 'jwt-from-handoff',
        tokenType: 'Bearer',
        expiresIn: 600,
        principalId: 'principal-9',
        tenantId: 'tenant-a',
      }),
    ),
    http.get('/v1/me', async ({ request }) => {
      meSignal = request.signal;
      await new Promise<void>((resolve) => {
        request.signal.addEventListener('abort', () => resolve(), { once: true });
      });
      return HttpResponse.json({ tenantId: 'tenant-a', actorId: 'late-user', roles: ['editor'] });
    }),
  );
  renderCallback();

  await waitFor(() => expect(meSignal).toBeDefined());
  fireEvent.click(screen.getByRole('button', { name: '离开回调' }));

  await waitFor(() => expect(meSignal?.aborted).toBe(true));
  expect(screen.getByRole('heading', { name: '未登录' })).toBeInTheDocument();
  expect(window.location.pathname).toBe('/away');
});
