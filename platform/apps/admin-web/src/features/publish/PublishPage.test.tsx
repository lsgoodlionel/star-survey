import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { ApiError } from '../../shared/api/errors';
import type { ApiClient, ApiRequest } from '../../shared/api/http';
import { renderWithQuery } from '../../test/render';
import { PublishPage } from './PublishPage';
import { VersionDetailPage } from './VersionDetailPage';

const surveyId = '11111111-1111-4111-8111-111111111111';
const approvalId = '22222222-2222-4222-8222-222222222222';
const requestId = '33333333-3333-4333-8333-333333333333';

const baseSurvey = {
  id: surveyId,
  title: '员工体验调查',
  status: 'draft',
  draftVersion: 4,
  publishedVersion: null,
  lastPublish: null,
};

const baseApproval = {
  id: approvalId,
  surveyId,
  draftVersion: 4,
  status: 'pending',
  applicant: 'author-1',
  submittedAt: '2026-10-08T01:00:00Z',
  decidedBy: null,
  decidedAt: null,
  reason: null,
};

const baseVersion = {
  surveyId,
  version: 2,
  requestId,
  draftVersion: 4,
  engineInstanceId: 'test-engine',
  engineSid: 876543,
  compilerVersion: '2.1.0',
  fingerprintVersion: '1',
  fingerprint: 'sha256:published-fingerprint',
  language: 'zh-Hans',
  enginePublishedAt: '2026-10-08T01:04:00Z',
  publishedBy: 'publisher-1',
  publishedAt: '2026-10-08T01:04:02Z',
  fields: [
    {
      questionUuid: '44444444-4444-4444-8444-444444444444',
      code: 'Q1',
      type: 'L',
      fieldname: '876543X1X1Q1',
      aid: '',
      scale: 0,
    },
  ],
  definition: {
    definitionVersion: 2,
    title: '员工体验调查（发布快照）',
    language: 'zh-Hans',
  },
  live: true,
  supersededAt: null,
  engineClosedAt: null,
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
    const override = overrides[key];
    if (override) return override(request);
    if (key === `GET /v1/surveys/${surveyId}`) return baseSurvey;
    if (key === `GET /v1/surveys/${surveyId}/approval-requests`) return [baseApproval];
    if (key === `GET /v1/surveys/${surveyId}/versions`) return [baseVersion];
    throw new Error(`Unhandled request: ${key}`);
  });
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('PublishPage', () => {
  test('showsOnlyActionsAllowedByTheCurrentApprovalState', async () => {
    const statuses = ['approved', 'rejected', 'withdrawn', 'voided', 'published', 'pending'];
    const approvals = statuses.map((status, index) => ({
      ...baseApproval,
      id: `${index + 1}2222222-2222-4222-8222-222222222222`,
      status,
      submittedAt: `2026-10-0${index + 1}T01:00:00Z`,
    }));
    const api = standardApi({
      [`GET /v1/surveys/${surveyId}/approval-requests`]: () => approvals,
      [`POST /v1/publish-approvals/${approvals.at(-1)?.id}/approve`]: () => {
        throw new ApiError('forbidden', '无权执行当前操作', 403);
      },
    });

    renderWithQuery(<PublishPage api={api} surveyId={surveyId} />);

    for (const label of ['待审批', '已批准', '已驳回', '已撤回', '已作废', '已发布']) {
      expect(await screen.findByText(label)).toBeInTheDocument();
    }
    expect(screen.getByRole('button', { name: '批准申请' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '驳回申请' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '撤回申请' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '发布问卷' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '提交审批' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '批准申请' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('无权执行当前操作');
    expect(screen.getByRole('button', { name: '批准申请' })).toBeInTheDocument();
  });

  test('refreshesAnApprovedRequestToVoidedAfterTheDraftChanges', async () => {
    let approvalReads = 0;
    let overviewReads = 0;
    let versionReads = 0;
    const api = standardApi({
      [`GET /v1/surveys/${surveyId}`]: () => {
        overviewReads += 1;
        return { ...baseSurvey, draftVersion: overviewReads === 1 ? 4 : 5 };
      },
      [`GET /v1/surveys/${surveyId}/approval-requests`]: () => {
        approvalReads += 1;
        if (approvalReads === 1) return [baseApproval];
        return [{ ...baseApproval, status: 'voided', reason: '草稿版本已变更' }];
      },
      [`GET /v1/surveys/${surveyId}/versions`]: () => {
        versionReads += 1;
        return [];
      },
      [`POST /v1/publish-approvals/${approvalId}/approve`]: () => ({
        ...baseApproval,
        status: 'approved',
        decidedBy: 'approver-1',
        decidedAt: '2026-10-08T01:02:00Z',
      }),
    });

    renderWithQuery(<PublishPage api={api} surveyId={surveyId} />);
    fireEvent.click(await screen.findByRole('button', { name: '批准申请' }));

    expect(await screen.findByText('已作废')).toBeInTheDocument();
    expect(screen.getByText('草稿版本已变更')).toBeInTheDocument();
    expect(overviewReads).toBeGreaterThanOrEqual(2);
    expect(approvalReads).toBeGreaterThanOrEqual(2);
    expect(versionReads).toBeGreaterThanOrEqual(2);
  });

  test('doesNotClaimSuccessWhilePublishReturns202', async () => {
    const approved = { ...baseApproval, status: 'approved' };
    const api = standardApi({
      [`GET /v1/surveys/${surveyId}/approval-requests`]: () => [approved],
      [`POST /v1/surveys/${surveyId}/publish`]: () => {
        throw new ApiError('pending', '发布结果正在核对', 202);
      },
    });

    renderWithQuery(<PublishPage api={api} surveyId={surveyId} />);
    fireEvent.click(await screen.findByRole('button', { name: '发布问卷' }));

    expect(await screen.findByText('发布结果正在核对')).toBeInTheDocument();
    expect(screen.queryByText('发布成功')).not.toBeInTheDocument();
  });

  test('stopsPollingWhenTheSurveyBecomesPublished', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let overviewReads = 0;
    const api = standardApi({
      [`GET /v1/surveys/${surveyId}`]: () => {
        overviewReads += 1;
        if (overviewReads < 3) {
          return {
            ...baseSurvey,
            status: 'pending_reconciliation',
            lastPublish: {
              requestId,
              engineInstanceId: 'test-engine',
              draftVersion: 4,
              outcome: 'unknown',
              gatewayStatus: 202,
              failedStage: null,
              failures: [],
              orphanEngineSid: null,
              tries: 1,
              nextReconcileAt: '2026-10-08T01:05:00Z',
              manualReviewAt: null,
            },
          };
        }
        return { ...baseSurvey, status: 'published', publishedVersion: 2 };
      },
      [`GET /v1/surveys/${surveyId}/approval-requests`]: () => [
        { ...baseApproval, status: 'approved' },
      ],
      [`POST /v1/surveys/${surveyId}/publish`]: () => {
        throw new ApiError('pending', '发布结果正在核对', 202);
      },
    });

    renderWithQuery(<PublishPage api={api} surveyId={surveyId} />);
    fireEvent.click(await screen.findByRole('button', { name: '发布问卷' }));
    await act(async () => vi.advanceTimersByTimeAsync(4_000));

    expect(await screen.findByText('发布成功')).toBeInTheDocument();
    const readsAtPublished = overviewReads;
    await act(async () => vi.advanceTimersByTimeAsync(6_000));
    expect(overviewReads).toBe(readsAtPublished);
  });

  test('pausesPollingWhileTheDocumentIsHiddenAndCancelsOnUnmount', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let visibility: DocumentVisibilityState = 'visible';
    vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => visibility);
    let overviewReads = 0;
    let aborted = false;
    const api = standardApi({
      [`GET /v1/surveys/${surveyId}`]: (request) => {
        overviewReads += 1;
        request.signal?.addEventListener('abort', () => {
          aborted = true;
        });
        if (overviewReads > 2) return new Promise(() => undefined);
        return {
          ...baseSurvey,
          status: 'pending_reconciliation',
          lastPublish: {
            requestId,
            engineInstanceId: 'test-engine',
            draftVersion: 4,
            outcome: 'unknown',
            gatewayStatus: 202,
            failedStage: null,
            failures: [],
            orphanEngineSid: null,
            tries: 1,
            nextReconcileAt: '2026-10-08T01:05:00Z',
            manualReviewAt: null,
          },
        };
      },
      [`GET /v1/surveys/${surveyId}/approval-requests`]: () => [
        { ...baseApproval, status: 'approved' },
      ],
      [`POST /v1/surveys/${surveyId}/publish`]: () => {
        throw new ApiError('pending', '发布结果正在核对', 202);
      },
    });

    const view = renderWithQuery(<PublishPage api={api} surveyId={surveyId} />);
    fireEvent.click(await screen.findByRole('button', { name: '发布问卷' }));
    await waitFor(() => expect(overviewReads).toBeGreaterThanOrEqual(2));
    visibility = 'hidden';
    fireEvent(document, new Event('visibilitychange'));
    const readsBeforeHiddenWait = overviewReads;
    await act(async () => vi.advanceTimersByTimeAsync(6_000));
    expect(overviewReads).toBe(readsBeforeHiddenWait);

    visibility = 'visible';
    fireEvent(document, new Event('visibilitychange'));
    await act(async () => vi.advanceTimersByTimeAsync(2_000));
    expect(overviewReads).toBeGreaterThan(readsBeforeHiddenWait);
    view.unmount();
    expect(aborted).toBe(true);
  });

  test('showsManualReviewAndOrphanSidAsBlockingWarnings', async () => {
    const api = standardApi({
      [`GET /v1/surveys/${surveyId}`]: () => ({
        ...baseSurvey,
        status: 'pending_reconciliation',
        lastPublish: {
          requestId,
          engineInstanceId: 'test-engine',
          draftVersion: 4,
          outcome: 'unknown',
          gatewayStatus: 502,
          failedStage: 'rollback',
          failures: ['回滚没有完成'],
          orphanEngineSid: 918273,
          tries: 7,
          nextReconcileAt: null,
          manualReviewAt: '2026-10-08T02:00:00Z',
        },
      }),
    });

    renderWithQuery(<PublishPage api={api} surveyId={surveyId} />);

    expect(await screen.findByText('需要人工复核')).toBeInTheDocument();
    expect(screen.getByText(/孤儿问卷.*918273/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '强制重发' })).not.toBeInTheDocument();
  });

  test('renders422FailuresAtThePublishBoundary', async () => {
    let rejected = false;
    const api = standardApi({
      [`GET /v1/surveys/${surveyId}`]: () =>
        rejected
          ? {
              ...baseSurvey,
              status: 'publish_failed',
              lastPublish: {
                requestId,
                engineInstanceId: 'test-engine',
                draftVersion: 4,
                outcome: 'failed',
                gatewayStatus: 422,
                failedStage: 'validate',
                failures: ['题目 Q1 缺少选项'],
                orphanEngineSid: null,
                tries: 1,
                nextReconcileAt: null,
                manualReviewAt: null,
              },
            }
          : baseSurvey,
      [`GET /v1/surveys/${surveyId}/approval-requests`]: () => [
        { ...baseApproval, status: 'approved' },
      ],
      [`POST /v1/surveys/${surveyId}/publish`]: () => {
        rejected = true;
        throw new ApiError('validation', '定义或导入内容未通过校验', 422);
      },
    });

    renderWithQuery(<PublishPage api={api} surveyId={surveyId} />);
    fireEvent.click(await screen.findByRole('button', { name: '发布问卷' }));

    expect(await screen.findByText('定义校验')).toBeInTheDocument();
    expect(screen.getByText('题目 Q1 缺少选项')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('定义或导入内容未通过校验');
  });

  test('neverRendersRawGatewaySecretsOrStackTraces', async () => {
    const api = standardApi({
      [`GET /v1/surveys/${surveyId}`]: () => ({
        ...baseSurvey,
        status: 'publish_failed',
        lastPublish: {
          requestId,
          engineInstanceId: 'test-engine',
          draftVersion: 4,
          outcome: 'failed',
          gatewayStatus: 502,
          failedStage: 'java.lang.IllegalStateException: gateway exploded',
          failures: [
            'Authorization: Bearer raw-gateway-secret',
            'java.lang.IllegalStateException\n at cn.mjy.gateway.Publish.run(Publish.java:41)',
          ],
          orphanEngineSid: null,
          tries: 1,
          nextReconcileAt: null,
          manualReviewAt: null,
        },
      }),
    });

    renderWithQuery(<PublishPage api={api} surveyId={surveyId} />);

    expect(await screen.findByText('发布失败')).toBeInTheDocument();
    expect(screen.getByText('未知阶段')).toBeInTheDocument();
    expect(screen.getAllByText('详细错误已隐藏，请联系管理员。')).toHaveLength(2);
    expect(screen.queryByText(/raw-gateway-secret/)).not.toBeInTheDocument();
    expect(screen.queryByText(/IllegalStateException/)).not.toBeInTheDocument();
  });
});

test('showsPublishedVersionsAsImmutableReadOnlyData', async () => {
  const api = apiFrom((request) => {
    if ((request.method ?? 'GET') === 'GET' && request.path === `/v1/surveys/${surveyId}/versions/2`) {
      return baseVersion;
    }
    throw new Error(`Unhandled request: ${request.path}`);
  });

  renderWithQuery(<VersionDetailPage api={api} surveyId={surveyId} version={2} />);

  expect(await screen.findByRole('heading', { name: '已发布版本 2' })).toBeInTheDocument();
  expect(screen.getByText('当前在线')).toBeInTheDocument();
  expect(screen.getByText('test-engine / 876543')).toBeInTheDocument();
  expect(screen.getByText('Q1')).toBeInTheDocument();
  expect(screen.getByText('876543X1X1Q1')).toBeInTheDocument();
  expect(screen.getByText(/员工体验调查（发布快照）/)).toBeInTheDocument();
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /保存|编辑|恢复/ })).not.toBeInTheDocument();
});
