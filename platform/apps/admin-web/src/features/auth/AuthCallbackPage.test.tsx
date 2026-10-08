import { render, screen } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { expect, test } from 'vitest';
import { server } from '../../test/server';
import { AuthCallbackPage } from './AuthCallbackPage';
import { AuthProvider, useAuth } from './AuthProvider';

function SignedInPage() {
  const { session } = useAuth();
  return <h1>{session ? `已登录：${session.me.actorId}` : '未登录'}</h1>;
}

test('exchangesTheHandoffAndLoadsMe', async () => {
  server.use(
    http.post('/v1/auth/org/tenant-a/handoff', async ({ request }) => {
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

  render(
    <AuthProvider>
      <MemoryRouter initialEntries={['/auth/callback?tenant=tenant-a&handoff=one-time-code']}>
        <Routes>
          <Route path="/auth/callback" element={<AuthCallbackPage />} />
          <Route path="/" element={<SignedInPage />} />
        </Routes>
      </MemoryRouter>
    </AuthProvider>,
  );

  expect(await screen.findByRole('heading', { name: '已登录：principal-9' })).toBeInTheDocument();
  expect(screen.queryByText('jwt-from-handoff')).not.toBeInTheDocument();
});
