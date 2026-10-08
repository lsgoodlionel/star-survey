import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { useState, type ReactNode } from 'react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { expect, test, vi } from 'vitest';
import { z } from 'zod';
import { createAppRoutes } from '../../app/router';
import { createApiClient } from '../../shared/api/http';
import { server } from '../../test/server';
import { AuthProvider, useAuth } from './AuthProvider';

const me = {
  tenantId: 'tenant-a',
  actorId: 'author-7',
  roles: ['editor'],
};

function SessionProbe() {
  const { api, authenticateWithToken, logout, session } = useAuth();
  const [requestFinished, setRequestFinished] = useState(false);

  return (
    <>
      <output aria-label="会话状态">{session?.me.actorId ?? '未登录'}</output>
      <button type="button" onClick={() => void authenticateWithToken('memory-token', 600)}>
        建立会话
      </button>
      <button type="button" onClick={() => void authenticateWithToken('new-token', 600)}>
        建立新会话
      </button>
      <button type="button" onClick={() => void logout()}>
        退出登录
      </button>
      <button
        type="button"
        onClick={() => {
          void api
            .request({ path: '/v1/expired', schema: z.unknown() })
            .catch(() => undefined)
            .finally(() => setRequestFinished(true));
        }}
      >
        请求过期接口
      </button>
      <button
        type="button"
        onClick={() => {
          void Promise.allSettled([
            api.request({ path: '/v1/expired/one', schema: z.unknown() }),
            api.request({ path: '/v1/expired/two', schema: z.unknown() }),
          ]).finally(() => setRequestFinished(true));
        }}
      >
        并发请求过期接口
      </button>
      {requestFinished ? <span>请求结束</span> : null}
    </>
  );
}

function createDeferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function renderAuth(
  children: ReactNode,
  options: { beforeSessionClear?: () => void | Promise<void>; queryClient?: QueryClient } = {},
) {
  const queryClient = options.queryClient ?? new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return {
    queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>
        <AuthProvider beforeSessionClear={options.beforeSessionClear}>{children}</AuthProvider>
      </QueryClientProvider>,
    ),
  };
}

function installMeHandler() {
  server.use(
    http.get('/v1/me', ({ request }) => {
      if (request.headers.get('Authorization') !== 'Bearer memory-token') {
        return HttpResponse.json({ error: 'unauthorized' }, { status: 401 });
      }
      return HttpResponse.json(me);
    }),
  );
}

test('keepsTheJwtOnlyInTheProviderMemory', async () => {
  installMeHandler();
  const persistentWrite = vi.spyOn(Storage.prototype, 'setItem');
  const first = renderAuth(<SessionProbe />);

  fireEvent.click(screen.getByRole('button', { name: '建立会话' }));
  expect(await screen.findByText('author-7')).toBeInTheDocument();
  expect(persistentWrite).not.toHaveBeenCalled();

  first.unmount();
  renderAuth(<SessionProbe />);
  expect(screen.getByLabelText('会话状态')).toHaveTextContent('未登录');
});

test('clearsTheSessionOn401ButPreservesTheEditorRecoveryPayload', async () => {
  installMeHandler();
  server.use(http.get('/v1/expired', () => HttpResponse.json({}, { status: 401 })));
  let recoveryPayload: unknown;

  renderAuth(<SessionProbe />, {
    beforeSessionClear: () => {
        expect(screen.getByLabelText('会话状态')).toHaveTextContent('author-7');
        recoveryPayload = { title: '未保存问卷', questions: 3 };
      },
  });

  fireEvent.click(screen.getByRole('button', { name: '建立会话' }));
  expect(await screen.findByText('author-7')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '请求过期接口' }));

  expect(await screen.findByText('请求结束')).toBeInTheDocument();
  expect(screen.getByLabelText('会话状态')).toHaveTextContent('未登录');
  expect(recoveryPayload).toEqual({ title: '未保存问卷', questions: 3 });
});

test('maps403404409422And202ToChineseDomainStates', async () => {
  server.use(
    http.get('/v1/status/:status', ({ params }) =>
      HttpResponse.json({ traceId: `trace-${params.status}` }, { status: Number(params.status) }),
    ),
  );
  const api = createApiClient();
  const cases = [
    [403, 'forbidden', '无权执行当前操作'],
    [404, 'not_found', '资源不存在或不可访问'],
    [409, 'conflict', '草稿已被其他人修改，本地内容未被覆盖'],
    [422, 'validation', '定义或导入内容未通过校验'],
    [202, 'pending', '发布结果正在核对'],
  ] as const;

  for (const [status, kind, message] of cases) {
    await expect(
      api.request({ path: `/v1/status/${status}`, schema: z.unknown() }),
    ).rejects.toMatchObject({ kind, message, status, traceId: `trace-${status}` });
  }
});

test('ignoresAStale401AfterANewTokenEstablishesTheSession', async () => {
  const staleResponse = createDeferred();
  const beforeSessionClear = vi.fn();
  server.use(
    http.get('/v1/me', ({ request }) => {
      const token = request.headers.get('Authorization');
      return HttpResponse.json({
        tenantId: 'tenant-a',
        actorId: token === 'Bearer new-token' ? 'new-user' : 'old-user',
        roles: ['editor'],
      });
    }),
    http.get('/v1/expired', async () => {
      await staleResponse.promise;
      return new HttpResponse(null, { status: 401 });
    }),
  );
  renderAuth(<SessionProbe />, { beforeSessionClear });

  fireEvent.click(screen.getByRole('button', { name: '建立会话' }));
  expect(await screen.findByText('old-user')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '请求过期接口' }));
  fireEvent.click(screen.getByRole('button', { name: '建立新会话' }));
  expect(await screen.findByText('new-user')).toBeInTheDocument();
  staleResponse.resolve();

  expect(await screen.findByText('请求结束')).toBeInTheDocument();
  expect(screen.getByLabelText('会话状态')).toHaveTextContent('new-user');
  expect(beforeSessionClear).not.toHaveBeenCalled();
});

test('singleFlightsConcurrent401RecoveryAndClearsQueriesBeforeTheSession', async () => {
  installMeHandler();
  server.use(http.get('/v1/expired/:id', () => new HttpResponse(null, { status: 401 })));
  const recovery = createDeferred();
  const beforeSessionClear = vi.fn(() => recovery.promise);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const originalClear = queryClient.clear.bind(queryClient);
  const clear = vi.spyOn(queryClient, 'clear').mockImplementation(() => {
    expect(screen.getByLabelText('会话状态')).toHaveTextContent('author-7');
    originalClear();
  });
  renderAuth(<SessionProbe />, { beforeSessionClear, queryClient });

  fireEvent.click(screen.getByRole('button', { name: '建立会话' }));
  expect(await screen.findByText('author-7')).toBeInTheDocument();
  queryClient.setQueryData(['private-profile'], { owner: 'author-7' });
  fireEvent.click(screen.getByRole('button', { name: '并发请求过期接口' }));
  await waitFor(() => expect(beforeSessionClear).toHaveBeenCalledTimes(1));
  expect(clear).not.toHaveBeenCalled();

  recovery.resolve();
  expect(await screen.findByText('请求结束')).toBeInTheDocument();
  expect(clear).toHaveBeenCalledTimes(1);
  expect(queryClient.getQueryData(['private-profile'])).toBeUndefined();
  expect(screen.getByLabelText('会话状态')).toHaveTextContent('未登录');
});

test('logoutClearsPriorUserQueriesBeforeCrossUserRelogin', async () => {
  server.use(
    http.get('/v1/me', ({ request }) =>
      HttpResponse.json({
        tenantId: 'tenant-a',
        actorId: request.headers.get('Authorization') === 'Bearer new-token' ? 'new-user' : 'old-user',
        roles: ['editor'],
      }),
    ),
    http.post('/v1/auth/logout', () => new HttpResponse(null, { status: 204 })),
  );
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const originalClear = queryClient.clear.bind(queryClient);
  const clear = vi.spyOn(queryClient, 'clear').mockImplementation(() => {
    expect(screen.getByLabelText('会话状态')).toHaveTextContent('old-user');
    originalClear();
  });
  renderAuth(<SessionProbe />, { queryClient });

  fireEvent.click(screen.getByRole('button', { name: '建立会话' }));
  expect(await screen.findByText('old-user')).toBeInTheDocument();
  queryClient.setQueryData(['private-profile'], { owner: 'old-user' });
  fireEvent.click(screen.getByRole('button', { name: '退出登录' }));
  await waitFor(() => expect(screen.getByLabelText('会话状态')).toHaveTextContent('未登录'));
  expect(clear).toHaveBeenCalledTimes(1);

  fireEvent.click(screen.getByRole('button', { name: '建立新会话' }));
  expect(await screen.findByText('new-user')).toBeInTheDocument();
  expect(queryClient.getQueryData(['private-profile'])).toBeUndefined();
});

test('doesNotRenderTheDevTokenEntryInAProductionBuild', async () => {
  const router = createMemoryRouter(createAppRoutes(false), { initialEntries: ['/dev/token'] });
  renderAuth(<RouterProvider router={router} />);

  await waitFor(() => expect(screen.getByRole('heading', { name: '页面不存在' })).toBeInTheDocument());
  expect(screen.queryByLabelText('开发令牌')).not.toBeInTheDocument();
});
