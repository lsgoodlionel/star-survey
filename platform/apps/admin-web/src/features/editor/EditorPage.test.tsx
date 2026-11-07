import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { useState } from 'react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { z } from 'zod';
import { AppProviders } from '../../app/App';
import { AuthProvider, useAuth } from '../auth/AuthProvider';
import { approvalRequestsQueryKey, surveyOverviewQueryKey } from '../../shared/api/approvals';
import { ApiError } from '../../shared/api/errors';
import type { ApiClient, ApiRequest } from '../../shared/api/http';
import { resourceQueryKey } from '../../shared/api/resources';
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

function seedPublishCaches(client: QueryClient) {
  const keys = [surveyOverviewQueryKey, approvalRequestsQueryKey].flatMap((key) => [key('tenant-a', surveyId), key('tenant-b', surveyId), key('tenant-a', otherSurveyId)]);
  keys.forEach((key) => client.setQueryData(key, []));
  return () => keys.forEach((key) => expect(client.getQueryState(key)?.isInvalidated).toBe(key[1] === 'tenant-a' && key[2] === surveyId));
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('EditorPage', () => {
  test.each(['switch', 'remove_before', 'remove_target', 'save_during_upload'] as const)('keeps a delayed carousel upload bound to its original slide: %s', async (scenario) => {
    const source = structuredClone(gatewayFixture);
    const questions = source.groups[0].questions as Array<Record<string, unknown>>;
    questions[0] = { ...questions[0], uuid: singleUuid, code: 'QFIRST', text: '第一道轮播', type: 'L', theme: 'mjy-carousel',
      answers: [{ code: 'A1', text: '甲' }, { code: 'A2', text: '乙' }], themeOptions: { slides: [{ code: 'A1', alt: '甲' }, { code: 'A2', alt: '乙' }], future: '保留' } };
    questions[1] = { ...questions[1], uuid: textUuid, code: 'QSECOND', text: '第二道轮播', type: 'L', theme: 'mjy-carousel',
      answers: [{ code: 'A1', text: '另一项' }], themeOptions: { slides: [{ code: 'A1', alt: '另一项' }] } };
    const upload = createDeferred<{ id: string }>();
    let saved: typeof source | undefined;
    const api = editorApi({
      [`GET /v1/surveys/${surveyId}/draft`]: () => ({ surveyId, version: 4, definition: source }),
      'GET /v1/assets': () => [], 'POST /v1/assets': () => upload.promise,
      [`PUT /v1/surveys/${surveyId}/draft`]: (request) => { const body = request.body as { definition: typeof source; expectedVersion: number }; saved = body.definition; return { surveyId, version: body.expectedVersion + 1, definition: saved }; },
    });
    renderEditor(api, `/surveys/${surveyId}/edit?question=${singleUuid}`);
    const input = await screen.findByLabelText('上传图片 2');
    fireEvent.change(input, { target: { files: [new File(['image'], 'image.png', { type: 'image/png' })] } });
    await screen.findByText('正在上传图片，完成时间视文件大小而定');
    if (scenario === 'switch') {
      fireEvent.click(screen.getByRole('button', { name: 'QSECOND 第二道轮播' }));
      fireEvent.change(screen.getByLabelText('题目文本'), { target: { value: '切换后的修改' } });
    } else if (scenario === 'remove_before') {
      fireEvent.click(screen.getByRole('button', { name: '删除选项 1' }));
      fireEvent.change(screen.getByLabelText('替代文本 1'), { target: { value: '移动后的修改' } });
    } else if (scenario === 'remove_target') {
      fireEvent.click(screen.getByRole('button', { name: '删除选项 2' }));
      fireEvent.click(screen.getByRole('button', { name: '添加选项' }));
    } else {
      fireEvent.change(screen.getByLabelText('题目文本'), { target: { value: '上传时先保存' } });
      fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));
      await screen.findByText('已保存版本 5');
    }
    await act(async () => { upload.resolve({ id: '60000000-0000-4000-8000-000000000001' }); await upload.promise; });
    await waitFor(() => expect(screen.queryByText('正在上传图片，完成时间视文件大小而定')).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));
    await screen.findByText(`已保存版本 ${scenario === 'save_during_upload' ? 6 : 5}`);
    const result = saved!.groups[0].questions as Array<Record<string, unknown>>;
    const first = (result[0].themeOptions as { slides: Array<Record<string, unknown>> }).slides;
    const second = (result[1].themeOptions as { slides: Array<Record<string, unknown>> }).slides;
    expect(second[0].assetId).toBeUndefined();
    if (scenario === 'remove_target') expect(first.find((slide) => slide.code === 'A2')?.assetId).toBe('');
    else expect(first.find((slide) => slide.code === 'A2')?.assetId).toBe('60000000-0000-4000-8000-000000000001');
    if (scenario === 'switch') expect(result[1].text).toBe('切换后的修改');
    if (scenario === 'remove_before') expect(first[0].alt).toBe('移动后的修改');
  });
  test('adds a basic question from the question palette', async () => {
    renderEditor(editorApi(), `/surveys/${surveyId}/edit?question=${singleUuid}`);
    await screen.findByLabelText('题目文本');
    fireEvent.change(screen.getByLabelText('添加题型'), { target: { value: 'S' } });
    fireEvent.click(screen.getByRole('button', { name: '添加题目' }));
    expect(screen.getByLabelText('题目文本')).toHaveValue('短文本题');
    expect(screen.getByRole('status', { name: '保存状态' })).toHaveTextContent('未保存');
  });

  test('adds a collapsible section from the palette', async () => {
    renderEditor(editorApi(), `/surveys/${surveyId}/edit?question=${singleUuid}`);
    await screen.findByLabelText('题目文本');
    fireEvent.change(screen.getByLabelText('添加题型'), { target: { value: 'mjy-collapsible' } });
    fireEvent.click(screen.getByRole('button', { name: '添加题目' }));
    expect(screen.getByLabelText('栏目标题')).toHaveValue('新栏目');
    expect(screen.getByLabelText('默认折叠')).toBeChecked();
  });

  test('adds and configures a cascading select using a published dictionary', async () => {
    const api = editorApi({ 'GET /v1/dictionaries': () => [{ code: 'regions', name: '地区', maxDepth: 1, currentVersion: 'v1' }] });
    renderEditor(api, `/surveys/${surveyId}/edit?question=${singleUuid}`);
    await screen.findByLabelText('题目文本');
    fireEvent.change(screen.getByLabelText('添加题型'), { target: { value: 'mjy-cascading-select' } });
    fireEvent.click(screen.getByRole('button', { name: '添加题目' }));
    await screen.findByRole('option', { name: '地区' });
    fireEvent.change(screen.getByLabelText('字典'), { target: { value: 'regions' } });
    fireEvent.change(screen.getByLabelText('第 1 级标题'), { target: { value: '省' } });
    expect(screen.getByLabelText('字典')).toHaveValue('regions');
    expect(screen.getByRole('button', { name: '添加级别' })).toBeDisabled();
  });
  test('keeps carousel slides aligned when answer codes change and options are added', async () => {
    const assetId = '60000000-0000-4000-8000-000000000001';
    let saved: Record<string, unknown> | undefined;
    const api = editorApi({
      'GET /v1/assets': () => [{ id: assetId, name: '图片', kind: 'image', status: 'active', currentVersion: 1, contentType: 'image/png', byteSize: 1, sha256: 'a'.repeat(64), createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z' }],
      [`PUT /v1/surveys/${surveyId}/draft`]: (request) => { saved = (request.body as { definition: Record<string, unknown> }).definition; return { surveyId, version: 5, definition: saved }; },
    });
    renderEditor(api, `/surveys/${surveyId}/edit?question=${singleUuid}`);
    await screen.findByLabelText('题目文本');
    fireEvent.change(screen.getByLabelText('添加题型'), { target: { value: 'mjy-carousel' } });
    fireEvent.click(screen.getByRole('button', { name: '添加题目' }));
    await screen.findByRole('option', { name: '图片' });
    expect(screen.getByLabelText('允许填写其他答案')).toBeEnabled();
    fireEvent.change(screen.getByLabelText('图片 1'), { target: { value: assetId } });
    fireEvent.change(screen.getByLabelText('替代文本 1'), { target: { value: '示例图片' } });
    fireEvent.change(screen.getByLabelText('选项 1 编码'), { target: { value: 'FIRST' } });
    fireEvent.click(screen.getByRole('button', { name: '添加选项' }));
    fireEvent.click(screen.getByRole('button', { name: '删除选项 2' }));
    expect(screen.queryByLabelText('图片 2')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '添加选项' }));
    fireEvent.change(screen.getByLabelText('图片 2'), { target: { value: assetId } });
    fireEvent.change(screen.getByLabelText('替代文本 2'), { target: { value: '另一张图片' } });
    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));
    await screen.findByText('已保存版本 5');
    const groups = saved!.groups as Array<{ questions: Array<Record<string, unknown>> }>;
    expect((groups[0].questions.at(-1)!.themeOptions as { slides: unknown }).slides).toEqual([{ code: 'FIRST', assetId, alt: '示例图片' }, { code: 'A2', assetId, alt: '另一张图片' }]);
  });
  test('does not rewrite malformed carousel extension data', async () => {
    const source = structuredClone(gatewayFixture);
    const question = source.groups[0].questions[0] as Record<string, unknown>;
    question.theme = 'mjy-carousel';
    question.themeOptions = { slides: [{ code: 'A1', future: 'keep' }, 'unknown-extension'], future: true };
    renderEditor(editorApi({ [`GET /v1/surveys/${surveyId}/draft`]: () => ({ surveyId, version: 4, definition: source }) }), `/surveys/${surveyId}/edit?question=${singleUuid}`);
    expect(await screen.findByRole('alert')).toHaveTextContent('轮播图配置格式无法可视化编辑');
    expect(screen.queryByRole('button', { name: '添加选项' })).not.toBeInTheDocument();
  });
  test('can repair a missing carousel slide without altering other slide metadata', async () => {
    const source = structuredClone(gatewayFixture);
    const question = source.groups[0].questions[0] as Record<string, unknown>;
    question.theme = 'mjy-carousel';
    question.themeOptions = { slides: [], future: 'keep' };
    const api = editorApi({ [`GET /v1/surveys/${surveyId}/draft`]: () => ({ surveyId, version: 4, definition: source }), 'GET /v1/assets': () => [] });
    renderEditor(api, `/surveys/${surveyId}/edit?question=${singleUuid}`);
    fireEvent.click(await screen.findByRole('button', { name: '为选项 A1 添加图片' }));
    expect(screen.getByLabelText('图片 1')).toBeInTheDocument();
  });
  test('retains malformed cascading levels instead of normalizing unknown data', async () => {
    const source = structuredClone(gatewayFixture);
    const question = source.groups[0].questions[1] as Record<string, unknown>;
    question.theme = 'mjy-cascading-select';
    question.themeOptions = { dictionary: 'regions', levels: ['地区', { future: true }] };
    renderEditor(editorApi({ [`GET /v1/surveys/${surveyId}/draft`]: () => ({ surveyId, version: 4, definition: source }) }), `/surveys/${surveyId}/edit?question=${textUuid}`);
    expect(await screen.findByRole('alert')).toHaveTextContent('多级下拉配置格式无法可视化编辑');
    expect(screen.queryByRole('button', { name: '添加级别' })).not.toBeInTheDocument();
  });
  test('shows an unavailable dictionary without clearing its stored reference', async () => {
    const source = structuredClone(gatewayFixture);
    const question = source.groups[0].questions[1] as Record<string, unknown>;
    question.theme = 'mjy-cascading-select';
    question.themeOptions = { dictionary: 'retired', structureVersion: 'r1', levels: ['地区'] };
    const api = editorApi({ [`GET /v1/surveys/${surveyId}/draft`]: () => ({ surveyId, version: 4, definition: source }), 'GET /v1/dictionaries': () => [] });
    renderEditor(api, `/surveys/${surveyId}/edit?question=${textUuid}`);
    expect(await screen.findByRole('option', { name: '当前字典不可用：retired' })).toBeInTheDocument();
    expect(screen.getByLabelText('字典')).toHaveValue('retired');
  });
  test('shows a Chinese question type and current save state', async () => {
    renderEditor(editorApi({ [`GET /v1/surveys/${surveyId}/draft`]: () => ({ surveyId, version: 4, definition: { ...gatewayFixture, language: 'zh-Hans' } }) }), `/surveys/${surveyId}/edit?question=${singleUuid}`);
    expect(await screen.findByText('单选题', { selector: 'p' })).toBeInTheDocument();
    expect(screen.getByRole('status', { name: '保存状态' })).toHaveTextContent('版本 4');
    fireEvent.change(screen.getByLabelText('题目文本'), { target: { value: '未保存修改' } });
    expect(screen.getByRole('status', { name: '保存状态' })).toHaveTextContent('未保存');
    expect(screen.getByText('简体中文')).toBeInTheDocument();
  });
  test('configures a collapsible section while preserving unknown options', async () => {
    const source = structuredClone(gatewayFixture);
    const question = source.groups[0].questions.find((item) => item.uuid === noteUuid)! as Record<string, unknown>;
    question.theme = 'mjy-collapsible';
    question.themeOptions = { summary: '原栏目', collapsed: true, future: { keep: true } };
    let savedDefinition: typeof source | undefined;
    const api = editorApi({
      [`GET /v1/surveys/${surveyId}/draft`]: () => ({ surveyId, version: 4, definition: source }),
      [`PUT /v1/surveys/${surveyId}/draft`]: (request) => {
        savedDefinition = (request.body as { definition: typeof source }).definition;
        return { surveyId, version: 5, definition: savedDefinition };
      },
    });
    renderEditor(api, `/surveys/${surveyId}/edit?question=${noteUuid}`);
    fireEvent.change(await screen.findByLabelText('栏目标题'), { target: { value: '新栏目' } });
    fireEvent.click(screen.getByLabelText('默认折叠'));
    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));
    await screen.findByText('已保存版本 5');
    const saved = savedDefinition!.groups[0].questions.find((item) => item.uuid === noteUuid)! as Record<string, unknown>;
    expect(saved.themeOptions).toEqual({ summary: '新栏目', collapsed: false, future: { keep: true } });
  });
  test('linksTheEditorToolbarToTheAuthoringWorkflow', async () => {
    renderEditor();

    await screen.findByRole('heading', { name: gatewayFixture.title });
    expect(screen.getByRole('link', { name: '批量导入' })).toHaveAttribute(
      'href',
      `/surveys/${surveyId}/import`,
    );
    expect(screen.getByRole('link', { name: '快速预览' })).toHaveAttribute(
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
    const rendered = renderEditor(api, `/surveys/${surveyId}/edit?question=${singleUuid}`);

    const assertPublishCaches = seedPublishCaches(rendered.queryClient);
    const text = await screen.findByLabelText('题目文本');
    fireEvent.change(text, { target: { value: '第一次修改' } });
    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));
    expect(await screen.findByText('已保存版本 5')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('题目文本'), { target: { value: '第二次修改' } });
    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));
    expect(await screen.findByText('已保存版本 6')).toBeInTheDocument();

    assertPublishCaches();
    expect(writes.map((body) => body.expectedVersion)).toEqual([4, 5]);
  });

  test('synchronizesTheSavedTitleAcrossSurveyAndWorkspaceCaches', async () => {
    const api = editorApi({
      [`PUT /v1/surveys/${surveyId}/draft`]: (request) => ({
        surveyId,
        version: 5,
        definition: (request.body as Record<string, unknown>).definition,
      }),
    });
    const rendered = renderEditor(api);
    const pathKey = ['resource-path', 'tenant-a', surveyId] as const;
    const listKey = resourceQueryKey('tenant-a', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa');
    const surveyResource = {
      id: surveyId,
      kind: 'survey' as const,
      parentId: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      name: overview.title,
      createdAt: '2026-10-08T08:00:00Z',
      updatedAt: '2026-10-09T08:00:00Z',
      archivedAt: null,
    };

    const title = await screen.findByLabelText('标题');
    rendered.queryClient.setQueryData(pathKey, [surveyResource]);
    rendered.queryClient.setQueryData(listKey, {
      pages: [{ items: [surveyResource], nextCursor: null }],
      pageParams: [null],
    });
    fireEvent.change(title, { target: { value: '保存后的新标题' } });
    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));

    expect(await screen.findByText('已保存版本 5')).toBeInTheDocument();
    expect(rendered.queryClient.getQueryData(surveyDetailQueryKey('tenant-a', surveyId))).toMatchObject({
      title: '保存后的新标题',
      draftVersion: 5,
    });
    expect(rendered.queryClient.getQueryData(pathKey)).toMatchObject([
      { id: surveyId, name: '保存后的新标题' },
    ]);
    expect(rendered.queryClient.getQueryData(listKey)).toMatchObject({
      pages: [{ items: [{ id: surveyId, name: '保存后的新标题' }] }],
    });
  });

  test('doesNotPolluteSurveyOrWorkspaceCachesWhenTitleSaveFails', async () => {
    const api = editorApi({
      [`PUT /v1/surveys/${surveyId}/draft`]: () => {
        throw new Error('保存失败');
      },
    });
    const rendered = renderEditor(api);
    const pathKey = ['resource-path', 'tenant-a', surveyId] as const;
    const listKey = resourceQueryKey('tenant-a', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa');
    const surveyResource = {
      id: surveyId,
      kind: 'survey' as const,
      parentId: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      name: overview.title,
      createdAt: '2026-10-08T08:00:00Z',
      updatedAt: '2026-10-09T08:00:00Z',
      archivedAt: null,
    };

    const title = await screen.findByLabelText('标题');
    rendered.queryClient.setQueryData(pathKey, [surveyResource]);
    rendered.queryClient.setQueryData(listKey, {
      pages: [{ items: [surveyResource], nextCursor: null }],
      pageParams: [null],
    });
    fireEvent.change(title, { target: { value: '不应进入缓存的标题' } });
    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('保存失败');
    expect(rendered.queryClient.getQueryData(surveyDetailQueryKey('tenant-a', surveyId))).toMatchObject({
      title: overview.title,
      draftVersion: overview.draftVersion,
    });
    expect(rendered.queryClient.getQueryData(pathKey)).toMatchObject([
      { id: surveyId, name: overview.title },
    ]);
    expect(rendered.queryClient.getQueryData(listKey)).toMatchObject({
      pages: [{ items: [{ id: surveyId, name: overview.title }] }],
    });
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
    expect(screen.getByRole('button', { name: '继续编辑' })).toHaveFocus();
    fireEvent.keyDown(screen.getByRole('dialog', { name: '草稿版本冲突' }), { key: 'Tab', shiftKey: true });
    expect(screen.getByRole('button', { name: '导出本地草稿' })).toHaveFocus();
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

    const assertPublishCaches = seedPublishCaches(rendered.queryClient);
    fireEvent.change(await screen.findByLabelText('题目文本'), {
      target: { value: '触发冲突' },
    });
    const save = screen.getByRole('button', { name: '保存草稿' });
    save.focus();
    fireEvent.click(save);
    fireEvent.click(await screen.findByRole('button', { name: '重新载入' }));

    expect(await screen.findByLabelText('题目文本')).toHaveValue('服务器重新载入的题目');
    assertPublishCaches();
    expect(screen.getByRole('main')).toHaveFocus();
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

  test.each([
    [819, true],
    [820, false],
  ])('uses the approved tab boundary at %ipx', async (width, expectsTabs) => {
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: query === '(max-width: 819px)' && width <= 819,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));
    renderEditor(editorApi(), `/surveys/${surveyId}/edit?question=${singleUuid}`);

    await screen.findByRole('heading', { name: '问卷大纲' });
    if (!expectsTabs) {
      expect(screen.queryByRole('tablist', { name: '编辑区域' })).not.toBeInTheDocument();
      return;
    }
    expect(screen.getByRole('tablist', { name: '编辑区域' })).toBeInTheDocument();
    expect(screen.getByRole('tabpanel', { name: '大纲' })).toBeInTheDocument();
    const outlineTab = screen.getByRole('tab', { name: '大纲' });
    outlineTab.focus();
    fireEvent.keyDown(outlineTab, { key: 'ArrowRight' });
    expect(screen.getByRole('tab', { name: '编辑' })).toHaveFocus();
    expect(screen.getByRole('tabpanel', { name: '编辑' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: '编辑' }));
    expect(screen.getByRole('tabpanel', { name: '编辑' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: '属性' }));
    expect(screen.getByRole('tabpanel', { name: '属性' })).toBeInTheDocument();
  });

  test.each([820, 1179])('opens and closes the properties inspector at %ipx', async (width) => {
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: query === '(min-width: 820px) and (max-width: 1179px)' && width >= 820 && width <= 1179,
      media: query, onchange: null, addEventListener: vi.fn(), removeEventListener: vi.fn(),
      addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: vi.fn(),
    }));
    renderEditor(editorApi(), `/surveys/${surveyId}/edit?question=${singleUuid}`);
    const open = await screen.findByRole('button', { name: '打开属性检查器' });
    expect(screen.queryByRole('dialog', { name: '问卷属性' })).not.toBeInTheDocument();
    fireEvent.click(open);
    expect(screen.getByRole('dialog', { name: '问卷属性' })).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole('dialog', { name: '问卷属性' }), { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: '问卷属性' })).not.toBeInTheDocument();
    expect(open).toHaveFocus();
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
