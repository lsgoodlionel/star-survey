import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Send, ShieldCheck, Undo2, XCircle } from 'lucide-react';
import { Link, useInRouterContext, useParams } from 'react-router-dom';
import { useAuth } from '../auth/AuthProvider';
import { ApiError } from '../../shared/api/errors';
import type { ApiClient } from '../../shared/api/http';
import {
  approvalRequestsQueryKey,
  approveRequest,
  getSurveyOverview,
  listApprovalRequests,
  listPublishedVersions,
  publishSurvey,
  rejectRequest,
  submitApproval,
  surveyOverviewQueryKey,
  versionsQueryKey,
  withdrawRequest,
  type ApprovalRequest,
  type ApprovalStatus,
  type SurveyOverview,
} from '../../shared/api/approvals';
import { surveyCapabilitiesQueryKey } from '../../shared/api/surveys';
import {
  getResourceCapabilities,
  type ResourceCapabilities,
} from '../../shared/api/resources';
import { ApprovalTimeline } from './ApprovalTimeline';
import { PublishStatus } from './PublishStatus';
import { parseSurveyIdParam } from './routeParams';
import './publish.css';

interface PublishPageProps {
  actorId: string;
  api: ApiClient;
  surveyId: string;
  tenantId: string;
}

type ApprovalState = ApprovalStatus | 'none';

interface ApprovalStateRule {
  canDecide: boolean;
  canDirectPublish: boolean;
  canPublishApproved: boolean;
  canSubmit: boolean;
  canWithdraw: boolean;
}

const approvalActionStateTable: Record<ApprovalState, ApprovalStateRule> = {
  none: { canDecide: false, canDirectPublish: true, canPublishApproved: false, canSubmit: true, canWithdraw: false },
  pending: { canDecide: true, canDirectPublish: true, canPublishApproved: false, canSubmit: false, canWithdraw: true },
  approved: { canDecide: false, canDirectPublish: true, canPublishApproved: true, canSubmit: false, canWithdraw: true },
  rejected: { canDecide: false, canDirectPublish: true, canPublishApproved: false, canSubmit: true, canWithdraw: false },
  withdrawn: { canDecide: false, canDirectPublish: true, canPublishApproved: false, canSubmit: true, canWithdraw: false },
  voided: { canDecide: false, canDirectPublish: true, canPublishApproved: false, canSubmit: true, canWithdraw: false },
  published: { canDecide: false, canDirectPublish: false, canPublishApproved: false, canSubmit: false, canWithdraw: false },
};

type ApprovalAction =
  | { kind: 'submit'; draftVersion: number }
  | { kind: 'approve' | 'withdraw'; approvalId: string }
  | { kind: 'reject'; approvalId: string; reason: string };

export function PublishPage({ actorId, api, surveyId, tenantId }: PublishPageProps) {
  const queryClient = useQueryClient();
  const visible = useDocumentVisibility();
  const [actionError, setActionError] = useState<ApiError | null>(null);
  const [awaitingPublishResult, setAwaitingPublishResult] = useState(false);
  const [showRejectForm, setShowRejectForm] = useState(false);
  const [rejectReason, setRejectReason] = useState('');

  const overview = useQuery({
    queryKey: surveyOverviewQueryKey(tenantId, surveyId),
    queryFn: ({ signal }) => getSurveyOverview(api, surveyId, signal),
    refetchInterval: (query) => shouldPoll(query.state.data, awaitingPublishResult, visible) ? 2_000 : false,
  });
  const approvals = useQuery({
    queryKey: approvalRequestsQueryKey(tenantId, surveyId),
    queryFn: ({ signal }) => listApprovalRequests(api, surveyId, signal),
  });
  const capabilities = useQuery({
    queryKey: surveyCapabilitiesQueryKey(tenantId, surveyId),
    queryFn: ({ signal }) => getResourceCapabilities(api, surveyId, signal),
  });
  const versions = useQuery({
    queryKey: versionsQueryKey(tenantId, surveyId),
    queryFn: ({ signal }) => listPublishedVersions(api, surveyId, signal),
  });

  const latestApproval = useMemo(() => approvals.data?.at(-1) ?? null, [approvals.data]);

  useEffect(() => () => {
    void queryClient.cancelQueries({
      queryKey: surveyOverviewQueryKey(tenantId, surveyId),
      exact: true,
    });
  }, [queryClient, surveyId, tenantId]);

  const approvalMutation = useMutation({
    mutationFn: (action: ApprovalAction) => runApprovalAction(api, surveyId, action),
    onMutate: () => {
      setActionError(null);
      setAwaitingPublishResult(false);
    },
    onSuccess: () => invalidatePublishQueries(queryClient, tenantId, surveyId),
    onError: (error) => setActionError(publicApiError(error)),
  });

  const publishMutation = useMutation({
    mutationFn: () => publishSurvey(api, surveyId),
    onMutate: () => {
      setActionError(null);
      setAwaitingPublishResult(false);
    },
    onError: (error) => {
      const apiError = publicApiError(error);
      if (apiError.kind === 'pending') setAwaitingPublishResult(true);
      else setActionError(apiError);
    },
    onSettled: () => invalidatePublishQueries(queryClient, tenantId, surveyId),
  });

  if (overview.isPending || approvals.isPending || capabilities.isPending || versions.isPending) return <p className="publish-loading">正在加载发布信息</p>;
  if (overview.error || approvals.error || capabilities.error || versions.error || !overview.data || !approvals.data || !capabilities.data) {
    return <p role="alert">发布信息暂时不可用，请稍后重试。</p>;
  }

  const currentApproval = latestApproval?.draftVersion === overview.data.draftVersion
    ? latestApproval
    : null;
  const busy = approvalMutation.isPending || publishMutation.isPending;
  const publicationBlocked = overview.data.lastPublish?.manualReviewAt != null
    || overview.data.lastPublish?.orphanEngineSid != null;

  return (
    <main className="publish-page">
      <header className="publish-page__header">
        <div><p className="publish-eyebrow">问卷发布</p><h1>{overview.data.title}</h1></div>
        <span className="publish-version-count">已发布 {versions.data?.length ?? 0} 个版本</span>
      </header>

      <PublishStatus survey={overview.data} awaitingPublishResult={awaitingPublishResult} />

      {actionError ? (
        <p className="publish-action-error" role="alert">
          {actionError.message}{actionError.traceId ? `（追踪编号：${actionError.traceId}）` : ''}
        </p>
      ) : null}

      <section className="publish-actions" aria-labelledby="publish-actions-title">
        <div><h2 id="publish-actions-title">可执行操作</h2><p>最终授权由服务端判定。</p></div>
        <div className="publish-actions__buttons">
          <ApprovalButtons
            approval={currentApproval}
            actorId={actorId}
            busy={busy}
            capabilities={capabilities.data}
            draftAlreadyPublished={overview.data.publishedVersion === overview.data.draftVersion}
            draftVersion={overview.data.draftVersion}
            onAction={(action) => approvalMutation.mutate(action)}
            onReject={() => setShowRejectForm(true)}
            onPublish={() => publishMutation.mutate()}
            publicationBlocked={publicationBlocked}
          />
        </div>
      </section>

      {showRejectForm && currentApproval?.status === 'pending' ? (
        <form
          className="reject-form"
          onSubmit={(event) => {
            event.preventDefault();
            const reason = rejectReason.trim();
            if (!reason) return;
            approvalMutation.mutate({ kind: 'reject', approvalId: currentApproval.id, reason });
            setShowRejectForm(false);
          }}
        >
          <label htmlFor="reject-reason">驳回原因</label>
          <input id="reject-reason" value={rejectReason} onChange={(event) => setRejectReason(event.target.value)} required maxLength={500} />
          <button type="submit" disabled={busy}>确认驳回</button>
          <button type="button" onClick={() => setShowRejectForm(false)}>取消</button>
        </form>
      ) : null}

      <section className="approval-section" aria-labelledby="approval-history-title">
        <h2 id="approval-history-title">审批记录</h2>
        <ApprovalTimeline approvals={approvals.data} />
      </section>

      <section className="published-versions" aria-labelledby="published-versions-title">
        <h2 id="published-versions-title">已发布版本</h2>
        {versions.data?.length ? (
          <ol>
            {versions.data.map((version) => (
              <li key={version.version}>
                <div>
                  <strong>版本 {version.version}</strong>
                  <span>{version.live ? '当前在线' : '历史版本'}</span>
                </div>
                <VersionLink surveyId={surveyId} version={version.version} />
              </li>
            ))}
          </ol>
        ) : <p className="publish-empty">尚无已发布版本</p>}
      </section>
    </main>
  );
}

function VersionLink({ surveyId, version }: { surveyId: string; version: number }) {
  const inRouter = useInRouterContext();
  const href = `/surveys/${surveyId}/versions/${version}`;
  if (inRouter) return <Link to={href}>查看版本 {version}</Link>;
  return <a href={href}>查看版本 {version}</a>;
}

export function PublishRoutePage() {
  const { api, session } = useAuth();
  const params = useParams();
  const surveyId = parseSurveyIdParam(params.surveyId);
  if (!session || !surveyId) return <p role="alert">问卷标识无效</p>;
  return (
    <PublishPage
      actorId={session.me.actorId}
      api={api}
      surveyId={surveyId}
      tenantId={session.me.tenantId}
    />
  );
}

function ApprovalButtons({ approval, actorId, busy, capabilities, draftAlreadyPublished, draftVersion, onAction, onReject, onPublish, publicationBlocked }: {
  approval: ApprovalRequest | null;
  actorId: string;
  busy: boolean;
  capabilities: ResourceCapabilities;
  draftAlreadyPublished: boolean;
  draftVersion: number;
  onAction: (action: ApprovalAction) => void;
  onReject: () => void;
  onPublish: () => void;
  publicationBlocked: boolean;
}) {
  const state: ApprovalState = draftAlreadyPublished ? 'published' : approval?.status ?? 'none';
  const rule = approvalActionStateTable[state];
  const canSubmit = rule.canSubmit && capabilities.canSubmitApproval;
  const canDecide = rule.canDecide && capabilities.canApprovePublish;
  const canWithdraw = Boolean(approval && rule.canWithdraw && approval.applicant === actorId);
  const canPublish = !publicationBlocked && (
    (rule.canDirectPublish && capabilities.canPublishDirectly)
    || (rule.canPublishApproved && capabilities.canSubmitApproval)
  );

  return <>
    {canSubmit ? <button type="button" disabled={busy} onClick={() => onAction({ kind: 'submit', draftVersion })}><ShieldCheck size={17} aria-hidden="true" />提交审批</button> : null}
    {canDecide && approval ? <button type="button" disabled={busy} onClick={() => onAction({ kind: 'approve', approvalId: approval.id })}><ShieldCheck size={17} aria-hidden="true" />批准申请</button> : null}
    {canDecide ? <button type="button" disabled={busy} onClick={onReject}><XCircle size={17} aria-hidden="true" />驳回申请</button> : null}
    {canWithdraw && approval ? <button type="button" disabled={busy} onClick={() => onAction({ kind: 'withdraw', approvalId: approval.id })}><Undo2 size={17} aria-hidden="true" />撤回申请</button> : null}
    {canPublish ? <button type="button" disabled={busy} onClick={onPublish}><Send size={17} aria-hidden="true" />发布问卷</button> : null}
    {publicationBlocked ? <p className="publish-blocked">发布操作已阻止，请先完成上述人工处理。</p> : null}
  </>;
}

function runApprovalAction(api: ApiClient, surveyId: string, action: ApprovalAction) {
  switch (action.kind) {
    case 'submit': return submitApproval(api, surveyId, action.draftVersion);
    case 'approve': return approveRequest(api, action.approvalId);
    case 'reject': return rejectRequest(api, action.approvalId, action.reason);
    case 'withdraw': return withdrawRequest(api, action.approvalId);
  }
}

async function invalidatePublishQueries(
  queryClient: ReturnType<typeof useQueryClient>,
  tenantId: string,
  surveyId: string,
) {
  await Promise.all([
    queryClient.invalidateQueries({
      queryKey: surveyOverviewQueryKey(tenantId, surveyId),
      exact: true,
    }),
    queryClient.invalidateQueries({
      queryKey: approvalRequestsQueryKey(tenantId, surveyId),
      exact: true,
    }),
    queryClient.invalidateQueries({ queryKey: versionsQueryKey(tenantId, surveyId) }),
  ]);
}

function publicApiError(error: unknown) {
  return error instanceof ApiError ? error : new ApiError('unexpected', '操作失败，请稍后重试');
}

function shouldPoll(survey: SurveyOverview | undefined, awaiting: boolean, visible: boolean) {
  if (!visible || survey?.lastPublish?.manualReviewAt) return false;
  if (survey && isPollingTerminal(survey)) return false;
  return awaiting || survey?.status === 'publishing' || survey?.status === 'pending_reconciliation';
}

function isPollingTerminal(survey: SurveyOverview) {
  return survey.status === 'published' || survey.status === 'publish_failed' || survey.lastPublish?.manualReviewAt != null;
}

function useDocumentVisibility() {
  const [visible, setVisible] = useState(() => document.visibilityState !== 'hidden');
  useEffect(() => {
    const update = () => setVisible(document.visibilityState !== 'hidden');
    document.addEventListener('visibilitychange', update);
    return () => document.removeEventListener('visibilitychange', update);
  }, []);
  return visible;
}
