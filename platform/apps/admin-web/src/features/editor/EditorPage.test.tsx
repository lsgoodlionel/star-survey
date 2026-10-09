import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { useState } from 'react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { z } from 'zod';
import { AppProviders } from '../../app/App';
import { AuthProvider, useAuth } from '../auth/AuthProvider';
import { ApiError } from '../../shared/api/errors';
import type { ApiClient, ApiRequest } from '../../shared/api/http';
import { surveyDetailQueryKey, surveyDraftQueryKey } from '../../shared/api/surveys';
import gatewayFixture from '../../test/fixtures/publish-gateway.json';
import { server } from '../../test/server';
import { ImportPage } from '../import/ImportPage';
import { EditorPage } from './EditorPage';
import { captureEditorRecovery } from './recovery';

const surveyId = '11111111-1111-4111-8111-111111111111';
const otherSurveyId = '22222222-2222-4222-8222-222222222222';
const singleUuid = '33333333-0001-4111-8111-000000000001';
const textUuid = '33333333-0002-4111-8111-000000000002';
const noteUuid = '33333333-0003-4111-8111-000000000003';

const overview = {
  id: surveyId,
  title: gatewayFixture.title,
  status: 'draft',
  draftVersion: 4,
  publishedVersion: null,
  lastPublish: null,
};

const editableCapabilities = {
  canCreateProject: false,
  canCreateChildren: false,
  canEdit: true,
  canSubmitApproval: true,
  canPublishDirectly: false,
  canApprovePublish: false,
  canArchive: true,
  canRestore: false,
};

type RequestHandler = (request: ApiRequest<unknown>) => unknown | Promise<unknown>;

function apiFrom(handler: RequestHandler): ApiClient {
  return {
    request: (request) => Promise.resolve(handler(request as ApiRequest<unknown>)) as never,
  };
}

function editorApi(overrides: Partial<Record<string, RequestHandler>> = {}) {
  return apiFrom((request) => {
    const key = `${request.method ?? 'GET'} ${request.path}`;
    const override = overrides[key];
    if (override) return override(request);
    if (key === `GET /v1/surveys/${surveyId}`) return overview;
    if (key === `GET /v1/surveys/${surveyId}/draft`) {
      return { surveyId, version: 4, definition: structuredClone(gatewayFixture) };
    }
    if (key === `GET /v1/resource-capabilities?resourceId=${surveyId}`) {
      return editableCapabilities;
    }
    throw new Error(`Unhandled request: ${key}`);
  });
}

function renderEditor(
  api: ApiClient = editorApi(),
  initialEntry = `/surveys/${surveyId}/edit`,
) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const router = createMemoryRouter(
    [
      {
        path: '/surveys/:surveyId/edit',
        element: <EditorPage api={api} surveyId={surveyId} tenantId="tenant-a" />,
      },
      { path: '/workspace', element: <p>工作区</p> },
      { path: '/login', element: <p>登录页</p> },
      { path: '/dev/token', element: <p>开发登录页</p> },
    ],
    { initialEntries: [initialEntry] },
  );
  return {
    queryClient,
    router,
    ...render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    ),
  };
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('EditorPage', () => {
  test('linksTheEditorToolbarToTheAuthoringWorkflow', async () => {
    renderEditor();

    await screen.findByRole('heading', { name: gatewayFixture.title });
    expect(screen.getByRole('link', { name: '批量导入' })).toHaveAttribute(
      'href',
      `/surveys/${surveyId}/import`,
    );
    expect(screen.getByRole('link', { name: '草稿预览' })).toHaveAttribute(
      'href',
      `/surveys/${surveyId}/preview`,
    );
    expect(screen.getByRole('link', { name: '发布管理' })).toHaveAttribute(
      'href',
      `/surveys/${surveyId}/publish`,
    );
  });

  test('doesNotConsumeAFreshDraftCachedForAnotherTenant', async () => {
    function tenantApi(tenantName: string, questionText: string) {
      return apiFrom((request) => {
        const key = `${request.method ?? 'GET'} ${request.path}`;
        if (key === `GET /v1/surveys/${surveyId}`) {
          return { ...overview, title: `${tenantName} 问卷` };
        }
        if (key === `GET /v1/surveys/${surveyId}/draft`) {
          const definition = structuredClone(gatewayFixture);
          definition.groups[0].questions[0].text = questionText;
          return { surveyId, version: 4, definition };
        }
        if (key === `GET /v1/resource-capabilities?resourceId=${surveyId}`) {
          return editableCapabilities;
        }
        throw new Error(`Unhandled request: ${key}`);
      });
    }
    const apiA = tenantApi('租户 A', '租户 A 题目');
    const apiB = tenantApi('租户 B', '租户 B 题目');
    function TenantEditorHarness() {
      const [tenantId, setTenantId] = useState('tenant-a');
      return (
        <>
          <button type="button" onClick={() => setTenantId('tenant-b')}>切换到租户 B</button>
          <EditorPage
            api={tenantId === 'tenant-a' ? apiA : apiB}
            surveyId={surveyId}
            tenantId={tenantId}
          />
        </>
      );
    }
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false, staleTime: 30_000 },
        mutations: { retry: false },
      },
    });
    const router = createMemoryRouter(
      [{ path: '*', element: <TenantEditorHarness /> }],
      { initialEntries: [`/surveys/${surveyId}/edit`] },
    );
    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );

    expect(await screen.findByRole('heading', { name: '租户 A 问卷' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '切换到租户 B' }));

    expect(await screen.findByRole('heading', { name: '租户 B 问卷' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /租户 B 题目/ })).toBeInTheDocument();
    expect(screen.queryByText('租户 A 问卷')).not.toBeInTheDocument();
  });

  test('keepsTheSelectedQuestionAcrossRouteChanges', async () => {
    const { router } = renderEditor(
      editorApi(),
      `/surveys/${surveyId}/edit?question=${textUuid}`,
    );

    expect(await screen.findByLabelText('题目文本')).toHaveValue('Say something short');
    expect(screen.getByRole('button', { name: 'QTEXT Say something short' })).toHaveAttribute(
      'aria-current',
      'true',
    );

    fireEvent.click(
      screen.getByRole('button', {
        name: 'QNOTE This block of text has no answer column at all.',
      }),
    );

    await waitFor(() => expect(router.state.location.search).toBe(`?question=${noteUuid}`));
    expect(screen.getByLabelText('题目文本')).toHaveValue(
      'This block of text has no answer column at all.',
    );
  });

  test('allowsDirtyQuestionSelectionWithoutConfirmingOrLosingData', async () => {
    const { router } = renderEditor(
      editorApi(),
      `/surveys/${surveyId}/edit?question=${singleUuid}`,
    );
    fireEvent.change(await screen.findByLabelText('题目文本'), {
      target: { value: '切题后仍要保留' },
    });

    fireEvent.click(screen.getByRole('button', { name: 'QTEXT Say something short' }));
    await waitFor(() => expect(router.state.location.search).toBe(`?question=${textUuid}`));
    expect(screen.queryByRole('dialog', { name: '未保存的修改' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'QSINGLE 切题后仍要保留' }));
    await waitFor(() => expect(router.state.location.search).toBe(`?question=${singleUuid}`));
    expect(screen.getByLabelText('题目文本')).toHaveValue('切题后仍要保留');

    void router.navigate(
      `/surveys/${surveyId}/edit?question=${singleUuid}&mode=unrelated`,
    );
    expect(await screen.findByRole('dialog', { name: '未保存的修改' })).toBeInTheDocument();
  });

  test('warnsBeforeLeavingWithUnsavedChanges', async () => {
    const { router } = renderEditor(
      editorApi(),
      `/surveys/${surveyId}/edit?question=${singleUuid}`,
    );
    const text = await screen.findByLabelText('题目文本');
    fireEvent.change(text, { target: { value: '尚未保存的题目' } });

    const event = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(true);

    void router.navigate('/workspace');
    expect(await screen.findByRole('dialog', { name: '未保存的修改' })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe(`/surveys/${surveyId}/edit`);

    fireEvent.click(screen.getByRole('button', { name: '放弃修改并离开' }));
    expect(await screen.findByText('工作区')).toBeInTheDocument();
  });

  test('savesWithTheCurrentDraftVersionAndAdoptsTheReturnedVersion', async () => {
    const writes: Array<Record<string, unknown>> = [];
    const api = editorApi({
      [`PUT /v1/surveys/${surveyId}/draft`]: (request) => {
        const body = request.body as Record<string, unknown>;
        writes.push(body);
        return {
          surveyId,
          version: Number(body.expectedVersion) + 1,
          definition: body.definition,
        };
      },
    });
    renderEditor(api, `/surveys/${surveyId}/edit?question=${singleUuid}`);

    const text = await screen.findByLabelText('题目文本');
    fireEvent.change(text, { target: { value: '第一次修改' } });
    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));
    expect(await screen.findByText('已保存版本 5')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('题目文本'), { target: { value: '第二次修改' } });
    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));
    expect(await screen.findByText('已保存版本 6')).toBeInTheDocument();

    expect(writes.map((body) => body.expectedVersion)).toEqual([4, 5]);
  });

  test('usesTheSavedVersionWhenImportMountsAgainstTheProductionFreshCache', async () => {
    let importRequest: ApiRequest<unknown> | undefined;
    let savedDefinition: unknown = structuredClone(gatewayFixture);
    const api = apiFrom((request) => {
      const key = `${request.method ?? 'GET'} ${request.path}`;
      if (key === `GET /v1/surveys/${surveyId}`) return { ...overview, draftVersion: 1 };
      if (key === `GET /v1/surveys/${surveyId}/draft`) {
        return { surveyId, version: 1, definition: structuredClone(gatewayFixture) };
      }
      if (key === `GET /v1/resource-capabilities?resourceId=${surveyId}`) {
        return editableCapabilities;
      }
      if (key === `PUT /v1/surveys/${surveyId}/draft`) {
        savedDefinition = (request.body as Record<string, unknown>).definition;
        return { surveyId, version: 2, definition: savedDefinition };
      }
      if (key === `POST /v1/surveys/${surveyId}/import/preview`) {
        return {
          lineCount: 1,
          questions: [{
            index: 0,
            line: 1,
            code: 'Q1',
            type: 'S',
            typeName: '填空',
            typeInferred: false,
            text: '跨页导入题',
            mandatory: false,
            options: [],
            importable: true,
            problems: [],
          }],
          problems: [],
        };
      }
      if (key === `POST /v1/surveys/${surveyId}/import`) {
        importRequest = request;
        return { surveyId, version: 3, definition: savedDefinition };
      }
      throw new Error(`Unhandled request: ${key}`);
    });
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false, staleTime: 30_000 },
        mutations: { retry: false },
      },
    });
    const router = createMemoryRouter(
      [
        {
          path: '/surveys/:surveyId/edit',
          element: <EditorPage api={api} surveyId={surveyId} tenantId="tenant-a" />,
        },
        {
          path: '/surveys/:surveyId/import',
          element: <ImportPage api={api} surveyId={surveyId} tenantId="tenant-a" />,
        },
      ],
      { initialEntries: [`/surveys/${surveyId}/edit?question=${singleUuid}`] },
    );
    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );

    fireEvent.change(await screen.findByLabelText('题目文本'), {
      target: { value: '保存后进入导入' },
    });
    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));
    expect(await screen.findByText('已保存版本 2')).toBeInTheDocument();
    expect(queryClient.getQueryData(surveyDetailQueryKey('tenant-a', surveyId))).toMatchObject({
      draftVersion: 2,
      title: overview.title,
    });

    fireEvent.click(screen.getByRole('link', { name: '批量导入' }));
    expect(await screen.findByText('当前草稿版本 2')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('待导入文本'), { target: { value: '1. 跨页导入题[填空]' } });
    fireEvent.click(screen.getByRole('button', { name: '预览导入' }));
    await screen.findByText('跨页导入题');
    fireEvent.click(screen.getByRole('button', { name: '确认导入 1 道题' }));

    await waitFor(() => expect(importRequest).toBeDefined());
    expect(importRequest?.body).toMatchObject({ expectedVersion: 2 });
  });

  test('keepsNewerLocalEditsWhenAnEarlierSaveResponseArrives', async () => {
    const firstSave = createDeferred<{
      surveyId: string;
      version: number;
      definition: unknown;
    }>();
    const writes: Array<Record<string, unknown>> = [];
    const api = editorApi({
      [`PUT /v1/surveys/${surveyId}/draft`]: (request) => {
        const body = request.body as Record<string, unknown>;
        writes.push(body);
        if (writes.length === 1) return firstSave.promise;
        return { surveyId, version: 6, definition: body.definition };
      },
    });
    const rendered = renderEditor(api, `/surveys/${surveyId}/edit?question=${singleUuid}`);

    fireEvent.change(await screen.findByLabelText('题目文本'), {
      target: { value: '已发送到版本 5 的文本' },
    });
    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));
    await waitFor(() => expect(writes).toHaveLength(1));
    fireEvent.change(screen.getByLabelText('题目文本'), {
      target: { value: '请求期间产生的更新文本' },
    });

    await act(async () => {
      firstSave.resolve({
        surveyId,
        version: 5,
        definition: writes[0].definition,
      });
      await firstSave.promise;
    });

    expect(await screen.findByText('已保存版本 5')).toBeInTheDocument();
    expect(screen.getByLabelText('题目文本')).toHaveValue('请求期间产生的更新文本');
    expect(screen.getByRole('button', { name: '保存草稿' })).toBeEnabled();
    expect(rendered.queryClient.getQueryData(surveyDraftQueryKey('tenant-a', surveyId))).toEqual({
      surveyId,
      version: 5,
      definition: writes[0].definition,
    });

    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));
    expect(await screen.findByText('已保存版本 6')).toBeInTheDocument();
    expect(writes.map((body) => body.expectedVersion)).toEqual([4, 5]);
    expect(
      (((writes[1].definition as typeof gatewayFixture).groups[0].questions[0]).text),
    ).toBe('请求期间产生的更新文本');
  });

  test('keepsLocalChangesWhenTheServerReturns409', async () => {
    const createObjectUrl = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:local-draft');
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    const api = editorApi({
      [`PUT /v1/surveys/${surveyId}/draft`]: () => {
        throw new ApiError('conflict', '草稿已被其他人修改，本地内容未被覆盖', 409);
      },
    });
    renderEditor(api, `/surveys/${surveyId}/edit?question=${singleUuid}`);
    const text = await screen.findByLabelText('题目文本');
    fireEvent.change(text, { target: { value: '必须保留的本地题目' } });

    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));

    expect(await screen.findByRole('dialog', { name: '草稿版本冲突' })).toBeInTheDocument();
    expect(screen.getByLabelText('题目文本')).toHaveValue('必须保留的本地题目');
    expect(screen.getByRole('button', { name: '重新载入' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '导出本地草稿' })).toBeInTheDocument();
    expect(createObjectUrl).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '导出本地草稿' }));
    expect(createObjectUrl).toHaveBeenCalledTimes(1);
  });

  test('writesAReloadedDraftBackToTheCanonicalCache', async () => {
    let draftReads = 0;
    const reloadedDefinition = structuredClone(gatewayFixture);
    reloadedDefinition.groups[0].questions[0].text = '服务器重新载入的题目';
    const api = editorApi({
      [`GET /v1/surveys/${surveyId}/draft`]: () => {
        draftReads += 1;
        return draftReads === 1
          ? { surveyId, version: 4, definition: structuredClone(gatewayFixture) }
          : { surveyId, version: 9, definition: reloadedDefinition };
      },
      [`PUT /v1/surveys/${surveyId}/draft`]: () => {
        throw new ApiError('conflict', '草稿已被其他人修改，本地内容未被覆盖', 409);
      },
    });
    const rendered = renderEditor(api, `/surveys/${surveyId}/edit?question=${singleUuid}`);

    fireEvent.change(await screen.findByLabelText('题目文本'), {
      target: { value: '触发冲突' },
    });
    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));
    fireEvent.click(await screen.findByRole('button', { name: '重新载入' }));

    expect(await screen.findByLabelText('题目文本')).toHaveValue('服务器重新载入的题目');
    expect(rendered.queryClient.getQueryData(surveyDraftQueryKey('tenant-a', surveyId))).toEqual({
      surveyId,
      version: 9,
      definition: reloadedDefinition,
    });
    expect(rendered.queryClient.getQueryData(surveyDetailQueryKey('tenant-a', surveyId))).toMatchObject({
      draftVersion: 9,
      title: overview.title,
    });
  });

  test('recoversTheInMemoryDraftAfterReauthentication', async () => {
    const persistentWrite = vi.spyOn(Storage.prototype, 'setItem');
    server.use(
      http.get('/v1/me', () =>
        HttpResponse.json({ tenantId: 'tenant-a', actorId: 'author-1', roles: ['editor'] }),
      ),
      http.get(`/v1/surveys/${surveyId}`, () => HttpResponse.json(overview)),
      http.get(`/v1/surveys/${surveyId}/draft`, () =>
        HttpResponse.json({ surveyId, version: 4, definition: gatewayFixture }),
      ),
      http.get('/v1/resource-capabilities', () => HttpResponse.json(editableCapabilities)),
      http.get('/v1/expired', () => new HttpResponse(null, { status: 401 })),
    );
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const router = createMemoryRouter(
      [
        {
          path: '/surveys/:surveyId/edit',
          element: (
            <AuthProvider beforeSessionClear={captureEditorRecovery}>
              <RecoveryHarness />
            </AuthProvider>
          ),
        },
      ],
      { initialEntries: [`/surveys/${surveyId}/edit?question=${singleUuid}`] },
    );
    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: '重新认证' }));
    const text = await screen.findByLabelText('题目文本');
    fireEvent.change(text, { target: { value: '登录失效也不能丢失' } });
    fireEvent.click(screen.getByRole('button', { name: '触发 401' }));
    expect(await screen.findByText('当前未认证')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '重新认证' }));
    expect(await screen.findByLabelText('题目文本')).toHaveValue('登录失效也不能丢失');
    expect(persistentWrite).not.toHaveBeenCalled();
  });

  test('productionCompositionRecoversOnlyTheMatchingTenantAndSurveyAfter401', async () => {
    const persistentWrite = vi.spyOn(Storage.prototype, 'setItem');
    server.use(
      http.get('/v1/me', ({ request }) => {
        const tenantId = request.headers.get('Authorization') === 'Bearer tenant-b-token'
          ? 'tenant-b'
          : 'tenant-a';
        return HttpResponse.json({ tenantId, actorId: `${tenantId}-author`, roles: ['editor'] });
      }),
      http.get(`/v1/surveys/${surveyId}`, () => HttpResponse.json(overview)),
      http.get(`/v1/surveys/${surveyId}/draft`, () =>
        HttpResponse.json({ surveyId, version: 4, definition: gatewayFixture }),
      ),
      http.get(`/v1/surveys/${otherSurveyId}`, () =>
        HttpResponse.json({ ...overview, id: otherSurveyId }),
      ),
      http.get(`/v1/surveys/${otherSurveyId}/draft`, () =>
        HttpResponse.json({ surveyId: otherSurveyId, version: 4, definition: gatewayFixture }),
      ),
      http.get('/v1/resource-capabilities', () => HttpResponse.json(editableCapabilities)),
      http.get('/v1/expired', () => new HttpResponse(null, { status: 401 })),
    );
    const router = createMemoryRouter(
      [{ path: '/surveys/:surveyId/edit', element: <ProductionRecoveryHarness /> }],
      { initialEntries: [`/surveys/${surveyId}/edit?question=${singleUuid}`] },
    );
    render(
      <AppProviders>
        <RouterProvider router={router} />
      </AppProviders>,
    );

    fireEvent.click(screen.getByRole('button', { name: '认证租户 A' }));
    fireEvent.change(await screen.findByLabelText('题目文本'), {
      target: { value: '仅属于租户 A 的本地修改' },
    });
    fireEvent.click(screen.getByRole('button', { name: '触发生产 401' }));
    expect(await screen.findByText('生产组合未认证')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '切换问卷' }));
    fireEvent.click(screen.getByRole('button', { name: '认证租户 A' }));
    expect(await screen.findByLabelText('题目文本')).toHaveValue('Pick exactly one');
    fireEvent.click(screen.getByRole('button', { name: '触发生产 401' }));
    expect(await screen.findByText('生产组合未认证')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '切换问卷' }));
    fireEvent.click(screen.getByRole('button', { name: '认证租户 B' }));
    expect(await screen.findByLabelText('题目文本')).toHaveValue('Pick exactly one');
    fireEvent.click(screen.getByRole('button', { name: '触发生产 401' }));
    expect(await screen.findByText('生产组合未认证')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '认证租户 A' }));
    expect(await screen.findByLabelText('题目文本')).toHaveValue('仅属于租户 A 的本地修改');
    expect(persistentWrite).not.toHaveBeenCalled();
  });

  test('allowsAuthenticationRecoveryNavigationWithUnsavedChanges', async () => {
    const { router } = renderEditor(
      editorApi(),
      `/surveys/${surveyId}/edit?question=${singleUuid}`,
    );
    fireEvent.change(await screen.findByLabelText('题目文本'), {
      target: { value: '等待重新认证的本地修改' },
    });

    void router.navigate('/login');

    expect(await screen.findByText('登录页')).toBeInTheDocument();
    expect(screen.queryByRole('dialog', { name: '未保存的修改' })).not.toBeInTheDocument();
  });

  test('switchesOutlineEditorAndPropertiesAsTabsOnNarrowScreens', async () => {
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: query === '(max-width: 900px)',
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));
    renderEditor(editorApi(), `/surveys/${surveyId}/edit?question=${singleUuid}`);

    expect(await screen.findByRole('tablist', { name: '编辑区域' })).toBeInTheDocument();
    expect(screen.getByRole('tabpanel', { name: '大纲' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: '编辑' }));
    expect(screen.getByRole('tabpanel', { name: '编辑' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: '属性' }));
    expect(screen.getByRole('tabpanel', { name: '属性' })).toBeInTheDocument();
  });

  test('gatesEditingAndSavingWithAuthoritativeResourceCapabilities', async () => {
    const api = editorApi({
      [`GET /v1/resource-capabilities?resourceId=${surveyId}`]: () => ({
        ...editableCapabilities,
        canEdit: false,
      }),
    });
    renderEditor(api, `/surveys/${surveyId}/edit?question=${singleUuid}`);

    expect(await screen.findByLabelText('题目文本')).toBeDisabled();
    expect(screen.getByRole('button', { name: '保存草稿' })).toBeDisabled();
    expect(screen.getByText('当前账号仅可查看此问卷')).toBeInTheDocument();
  });
});

function RecoveryHarness() {
  const { api, authenticateWithToken, session } = useAuth();
  const [requestDone, setRequestDone] = useState(false);
  return (
    <>
      <button type="button" onClick={() => void authenticateWithToken('memory-token', 600)}>
        重新认证
      </button>
      <button
        type="button"
        onClick={() => {
          void api
            .request({ path: '/v1/expired', schema: z.unknown() })
            .catch(() => undefined)
            .finally(() => setRequestDone(true));
        }}
      >
        触发 401
      </button>
      {requestDone ? <span>请求结束</span> : null}
      {session ? (
        <EditorPage api={api} surveyId={surveyId} tenantId={session.me.tenantId} />
      ) : (
        <p>当前未认证</p>
      )}
    </>
  );
}

function ProductionRecoveryHarness() {
  const { api, authenticateWithToken, session } = useAuth();
  const [selectedSurveyId, setSelectedSurveyId] = useState(surveyId);
  return (
    <>
      <button type="button" onClick={() => void authenticateWithToken('tenant-a-token', 600)}>
        认证租户 A
      </button>
      <button type="button" onClick={() => void authenticateWithToken('tenant-b-token', 600)}>
        认证租户 B
      </button>
      <button
        type="button"
        onClick={() => setSelectedSurveyId((current) =>
          current === surveyId ? otherSurveyId : surveyId)}
      >
        切换问卷
      </button>
      <button
        type="button"
        onClick={() => void api.request({ path: '/v1/expired', schema: z.unknown() }).catch(() => undefined)}
      >
        触发生产 401
      </button>
      {session ? (
        <EditorPage api={api} surveyId={selectedSurveyId} tenantId={session.me.tenantId} />
      ) : (
        <p>生产组合未认证</p>
      )}
    </>
  );
}

function createDeferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
