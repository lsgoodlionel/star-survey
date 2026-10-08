import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, test } from 'vitest';
import { ApiError } from '../../shared/api/errors';
import type { ApiClient, ApiRequest } from '../../shared/api/http';
import { ImportPage } from './ImportPage';

const surveyId = '11111111-1111-4111-8111-111111111111';
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

function renderImport(api: ApiClient) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return {
    queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[`/surveys/${surveyId}/import`]}>
          <Routes>
            <Route path="/surveys/:surveyId/import" element={<ImportPage api={api} surveyId={surveyId} />} />
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

describe('ImportPage', () => {
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
    rendered.queryClient.setQueryData(['survey', surveyId], { id: surveyId, draftVersion: 7 });
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
    expect(rendered.queryClient.getQueryData(['survey-draft', surveyId])).toEqual(importedDraft);
    expect(rendered.queryClient.getQueryState(['survey', surveyId])?.isInvalidated).toBe(true);
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
});
