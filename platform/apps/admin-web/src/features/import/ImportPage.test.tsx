import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import {
  createMemoryRouter,
  MemoryRouter,
  Route,
  RouterProvider,
  Routes,
  useParams,
} from 'react-router-dom';
import { describe, expect, test } from 'vitest';
import { ApiError } from '../../shared/api/errors';
import type { ApiClient, ApiRequest } from '../../shared/api/http';
import {
  approvalRequestsQueryKey,
  surveyOverviewQueryKey,
  versionQueryKey,
  versionsQueryKey,
} from '../../shared/api/approvals';
import { surveyDraftQueryKey } from '../../shared/api/surveys';
import { ImportPage } from './ImportPage';

const surveyId = '11111111-1111-4111-8111-111111111111';
const surveyBId = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const tenantA = 'tenant-a';
const tenantB = 'tenant-b';
const groupOne = '22222222-2222-4222-8222-222222222222';
const groupTwo = '33333333-3333-4333-8333-333333333333';
const sourceText = '1. 工作满意吗？[单选]\nA. 满意\nB. 不满意\n\n2. 请说明原因[填空]';

const definition = {
  definitionVersion: 2,
  title: '员工体验调查',
  language: 'zh-Hans',
  groups: [
    { uuid: groupOne, title: '基本信息', questions: [] },
    { uuid: groupTwo, title: '反馈', questions: [] },
  ],
};

const draft = { surveyId, version: 7, definition };
const preview = {
  lineCount: 5,
  questions: [
    {
      index: 0,
      line: 1,
      code: 'Q1',
      type: 'L',
      typeName: '单选',
      typeInferred: false,
      text: '工作满意吗？',
      mandatory: false,
      options: [
        { code: 'A1', text: '满意' },
        { code: 'A2', text: '不满意' },
      ],
      importable: true,
      problems: [],
    },
    {
      index: 1,
      line: 5,
      code: 'Q2',
      type: 'S',
      typeName: '填空',
      typeInferred: false,
      text: '请说明原因',
      mandatory: false,
      options: [],
      importable: true,
      problems: [],
    },
  ],
  problems: [{ line: 4, code: 'unrecognized_line', message: '无法识别这一行' }],
};

const capabilities = {
  canCreateProject: false,
  canCreateChildren: false,
  canEdit: true,
  canSubmitApproval: false,
  canPublishDirectly: false,
  canApprovePublish: false,
};

type RequestHandler = (request: ApiRequest<unknown>) => unknown | Promise<unknown>;

function apiFrom(handler: RequestHandler): ApiClient {
  return {
    request: (request) => Promise.resolve(handler(request as ApiRequest<unknown>)) as never,
  };
}

function standardApi(overrides: Partial<Record<string, RequestHandler>> = {}) {
  return apiFrom((request) => {
    const key = `${request.method ?? 'GET'} ${request.path}`;
    if (overrides[key]) return overrides[key](request);
    if (key === `GET /v1/surveys/${surveyId}/draft`) return draft;
    if (key === `GET /v1/resource-capabilities?resourceId=${surveyId}`) return capabilities;
    if (key === `POST /v1/surveys/${surveyId}/import/preview`) return preview;
    throw new Error(`Unhandled request: ${key}`);
  });
}

function renderImport(api: ApiClient, tenantId = tenantA) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return {
    queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[`/surveys/${surveyId}/import`]}>
          <Routes>
            <Route
              path="/surveys/:surveyId/import"
              element={<ImportPage api={api} surveyId={surveyId} tenantId={tenantId} />}
            />
            <Route path="/surveys/:surveyId/edit" element={<p>已返回编辑器</p>} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  };
}

async function enterAndPreview(api: ApiClient) {
  renderImport(api);
  const input = await screen.findByLabelText('待导入文本');
  fireEvent.change(input, { target: { value: sourceText } });
  fireEvent.click(screen.getByRole('button', { name: '预览导入' }));
  await screen.findByText('工作满意吗？');
  return input;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function previewWithText(text: string) {
  return {
    ...preview,
    questions: preview.questions.map((question, index) =>
      index === 0 ? { ...question, text } : question,
    ),
  };
}

describe('ImportPage', () => {
  test('linksBackToTheEditorWithoutReloadingTheSession', async () => {
    renderImport(standardApi());

    expect(await screen.findByRole('link', { name: '返回编辑' })).toHaveAttribute(
      'href',
      `/surveys/${surveyId}/edit`,
    );
  });

  test('previewsWithoutWritingAndSelectsValidQuestionsByDefault', async () => {
    const requests: ApiRequest<unknown>[] = [];
    const api = standardApi({
      [`POST /v1/surveys/${surveyId}/import/preview`]: (request) => {
        requests.push(request);
        return preview;
      },
    });

    await enterAndPreview(api);

    expect(requests).toHaveLength(1);
    expect(requests[0]).toMatchObject({ method: 'POST', body: { text: sourceText } });
    expect(screen.getByRole('checkbox', { name: /Q1 工作满意吗/ })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: /Q2 请说明原因/ })).toBeChecked();
  });

  test('keepsBadLinesVisibleWithTheirOriginalLineNumbers', async () => {
    await enterAndPreview(standardApi());

    expect(screen.getByText('第 4 行')).toBeInTheDocument();
    expect(screen.getByText('无法识别这一行')).toBeInTheDocument();
  });

  test('requiresANewPreviewAfterTheSourceTextChanges', async () => {
    const input = await enterAndPreview(standardApi());

    fireEvent.change(input, { target: { value: `${sourceText}\n3. 新问题[填空]` } });

    expect(screen.queryByRole('heading', { name: '解析结果' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /确认导入/ })).not.toBeInTheDocument();
  });

  test('ignoresADeferredPreviewResponseAfterTheSourceRevisionChanges', async () => {
    const first = deferred<typeof preview>();
    let previewReads = 0;
    const api = standardApi({
      [`POST /v1/surveys/${surveyId}/import/preview`]: () => {
        previewReads += 1;
        return previewReads === 1 ? first.promise : previewWithText('B 的预览题目');
      },
    });
    renderImport(api);
    const input = await screen.findByLabelText('待导入文本');
    fireEvent.change(input, { target: { value: 'A 原文' } });
    fireEvent.click(screen.getByRole('button', { name: '预览导入' }));
    await waitFor(() => expect(previewReads).toBe(1));

    fireEvent.change(input, { target: { value: 'B 原文' } });
    await act(async () => first.resolve(previewWithText('A 的预览题目')));

    expect(screen.queryByText('A 的预览题目')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /确认导入/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '预览导入' }));
    expect(await screen.findByText('B 的预览题目')).toBeInTheDocument();
  });

  test('isolatesLocalStateAndDeferredResponsesWhenTheSurveyRouteChanges', async () => {
    const delayedA = deferred<typeof preview>();
    let previewReads = 0;
    const api = apiFrom((request) => {
      const key = `${request.method ?? 'GET'} ${request.path}`;
      if (key === `GET /v1/surveys/${surveyId}/draft`) return draft;
      if (key === `GET /v1/surveys/${surveyBId}/draft`) {
        return { ...draft, surveyId: surveyBId, version: 3 };
      }
      if (key.startsWith('GET /v1/resource-capabilities?resourceId=')) return capabilities;
      if (key === `POST /v1/surveys/${surveyId}/import/preview`) {
        previewReads += 1;
        return previewReads === 1 ? preview : delayedA.promise;
      }
      if (key === `POST /v1/surveys/${surveyBId}/import/preview`) {
        return previewWithText('B 路由预览');
      }
      throw new Error(`Unhandled request: ${key}`);
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    function RouteHarness() {
      const currentSurveyId = useParams().surveyId ?? '';
      return <ImportPage api={api} surveyId={currentSurveyId} tenantId={tenantA} />;
    }
    const router = createMemoryRouter(
      [{ path: '/surveys/:surveyId/import', element: <RouteHarness /> }],
      { initialEntries: [`/surveys/${surveyId}/import`] },
    );
    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    const input = await screen.findByLabelText('待导入文本');
    fireEvent.change(input, { target: { value: sourceText } });
    fireEvent.click(screen.getByRole('button', { name: '预览导入' }));
    await screen.findByText('工作满意吗？');
    fireEvent.click(screen.getByRole('checkbox', { name: /Q2 请说明原因/ }));
    fireEvent.change(screen.getByLabelText('导入到题组'), { target: { value: groupTwo } });
    fireEvent.click(screen.getByRole('button', { name: '预览导入' }));
    await waitFor(() => expect(previewReads).toBe(2));

    await act(async () => router.navigate(`/surveys/${surveyBId}/import`));
    expect(await screen.findByText('当前草稿版本 3')).toBeInTheDocument();
    expect(screen.getByLabelText('待导入文本')).toHaveValue('');
    expect(screen.queryByRole('heading', { name: '解析结果' })).not.toBeInTheDocument();
    await act(async () => delayedA.resolve(previewWithText('A 延迟预览')));
    expect(screen.queryByText('A 延迟预览')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /确认导入/ })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('待导入文本'), { target: { value: 'B 原文' } });
    fireEvent.click(screen.getByRole('button', { name: '预览导入' }));
    expect(await screen.findByText('B 路由预览')).toBeInTheDocument();
    expect(screen.getByLabelText('导入到题组')).toHaveValue('');
    expect(screen.getByRole('checkbox', { name: /Q2 请说明原因/ })).toBeChecked();
  });

  test('importsOnlyTheCheckedOrdinalsIntoTheChosenGroupAndRefreshesTheDraft', async () => {
    let importRequest: ApiRequest<unknown> | undefined;
    const importedDraft = {
      ...draft,
      version: 8,
      definition: {
        ...definition,
        groups: definition.groups.map((group) =>
          group.uuid === groupTwo
            ? { ...group, questions: [{ uuid: crypto.randomUUID(), code: 'Q1', type: 'L', text: '工作满意吗？' }] }
            : group,
        ),
      },
    };
    const api = standardApi({
      [`POST /v1/surveys/${surveyId}/import`]: (request) => {
        importRequest = request;
        return importedDraft;
      },
    });
    const rendered = renderImport(api);
    rendered.queryClient.setQueryData(
      surveyOverviewQueryKey(tenantA, surveyId),
      { id: surveyId, draftVersion: 7 },
    );
    rendered.queryClient.setQueryData(
      approvalRequestsQueryKey(tenantA, surveyId),
      [{ id: 'approval-before-import' }],
    );
    rendered.queryClient.setQueryData(
      versionsQueryKey(tenantA, surveyId),
      [{ version: 1 }],
    );
    rendered.queryClient.setQueryData(
      versionQueryKey(tenantA, surveyId, 1),
      { version: 1, surveyId },
    );
    const input = await screen.findByLabelText('待导入文本');
    fireEvent.change(input, { target: { value: sourceText } });
    fireEvent.click(screen.getByRole('button', { name: '预览导入' }));
    await screen.findByText('工作满意吗？');

    fireEvent.click(screen.getByRole('checkbox', { name: /Q2 请说明原因/ }));
    fireEvent.change(screen.getByLabelText('导入到题组'), { target: { value: groupTwo } });
    fireEvent.click(screen.getByRole('button', { name: '确认导入 1 道题' }));

    expect(await screen.findByText('已返回编辑器')).toBeInTheDocument();
    expect(importRequest).toMatchObject({
      method: 'POST',
      body: { expectedVersion: 7, text: sourceText, accept: [0], groupUuid: groupTwo },
    });
    expect(rendered.queryClient.getQueryData(surveyDraftQueryKey(tenantA, surveyId))).toEqual(importedDraft);
    expect(rendered.queryClient.getQueryData(['survey-draft', surveyId])).toBeUndefined();
    expect(rendered.queryClient.getQueryState(
      surveyOverviewQueryKey(tenantA, surveyId),
    )?.isInvalidated).toBe(true);
    expect(rendered.queryClient.getQueryState(
      approvalRequestsQueryKey(tenantA, surveyId),
    )?.isInvalidated).toBe(true);
    expect(rendered.queryClient.getQueryState(
      versionsQueryKey(tenantA, surveyId),
    )?.isInvalidated).toBe(true);
    expect(rendered.queryClient.getQueryState(
      versionQueryKey(tenantA, surveyId, 1),
    )?.isInvalidated).toBe(true);
  });

  test('preservesTheLocalTextAndCheckedQuestionsWhenImportConflicts', async () => {
    const api = standardApi({
      [`POST /v1/surveys/${surveyId}/import`]: () => {
        throw new ApiError('conflict', '草稿已被其他人修改，本地内容未被覆盖', 409);
      },
    });
    const input = await enterAndPreview(api);
    fireEvent.click(screen.getByRole('checkbox', { name: /Q2 请说明原因/ }));
    fireEvent.click(screen.getByRole('button', { name: '确认导入 1 道题' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('草稿已被其他人修改，本地内容未被覆盖');
    expect(input).toHaveValue(sourceText);
    expect(screen.getByRole('checkbox', { name: /Q1 工作满意吗/ })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: /Q2 请说明原因/ })).not.toBeChecked();
  });

  test('showsValidationProblemsByOriginalLineAfterA422', async () => {
    let previewReads = 0;
    const api = standardApi({
      [`POST /v1/surveys/${surveyId}/import/preview`]: () => {
        previewReads += 1;
        return previewReads === 1
          ? { ...preview, problems: [] }
          : {
              ...preview,
              problems: [{ line: 5, code: 'unrecognized_line', message: '此行内容无法识别' }],
            };
      },
      [`POST /v1/surveys/${surveyId}/import`]: () => {
        throw new ApiError('validation', '定义或导入内容未通过校验', 422);
      },
    });
    await enterAndPreview(api);
    fireEvent.click(screen.getByRole('button', { name: '确认导入 2 道题' }));

    expect(await screen.findByText('第 5 行')).toBeInTheDocument();
    expect(screen.getByText('此行内容无法识别')).toBeInTheDocument();
    expect(previewReads).toBe(2);
  });

  test('doesNotReuseTheSameSurveyCacheAcrossTenants', async () => {
    const apiA = standardApi();
    const apiB = standardApi({
      [`GET /v1/surveys/${surveyId}/draft`]: () => ({ ...draft, version: 12 }),
    });
    function TenantHarness() {
      const [tenant, setTenant] = useState(tenantA);
      const currentApi = tenant === tenantA ? apiA : apiB;
      return (
        <>
          <button type="button" onClick={() => setTenant(tenantB)}>切换租户</button>
          <ImportPage api={currentApi} surveyId={surveyId} tenantId={tenant} />
        </>
      );
    }
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter><TenantHarness /></MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText('当前草稿版本 7')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('待导入文本'), { target: { value: '租户 A 原文' } });
    fireEvent.click(screen.getByRole('button', { name: '切换租户' }));

    expect(await screen.findByText('当前草稿版本 12')).toBeInTheDocument();
    expect(screen.getByLabelText('待导入文本')).toHaveValue('');
    expect(queryClient.getQueryData(surveyDraftQueryKey(tenantA, surveyId))).toEqual(draft);
    expect(queryClient.getQueryData(surveyDraftQueryKey(tenantB, surveyId))).toEqual({ ...draft, version: 12 });
  });
});
