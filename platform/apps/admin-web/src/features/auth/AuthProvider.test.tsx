import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { useState } from 'react';
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
  const { api, authenticateWithToken, session } = useAuth();
  const [requestFinished, setRequestFinished] = useState(false);

  return (
    <>
      <output aria-label="会话状态">{session?.me.actorId ?? '未登录'}</output>
      <button type="button" onClick={() => void authenticateWithToken('memory-token', 600)}>
        建立会话
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
      {requestFinished ? <span>请求结束</span> : null}
    </>
  );
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
  const first = render(
    <AuthProvider>
      <SessionProbe />
    </AuthProvider>,
  );

  fireEvent.click(screen.getByRole('button', { name: '建立会话' }));
  expect(await screen.findByText('author-7')).toBeInTheDocument();
  expect(persistentWrite).not.toHaveBeenCalled();

  first.unmount();
  render(
    <AuthProvider>
      <SessionProbe />
    </AuthProvider>,
  );
  expect(screen.getByLabelText('会话状态')).toHaveTextContent('未登录');
});

test('clearsTheSessionOn401ButPreservesTheEditorRecoveryPayload', async () => {
  installMeHandler();
  server.use(http.get('/v1/expired', () => HttpResponse.json({}, { status: 401 })));
  let recoveryPayload: unknown;

  render(
    <AuthProvider
      beforeSessionClear={() => {
        expect(screen.getByLabelText('会话状态')).toHaveTextContent('author-7');
        recoveryPayload = { title: '未保存问卷', questions: 3 };
      }}
    >
      <SessionProbe />
    </AuthProvider>,
  );

  fireEvent.click(screen.getByRole('button', { name: '建立会话' }));
  expect(await screen.findByText('author-7')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '请求过期接口' }));

  expect(await screen.findByText('请求结束')).toBeInTheDocument();
  expect(screen.getByLabelText('会话状态')).toHaveTextContent('未登录');
  expect(recoveryPayload).toEqual({ title: '未保存问卷', questions: 3 });
});

test('maps403404409422And202ToChineseDomainStates', async () => {
  server.use(
    http.get('/status/:status', ({ params }) =>
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
      api.request({ path: `/status/${status}`, schema: z.unknown() }),
    ).rejects.toMatchObject({ kind, message, status, traceId: `trace-${status}` });
  }
});

test('doesNotRenderTheDevTokenEntryInAProductionBuild', async () => {
  const router = createMemoryRouter(createAppRoutes(false), { initialEntries: ['/dev/token'] });
  render(
    <AuthProvider>
      <RouterProvider router={router} />
    </AuthProvider>,
  );

  await waitFor(() => expect(screen.getByRole('heading', { name: '页面不存在' })).toBeInTheDocument());
  expect(screen.queryByLabelText('开发令牌')).not.toBeInTheDocument();
});
