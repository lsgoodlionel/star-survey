import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { render } from '@testing-library/react';
import { useState } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { ApiError } from '../../shared/api/errors';
import type { ApiClient, ApiRequest } from '../../shared/api/http';
import { publishSurvey, versionQueryKey } from '../../shared/api/approvals';
import { renderWithQuery } from '../../test/render';
import { createAppRoutes } from '../../app/router';
import { PublishPage } from './PublishPage';
import { VersionDetailPage } from './VersionDetailPage';

const surveyId = '11111111-1111-4111-8111-111111111111';
const approvalId = '22222222-2222-4222-8222-222222222222';
const requestId = '33333333-3333-4333-8333-333333333333';
const baseCapabilities = {
  canCreateProject: false,
  canCreateChildren: false,
  canEdit: false,
  canSubmitApproval: true,
  canPublishDirectly: false,
  canApprovePublish: true,
};

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
  draftVersion: 3,
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
    if (key === `GET /v1/resource-capabilities?resourceId=${surveyId}`) return baseCapabilities;
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
  test('usesAnEngineSafeTimeoutForThePublishRequest', async () => {
    const request = vi.fn().mockResolvedValue(undefined);

    await publishSurvey({ request }, surveyId);

    expect(request).toHaveBeenCalledWith(expect.objectContaining({ timeoutMs: 180_000 }));
  });

  test('linksEveryPublishedVersionToItsImmutableDetailPage', async () => {
    renderWithQuery(
      <MemoryRouter>
        <PublishPage actorId="author-1" api={standardApi()} surveyId={surveyId} tenantId="tenant-a" />
      </MemoryRouter>,
    );

    expect(await screen.findByRole('link', { name: '查看版本 2' })).toHaveAttribute(
      'href',
      `/surveys/${surveyId}/versions/2`,
    );
  });

  test('doesNotConsumeFreshPublishDataCachedForAnotherTenant', async () => {
    const tenantApi = (title: string) => standardApi({
      [`GET /v1/surveys/${surveyId}`]: () => ({ ...baseSurvey, title }),
    });
    const apiA = tenantApi('租户 A 发布页');
    const apiB = tenantApi('租户 B 发布页');
    function TenantPublishHarness() {
      const [tenantId, setTenantId] = useState('tenant-a');
      return (
        <>
          <button type="button" onClick={() => setTenantId('tenant-b')}>切换发布租户</button>
          <PublishPage
            actorId="author-1"
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
    render(
      <QueryClientProvider client={queryClient}>
        <TenantPublishHarness />
      </QueryClientProvider>,
    );

    expect(await screen.findByRole('heading', { name: '租户 A 发布页' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '切换发布租户' }));

    expect(await screen.findByRole('heading', { name: '租户 B 发布页' })).toBeInTheDocument();
    expect(screen.queryByText('租户 A 发布页')).not.toBeInTheDocument();
  });

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

    renderWithQuery(<PublishPage actorId="author-1" api={api} surveyId={surveyId} tenantId="tenant-a" />);

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

    const rendered = renderWithQuery(
      <PublishPage actorId="author-1" api={api} surveyId={surveyId} tenantId="tenant-a" />,
    );
    rendered.queryClient.setQueryData(versionQueryKey('tenant-a', surveyId, 2), baseVersion);
    fireEvent.click(await screen.findByRole('button', { name: '批准申请' }));

    expect(await screen.findByText('已作废')).toBeInTheDocument();
    expect(screen.getByText('草稿版本已变更')).toBeInTheDocument();
    expect(overviewReads).toBeGreaterThanOrEqual(2);
    expect(approvalReads).toBeGreaterThanOrEqual(2);
    expect(versionReads).toBeGreaterThanOrEqual(2);
    expect(rendered.queryClient.getQueryState(
      versionQueryKey('tenant-a', surveyId, 2),
    )?.isInvalidated).toBe(true);
  });

  test('doesNotClaimSuccessWhilePublishReturns202', async () => {
    const approved = { ...baseApproval, status: 'approved' };
    const api = standardApi({
      [`GET /v1/surveys/${surveyId}/approval-requests`]: () => [approved],
      [`POST /v1/surveys/${surveyId}/publish`]: () => {
        throw new ApiError('pending', '发布结果正在核对', 202);
      },
    });

    renderWithQuery(<PublishPage actorId="author-1" api={api} surveyId={surveyId} tenantId="tenant-a" />);
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

    renderWithQuery(<PublishPage actorId="author-1" api={api} surveyId={surveyId} tenantId="tenant-a" />);
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

    const view = renderWithQuery(
      <PublishPage actorId="author-1" api={api} surveyId={surveyId} tenantId="tenant-a" />,
    );
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

  test.each([
    {
      name: 'manual review alone',
      manualReviewAt: '2026-10-08T02:00:00Z',
      orphanEngineSid: null,
      warning: '需要人工复核',
      absentWarning: /孤儿问卷/,
    },
    {
      name: 'orphan engine survey alone',
      manualReviewAt: null,
      orphanEngineSid: 918273,
      warning: /孤儿问卷.*918273/,
      absentWarning: '需要人工复核',
    },
  ])('blocks publishing for $name', async ({ manualReviewAt, orphanEngineSid, warning, absentWarning }) => {
    const api = standardApi({
      [`GET /v1/resource-capabilities?resourceId=${surveyId}`]: () => ({
        ...baseCapabilities,
        canPublishDirectly: true,
      }),
      [`GET /v1/surveys/${surveyId}/approval-requests`]: () => [
        { ...baseApproval, status: 'approved' },
      ],
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
          orphanEngineSid,
          tries: 7,
          nextReconcileAt: null,
          manualReviewAt,
        },
      }),
    });

    renderWithQuery(<PublishPage actorId="author-1" api={api} surveyId={surveyId} tenantId="tenant-a" />);

    expect(await screen.findByText(warning)).toBeInTheDocument();
    expect(screen.queryByText(absentWarning)).not.toBeInTheDocument();
    expect(screen.queryByText('回滚没有完成')).not.toBeInTheDocument();
    expect(screen.getByText('发布操作已阻止，请先完成上述人工处理。')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '发布问卷' })).not.toBeInTheDocument();
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
                failures: [
                  'E_MISSING_ANSWERS groups[0].questions[1].answers: 至少需要一个选项',
                  'E_DUPLICATE_UUID groups[].uuid: uuid 重复',
                  'E_QUESTION_CODE_DUPLICATE questions[44444444-4444-4444-8444-444444444444].code: 题目代码重复',
                ],
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

    renderWithQuery(<PublishPage actorId="author-1" api={api} surveyId={surveyId} tenantId="tenant-a" />);
    fireEvent.click(await screen.findByRole('button', { name: '发布问卷' }));

    expect(await screen.findByText('定义校验')).toBeInTheDocument();
    expect(screen.getByText('题目缺少选项（groups[0].questions[1].answers）')).toBeInTheDocument();
    expect(screen.getByText('题目或题组标识重复（groups[].uuid）')).toBeInTheDocument();
    expect(screen.getByText('题目代码重复（questions[44444444-4444-4444-8444-444444444444].code）')).toBeInTheDocument();
    expect(screen.queryByText(/至少需要一个选项|uuid 重复/)).not.toBeInTheDocument();
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

    renderWithQuery(<PublishPage actorId="author-1" api={api} surveyId={surveyId} tenantId="tenant-a" />);

    expect(await screen.findByText('发布失败')).toBeInTheDocument();
    expect(screen.getByText('未知阶段')).toBeInTheDocument();
    expect(screen.getByText('发布失败，详细信息已隐藏。')).toBeInTheDocument();
    expect(screen.queryByText(/raw-gateway-secret/)).not.toBeInTheDocument();
    expect(screen.queryByText(/IllegalStateException/)).not.toBeInTheDocument();
  });

  test.each([
    { name: 'known code with an arbitrary second token', failedStage: 'validate', failure: 'E_MISSING_ANSWERS RAW_GATEWAY_SECRET' },
    { name: 'unknown validation code', failedStage: 'validate', failure: 'E_UNKNOWN groups[0].questions[1]: 无法校验' },
    { name: 'malformed validation path', failedStage: 'validate', failure: 'E_MISSING_ANSWERS groups[0]..questions[1].answers: 至少需要一个选项' },
    { name: 'overlong validation path', failedStage: 'validate', failure: `E_MISSING_ANSWERS groups[0].${'questions.'.repeat(30)}answers: 至少需要一个选项` },
    { name: 'extra line structure', failedStage: 'validate', failure: 'E_MISSING_ANSWERS groups[0].questions[1].answers: 至少需要一个选项\nRAW_GATEWAY_SECRET' },
    { name: 'NEL U+0085 separator', failedStage: 'validate', failure: 'E_MISSING_ANSWERS groups[0].questions[1].answers: 第一行\u0085第二行' },
    { name: 'line separator U+2028', failedStage: 'validate', failure: 'E_MISSING_ANSWERS groups[0].questions[1].answers: 第一行\u2028第二行' },
    { name: 'paragraph separator U+2029', failedStage: 'validate', failure: 'E_MISSING_ANSWERS groups[0].questions[1].answers: 第一行\u2029第二行' },
    { name: 'secret-like validation content', failedStage: 'validate', failure: 'E_MISSING_ANSWERS groups[0].questions[1].answers: bearer gateway-secret' },
    { name: 'allowlisted code at a non-validation stage', failedStage: 'compile', failure: 'E_MISSING_ANSWERS groups[0].questions[1].answers: 至少需要一个选项' },
  ])('hides $name', async ({ failedStage, failure }) => {
    const api = standardApi({
      [`GET /v1/surveys/${surveyId}`]: () => ({
        ...baseSurvey,
        status: 'publish_failed',
        lastPublish: {
          requestId,
          engineInstanceId: 'test-engine',
          draftVersion: 4,
          outcome: 'failed',
          gatewayStatus: 422,
          failedStage,
          failures: [failure],
          orphanEngineSid: null,
          tries: 1,
          nextReconcileAt: null,
          manualReviewAt: null,
        },
      }),
    });

    renderWithQuery(<PublishPage actorId="author-1" api={api} surveyId={surveyId} tenantId="tenant-a" />);

    expect(await screen.findByText('发布失败，详细信息已隐藏。')).toBeInTheDocument();
    expect(screen.queryByText(/RAW_GATEWAY_SECRET|gateway-secret|至少需要一个选项|无法校验/)).not.toBeInTheDocument();
  });

  test.each([
    {
      name: 'none allows submit but not direct publish',
      approval: null,
      actorId: 'author-1',
      capabilities: { ...baseCapabilities, canApprovePublish: false },
      shown: ['提交审批'],
      hidden: ['发布问卷', '批准申请', '驳回申请', '撤回申请'],
    },
    {
      name: 'pending approver who is not applicant may decide but not withdraw',
      approval: baseApproval,
      actorId: 'approver-1',
      capabilities: { ...baseCapabilities, canSubmitApproval: false },
      shown: ['批准申请', '驳回申请'],
      hidden: ['提交审批', '发布问卷', '撤回申请'],
    },
    {
      name: 'pending applicant without approval grant may only withdraw',
      approval: baseApproval,
      actorId: 'author-1',
      capabilities: { ...baseCapabilities, canSubmitApproval: true, canPublishDirectly: false, canApprovePublish: false },
      shown: ['撤回申请'],
      hidden: ['提交审批', '发布问卷', '批准申请', '驳回申请'],
    },
    {
      name: 'approved ordinary publisher may publish through the approved path',
      approval: { ...baseApproval, status: 'approved' },
      actorId: 'publisher-1',
      capabilities: { ...baseCapabilities, canSubmitApproval: true, canPublishDirectly: false, canApprovePublish: false },
      shown: ['发布问卷'],
      hidden: ['提交审批', '批准申请', '驳回申请', '撤回申请'],
    },
    {
      name: 'published exposes no further action',
      approval: { ...baseApproval, status: 'published' },
      actorId: 'publisher-1',
      capabilities: { ...baseCapabilities, canSubmitApproval: true, canPublishDirectly: true, canApprovePublish: true },
      shown: [],
      hidden: ['提交审批', '发布问卷', '批准申请', '驳回申请', '撤回申请'],
    },
    ...(['rejected', 'withdrawn', 'voided'] as const).map((status) => ({
      name: `${status} allows a new submission`,
      approval: { ...baseApproval, status },
      actorId: 'author-1',
      capabilities: { ...baseCapabilities, canSubmitApproval: true, canPublishDirectly: false, canApprovePublish: false },
      shown: ['提交审批'],
      hidden: ['发布问卷', '批准申请', '驳回申请', '撤回申请'],
    })),
    {
      name: 'direct publisher may publish without submitting approval',
      approval: null,
      actorId: 'direct-publisher',
      capabilities: { ...baseCapabilities, canSubmitApproval: false, canPublishDirectly: true, canApprovePublish: false },
      shown: ['发布问卷'],
      hidden: ['提交审批', '批准申请', '驳回申请', '撤回申请'],
    },
  ])('$name', async ({ approval, actorId, capabilities, shown, hidden }) => {
    const api = standardApi({
      [`GET /v1/resource-capabilities?resourceId=${surveyId}`]: () => capabilities,
      [`GET /v1/surveys/${surveyId}/approval-requests`]: () => approval ? [approval] : [],
    });

    renderWithQuery(<PublishPage actorId={actorId} api={api} surveyId={surveyId} tenantId="tenant-a" />);

    await screen.findByRole('heading', { name: '可执行操作' });
    for (const name of shown) expect(screen.getByRole('button', { name })).toBeInTheDocument();
    for (const name of hidden) expect(screen.queryByRole('button', { name })).not.toBeInTheDocument();
  });

  test('allowsSubmittingANewDraftAfterThePreviousDraftWasPublished', async () => {
    const api = standardApi({
      [`GET /v1/surveys/${surveyId}`]: () => ({ ...baseSurvey, draftVersion: 5 }),
      [`GET /v1/resource-capabilities?resourceId=${surveyId}`]: () => ({
        ...baseCapabilities,
        canApprovePublish: false,
      }),
      [`GET /v1/surveys/${surveyId}/approval-requests`]: () => [
        { ...baseApproval, status: 'published' },
      ],
    });

    renderWithQuery(
      <PublishPage actorId="author-1" api={api} surveyId={surveyId} tenantId="tenant-a" />,
    );

    expect(await screen.findByRole('button', { name: '提交审批' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '发布问卷' })).not.toBeInTheDocument();
  });

  test('doesNotOfferActionsForADirectlyPublishedCurrentDraft', async () => {
    const api = standardApi({
      [`GET /v1/surveys/${surveyId}`]: () => ({
        ...baseSurvey,
        status: 'published',
        publishedVersion: 1,
      }),
      [`GET /v1/resource-capabilities?resourceId=${surveyId}`]: () => ({
        ...baseCapabilities,
        canPublishDirectly: true,
      }),
      [`GET /v1/surveys/${surveyId}/approval-requests`]: () => [],
      [`GET /v1/surveys/${surveyId}/versions`]: () => [
        { ...baseVersion, version: 1, draftVersion: baseSurvey.draftVersion },
      ],
    });

    renderWithQuery(
      <PublishPage actorId="publisher-1" api={api} surveyId={surveyId} tenantId="tenant-a" />,
    );

    await screen.findByRole('heading', { name: '可执行操作' });
    expect(screen.queryByRole('button', { name: '提交审批' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '发布问卷' })).not.toBeInTheDocument();
  });

  test('announcesPublishStatusChangesToAssistiveTechnology', async () => {
    renderWithQuery(
      <PublishPage actorId="author-1" api={standardApi()} surveyId={surveyId} tenantId="tenant-a" />,
    );

    expect(await screen.findByRole('status')).toHaveAttribute('aria-live', 'polite');
  });
});

test('showsPublishedVersionsAsImmutableReadOnlyData', async () => {
  const api = apiFrom((request) => {
    if ((request.method ?? 'GET') === 'GET' && request.path === `/v1/surveys/${surveyId}/versions/2`) {
      return baseVersion;
    }
    throw new Error(`Unhandled request: ${request.path}`);
  });

  renderWithQuery(
    <VersionDetailPage api={api} surveyId={surveyId} tenantId="tenant-a" version={2} />,
  );

  expect(await screen.findByRole('heading', { name: '已发布版本 2' })).toBeInTheDocument();
  expect(screen.getByText('当前在线')).toBeInTheDocument();
  expect(screen.getByText('test-engine / 876543')).toBeInTheDocument();
  expect(screen.getByText('Q1')).toBeInTheDocument();
  expect(screen.getByText('876543X1X1Q1')).toBeInTheDocument();
  expect(screen.getByText(/员工体验调查（发布快照）/)).toBeInTheDocument();
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /保存|编辑|恢复/ })).not.toBeInTheDocument();
});

test('registersPublishAndImmutableVersionRoutesUnderProtectedShell', () => {
  const routes = createAppRoutes(false);
  const protectedRoute = routes.find((route) => route.children?.some((child) => child.children));
  const appShell = protectedRoute?.children?.find((route) => route.children);
  const protectedPaths = appShell?.children?.map((route) => route.path).filter(Boolean);

  expect(protectedPaths).toEqual(expect.arrayContaining([
    'surveys/:surveyId/publish',
    'surveys/:surveyId/versions/:version',
  ]));
  expect(routes.map((route) => route.path).filter(Boolean)).not.toEqual(expect.arrayContaining([
    'surveys/:surveyId/publish',
    'surveys/:surveyId/versions/:version',
  ]));
});
