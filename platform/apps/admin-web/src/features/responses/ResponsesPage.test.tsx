import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { SurveyShellContext, type RecentWorkReadiness } from '../../app/surveyShellContext';
import { ApiError } from '../../shared/api/errors';
import type { ExportClient, ExportJobView } from '../../shared/api/exports';
import type { ApiClient, ApiRequest } from '../../shared/api/http';
import { responsePageQueryKey, responseSummaryQueryKey } from '../../shared/api/responses';
import { renderWithQuery } from '../../test/render';
import { ResponsesPage } from './ResponsesPage';

const surveyId = '11111111-1111-4111-8111-111111111111';
const jobId = '77777777-7777-4777-8777-777777777777';
const summary = {
  surveyId,
  versions: [
    { version: 2, counts: { inProgress: 1, engineCompleted: 2, deleted: 0 } },
    { version: 1, counts: { inProgress: 0, engineCompleted: 1, deleted: 1 } },
  ],
  total: { inProgress: 1, engineCompleted: 3, deleted: 1 },
};
const page = {
  items: [{
    version: 2,
    engineInstanceId: 'engine-a',
    engineSid: 123456,
    generation: 'g2',
    responseId: 88,
    state: 'engine_completed',
    startedAt: '2026-10-09T08:00:00Z',
    completedAt: '2026-10-09T08:05:00Z',
    deletedAt: null,
    answersStatus: 'available',
    answers: { Q1: '满意', email: 'm***@example.test' },
  }],
  nextCursor: 'next-page',
  sensitiveRevealed: false,
};

function apiFrom(handler: (request: ApiRequest<unknown>) => unknown): ApiClient {
  return { request: (request) => Promise.resolve(handler(request as ApiRequest<unknown>)) as never };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, reject, resolve };
}

function renderWithReadiness(
  api: ApiClient,
  queryClient: QueryClient,
  reportPageReady: (readiness: RecentWorkReadiness) => void,
) {
  return render(
    <QueryClientProvider client={queryClient}>
      <SurveyShellContext.Provider value={{ reportPageReady, setUnsavedChanges: vi.fn() }}>
        <MemoryRouter>
          <ResponsesPage api={api} exportClient={exportClient()} surveyId={surveyId} tenantId="tenant-a" />
        </MemoryRouter>
      </SurveyShellContext.Provider>
    </QueryClientProvider>,
  );
}

function baseJob(status: ExportJobView['status']): ExportJobView {
  return {
    jobId,
    surveyId,
    format: 'xlsx',
    templateVersion: 'current',
    filter: { states: ['engine_completed'], versions: null },
    status,
    sensitiveRevealed: false,
    totalRows: status === 'queued' ? null : 3,
    processedRows: status === 'queued' ? 0 : 3,
    attempts: 1,
    error: null,
    fileSize: status === 'completed' ? 2048 : null,
    sha256: status === 'completed' ? 'a'.repeat(64) : null,
    createdAt: '2026-10-09T08:00:00Z',
    startedAt: '2026-10-09T08:01:00Z',
    finishedAt: status === 'queued' ? null : '2026-10-09T08:02:00Z',
    expiresAt: '2026-10-10T08:00:00Z',
  };
}

function responseApi(overrides: Partial<Record<string, unknown>> = {}) {
  return apiFrom((request) => {
    if (request.path === `/v1/surveys/${surveyId}/responses/summary`) return overrides.summary ?? summary;
    if (request.path.startsWith(`/v1/surveys/${surveyId}/responses?`)) return overrides.page ?? page;
    throw new Error(`Unhandled request: ${request.path}`);
  });
}

function exportClient(overrides: Partial<ExportClient> = {}): ExportClient {
  return {
    create: vi.fn().mockResolvedValue(baseJob('queued')),
    get: vi.fn().mockResolvedValue(baseJob('running')),
    cancel: vi.fn().mockResolvedValue(baseJob('cancelled')),
    download: vi.fn().mockResolvedValue({
      blob: new Blob(['file']),
      filename: 'responses.xlsx',
      contentType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      sha256: 'a'.repeat(64),
    }),
    ...overrides,
  };
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('ResponsesPage', () => {
  test('waitsForCachedSummaryAndRowsToFinishTheirRequiredRefetchBeforeReportingReady', async () => {
    const summaryRequest = deferred<typeof summary>();
    const pageRequest = deferred<typeof page>();
    const api = apiFrom((request) => request.path.endsWith('/responses/summary')
      ? summaryRequest.promise
      : pageRequest.promise);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    queryClient.setQueryData(responseSummaryQueryKey('tenant-a', surveyId), summary);
    queryClient.setQueryData(responsePageQueryKey('tenant-a', surveyId, { state: null, version: null, cursor: null, limit: 50 }), page);
    const reportPageReady = vi.fn();

    renderWithReadiness(api, queryClient, reportPageReady);
    expect(await screen.findByText('m***@example.test')).toBeInTheDocument();
    expect(reportPageReady).not.toHaveBeenCalled();

    summaryRequest.resolve(summary);
    pageRequest.resolve(page);
    await waitFor(() => expect(reportPageReady).toHaveBeenCalledWith({ page: 'responses', version: null }));
  });

  test('doesNotReportReadyWhenACachedResponsesRefetchFails', async () => {
    const summaryRequest = deferred<typeof summary>();
    const pageRequest = deferred<typeof page>();
    const api = apiFrom((request) => request.path.endsWith('/responses/summary')
      ? summaryRequest.promise
      : pageRequest.promise);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    queryClient.setQueryData(responseSummaryQueryKey('tenant-a', surveyId), summary);
    queryClient.setQueryData(responsePageQueryKey('tenant-a', surveyId, { state: null, version: null, cursor: null, limit: 50 }), page);
    const reportPageReady = vi.fn();

    renderWithReadiness(api, queryClient, reportPageReady);
    summaryRequest.resolve(summary);
    pageRequest.reject(new ApiError('unavailable', '服务暂时不可用'));

    expect(await screen.findByRole('alert')).toHaveTextContent('答卷数据加载失败');
    expect(reportPageReady).not.toHaveBeenCalled();
  });

  test('requiresAtLeastOneAuthorizedResponsesDatasetBeforeReportingReady', async () => {
    const forbidden = new ApiError('forbidden', '无权执行当前操作', 403);
    const bothForbidden = apiFrom(() => Promise.reject(forbidden));
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const reportPageReady = vi.fn();
    const denied = renderWithReadiness(bothForbidden, queryClient, reportPageReady);

    await screen.findByText('你没有查看答卷统计摘要的权限。');
    await screen.findByText('你可以查看统计摘要，但没有查看答卷明细和导出的权限。');
    expect(reportPageReady).not.toHaveBeenCalled();
    denied.unmount();

    const partialQueryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const partialReady = vi.fn();
    renderWithReadiness(apiFrom((request) => request.path.endsWith('/responses/summary')
      ? summary
      : Promise.reject(forbidden)), partialQueryClient, partialReady);
    await waitFor(() => expect(partialReady).toHaveBeenCalledWith({ page: 'responses', version: null }));
  });

  test('alwaysOffersAManualRefreshAndReplacesFreshCachedSummaryAndRows', async () => {
    let completed = 0;
    const api = apiFrom((request) => {
      if (request.path.endsWith('/responses/summary')) {
        return { ...summary, total: { ...summary.total, engineCompleted: completed } };
      }
      if (request.path.includes('/responses?')) {
        return completed ? page : { ...page, items: [], nextCursor: null };
      }
      throw new Error(`Unhandled request: ${request.path}`);
    });
    renderWithQuery(
      <MemoryRouter>
        <ResponsesPage api={api} exportClient={exportClient()} surveyId={surveyId} tenantId="tenant-a" />
      </MemoryRouter>,
    );

    expect(await screen.findByLabelText('已完成 0')).toBeInTheDocument();
    completed = 1;
    fireEvent.click(screen.getByRole('button', { name: '刷新答卷数据' }));

    expect(await screen.findByLabelText('已完成 1')).toBeInTheDocument();
    expect(screen.getByRole('table')).toHaveTextContent('#88');
    expect(screen.getByText('答卷数据已更新')).toBeInTheDocument();
  });

  test('shows summary, masked detail rows and cursor pagination with status and version filters', async () => {
    const requests: string[] = [];
    const api = apiFrom((request) => {
      requests.push(request.path);
      if (request.path.endsWith('/responses/summary')) return summary;
      if (request.path.includes('/responses?')) {
        return request.path.includes('cursor=next-page') ? { ...page, items: [], nextCursor: null } : page;
      }
      throw new Error(`Unhandled request: ${request.path}`);
    });
    renderWithQuery(
      <MemoryRouter>
        <ResponsesPage api={api} exportClient={exportClient()} surveyId={surveyId} tenantId="tenant-a" />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('heading', { name: '答卷与导出' })).toBeInTheDocument();
    expect(await screen.findByLabelText('已完成 3')).toBeInTheDocument();
    expect(screen.getByLabelText('填写中 1')).toBeInTheDocument();
    expect(screen.getByLabelText('已删除 1')).toBeInTheDocument();
    expect(screen.getByText('敏感字段已按权限遮蔽')).toBeInTheDocument();
    expect(screen.getByText('m***@example.test')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('答卷状态'), { target: { value: 'engine_completed' } });
    fireEvent.change(screen.getByLabelText('发布版本'), { target: { value: '2' } });
    await waitFor(() => expect(requests.some((path) =>
      path.includes('state=engine_completed') && path.includes('version=2'),
    )).toBe(true));

    fireEvent.click(await screen.findByRole('button', { name: '下一页' }));
    expect(await screen.findByText('当前筛选下没有答卷。')).toBeInTheDocument();
    expect(requests.some((path) => path.includes('cursor=next-page'))).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: '上一页' }));
    expect(await screen.findByText('m***@example.test')).toBeInTheDocument();
  });

  test('offers only backend formats and supports create cancel download and expired states', async () => {
    const create = vi.fn()
      .mockResolvedValueOnce(baseJob('queued'))
      .mockResolvedValueOnce(baseJob('completed'))
      .mockResolvedValueOnce(baseJob('expired'));
    const cancel = vi.fn().mockResolvedValue(baseJob('cancelled'));
    const download = vi.fn().mockResolvedValue({
      blob: new Blob(['file']),
      filename: 'responses.xlsx',
      contentType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      sha256: 'a'.repeat(64),
    });
    const createObjectURL = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:download');
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    const client = exportClient({ create, cancel, download });

    renderWithQuery(
      <ResponsesPage api={responseApi()} exportClient={client} surveyId={surveyId} tenantId="tenant-a" />,
    );
    await screen.findByRole('heading', { name: '创建导出' });
    const format = screen.getByLabelText('导出格式');
    expect(within(format).getAllByRole('option').map((option) => option.getAttribute('value'))).toEqual([
      'csv', 'xlsx', 'sav', 'docx', 'attachments',
    ]);
    expect(screen.queryByRole('option', { name: /pdf/i })).not.toBeInTheDocument();

    fireEvent.change(format, { target: { value: 'xlsx' } });
    fireEvent.click(screen.getByRole('button', { name: '创建导出任务' }));
    expect(await screen.findByText('等待处理')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '取消任务' }));
    expect(await screen.findByText('已取消')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '创建导出任务' }));
    expect(await screen.findByText('导出完成')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '下载导出文件' }));
    await waitFor(() => expect(download).toHaveBeenCalledWith(jobId));
    expect(createObjectURL).toHaveBeenCalled();
    expect(click).toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '创建导出任务' }));
    expect(await screen.findByText('文件已过期')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '下载导出文件' })).not.toBeInTheDocument();
  });

  test('uses the selected status and version in the exact export snapshot request', async () => {
    const create = vi.fn().mockResolvedValue(baseJob('queued'));
    renderWithQuery(
      <ResponsesPage
        api={responseApi()}
        exportClient={exportClient({ create })}
        surveyId={surveyId}
        tenantId="tenant-a"
      />,
    );

    await screen.findByLabelText('发布版本 2');
    fireEvent.change(screen.getByLabelText('答卷状态'), { target: { value: 'engine_completed' } });
    fireEvent.change(screen.getByLabelText('发布版本'), { target: { value: '2' } });
    fireEvent.change(screen.getByLabelText('导出格式'), { target: { value: 'xlsx' } });
    fireEvent.click(screen.getByRole('button', { name: '创建导出任务' }));

    await waitFor(() => expect(create).toHaveBeenCalledWith(
      surveyId,
      {
        format: 'xlsx',
        filter: { states: ['engine_completed'], versions: [2] },
        templateVersion: null,
      },
      { idempotencyKey: expect.any(String) },
    ));
  });

  test('keeps the export job and offers reload after polling fails', async () => {
    const get = vi.fn()
      .mockRejectedValueOnce(new ApiError('unavailable', '服务暂时不可用'))
      .mockResolvedValue(baseJob('running'));
    renderWithQuery(
      <ResponsesPage
        api={responseApi()}
        exportClient={exportClient({ get })}
        surveyId={surveyId}
        tenantId="tenant-a"
      />,
    );

    await screen.findByRole('heading', { name: '创建导出' });
    fireEvent.click(screen.getByRole('button', { name: '创建导出任务' }));
    expect(await screen.findByRole('alert', { name: '导出任务状态刷新失败' })).toHaveTextContent(
      '任务仍已保留',
    );
    expect(screen.getByText('等待处理')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重新加载任务状态' }));
    expect(await screen.findByText('正在导出')).toBeInTheDocument();
  });

  test('keeps the export job and offers retry and reload after cancellation fails', async () => {
    const get = vi.fn().mockResolvedValue(baseJob('queued'));
    const cancel = vi.fn()
      .mockRejectedValueOnce(new ApiError('unavailable', '服务暂时不可用'))
      .mockResolvedValue(baseJob('cancelled'));
    renderWithQuery(
      <ResponsesPage
        api={responseApi()}
        exportClient={exportClient({ get, cancel })}
        surveyId={surveyId}
        tenantId="tenant-a"
      />,
    );

    await screen.findByRole('heading', { name: '创建导出' });
    fireEvent.click(screen.getByRole('button', { name: '创建导出任务' }));
    expect(await screen.findByText('等待处理')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '取消任务' }));
    expect(await screen.findByRole('alert', { name: '取消导出任务失败' })).toHaveTextContent(
      '任务仍已保留',
    );
    expect(screen.getByText('等待处理')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '刷新任务状态' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试取消' }));
    expect(await screen.findByText('已取消')).toBeInTheDocument();
  });

  test('keeps summary available when detail permission is missing and separates empty and failed states', async () => {
    const noDetailApi = apiFrom((request) => {
      if (request.path.endsWith('/responses/summary')) return summary;
      throw new ApiError('forbidden', '无权执行当前操作', 403);
    });
    const forbidden = renderWithQuery(
      <ResponsesPage api={noDetailApi} exportClient={exportClient()} surveyId={surveyId} tenantId="tenant-a" />,
    );
    expect(await screen.findByText('你可以查看统计摘要，但没有查看答卷明细和导出的权限。')).toBeInTheDocument();
    expect(screen.getByLabelText('已完成 3')).toBeInTheDocument();
    forbidden.unmount();

    const empty = renderWithQuery(
      <ResponsesPage
        api={responseApi({
          summary: {
            surveyId,
            versions: [],
            total: { inProgress: 0, engineCompleted: 0, deleted: 0 },
          },
          page: { items: [], nextCursor: null, sensitiveRevealed: false },
        })}
        exportClient={exportClient()}
        surveyId={surveyId}
        tenantId="tenant-a"
      />,
    );
    expect(await screen.findByText('当前还没有答卷。')).toBeInTheDocument();
    empty.unmount();

    const failedApi = apiFrom(() => { throw new ApiError('unavailable', '服务暂时不可用'); });
    renderWithQuery(
      <ResponsesPage api={failedApi} exportClient={exportClient()} surveyId={surveyId} tenantId="tenant-a" />,
    );
    expect(await screen.findByRole('alert')).toHaveTextContent('答卷数据加载失败');
    expect(screen.getByRole('button', { name: '重新加载' })).toBeInTheDocument();
  });
});
