import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ArrowLeft, ExternalLink, Monitor, Play, RotateCw, Smartphone, Square } from 'lucide-react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { z } from 'zod';
import { surveyWorkflowHref, useSurveyShell } from '../../app/SurveyShell';
import { useAuth } from '../auth/AuthProvider';
import { parseDefinition } from '../editor/model/definition';
import type { ApiClient } from '../../shared/api/http';
import { getSurveyDraft, surveyDraftQueryKey } from '../../shared/api/surveys';
import {
  createPreviewClient,
  previewSessionQueryKey,
  type PreviewClient,
  type PreviewSessionView,
} from '../../shared/api/previews';
import { DraftRenderer } from './DraftRenderer';
import './preview.css';

interface PreviewPageProps {
  api: ApiClient;
  previewClient: PreviewClient;
  surveyId: string;
  tenantId: string;
}

type PreviewMode = 'desktop' | 'mobile';

export function PreviewPage(props: PreviewPageProps) {
  return <PreviewPageInstance key={`${props.tenantId}:${props.surveyId}`} {...props} />;
}

function PreviewPageInstance({ api, previewClient, surveyId, tenantId }: PreviewPageProps) {
  const [mode, setMode] = useState<PreviewMode>('desktop');
  const [session, setSession] = useState<PreviewSessionView | null>(null);
  const [sessionHistory, setSessionHistory] = useState<PreviewSessionView[]>([]);
  const [requestId, setRequestId] = useState<string | null>(null);
  const [action, setAction] = useState<'creating' | 'closing' | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [clock, setClock] = useState(() => Date.now());
  const actionRef = useRef(false);
  const surveyShell = useSurveyShell();
  const [searchParams] = useSearchParams();
  const selectedQuestion = searchParams.get('question');
  const draftQuery = useQuery({
    queryKey: surveyDraftQueryKey(tenantId, surveyId),
    queryFn: ({ signal }) => getSurveyDraft(api, surveyId, signal),
  });
  const lifecycleQuery = useQuery({
    queryKey: previewSessionQueryKey(tenantId, surveyId, session?.id ?? 'pending'),
    queryFn: ({ signal }) => previewClient.get(session!.id, signal),
    enabled: Boolean(session && (session.status === 'creating' || session.status === 'closing')),
    retry: false,
    refetchInterval: (query) => {
      const current = query.state.data ?? session;
      return current?.status === 'creating' || current?.status === 'closing' ? 1_000 : false;
    },
  });
  const sessionPending = session?.status === 'creating' || session?.status === 'closing';
  const displayedSession = sessionPending ? (lifecycleQuery.data ?? session) : session;
  const sessionExpired = displayedSession?.status === 'ready'
    && Date.parse(displayedSession.expiresAt) <= clock;

  useEffect(() => {
    if (!displayedSession || displayedSession.status !== 'ready') return undefined;
    const remaining = Date.parse(displayedSession.expiresAt) - Date.now();
    if (remaining <= 0) return undefined;
    const timer = window.setTimeout(() => setClock(Date.now()), Math.min(remaining, 2_147_483_647));
    return () => window.clearTimeout(timer);
  }, [displayedSession]);

  const createRealPreview = async (newOperation: boolean) => {
    if (actionRef.current) return;
    actionRef.current = true;
    if (newOperation && displayedSession) {
      setSessionHistory((history) => appendSession(history, displayedSession));
      setSession(null);
    }
    const stableRequestId = newOperation || !requestId ? crypto.randomUUID() : requestId;
    setRequestId(stableRequestId);
    setAction('creating');
    setActionError(null);
    try {
      setSession(await previewClient.create(surveyId, {
        requestId: stableRequestId,
        ttlSeconds: 1_800,
      }));
      setClock(Date.now());
    } catch {
      setActionError('真实预览创建失败，请使用同一请求重试。');
    } finally {
      actionRef.current = false;
      setAction(null);
    }
  };

  if (draftQuery.isPending) return <p>正在加载快速预览</p>;
  if (draftQuery.isError || !draftQuery.data) return <p role="alert">快速预览暂时不可用。</p>;

  let definition;
  try {
    definition = parseDefinition(draftQuery.data.definition);
  } catch {
    return <p role="alert">草稿格式无法预览。</p>;
  }

  return (
    <main className="preview-page">
      <header className="preview-toolbar">
        {!surveyShell ? (
          <div>
            <p>只读视图</p>
            <h1>快速预览</h1>
          </div>
        ) : <h2 className="sr-only">快速预览</h2>}
        <div className="preview-toolbar-actions">
          {!surveyShell ? (
            <Link to={surveyWorkflowHref(surveyId, 'edit', selectedQuestion)}>
              <ArrowLeft aria-hidden="true" />
              返回编辑
            </Link>
          ) : null}
          <div className="preview-segments" role="group" aria-label="预览设备">
            <button
              type="button"
              aria-pressed={mode === 'desktop'}
              onClick={() => setMode('desktop')}
            >
              <Monitor aria-hidden="true" />
              桌面端
            </button>
            <button
              type="button"
              aria-pressed={mode === 'mobile'}
              onClick={() => setMode('mobile')}
            >
              <Smartphone aria-hidden="true" />
              移动端
            </button>
          </div>
        </div>
      </header>
      <RealPreviewPanel
        action={action}
        error={actionError}
        lifecycleError={lifecycleQuery.isError}
        requestId={requestId}
        session={displayedSession}
        sessionExpired={sessionExpired}
        sessionHistory={sessionHistory}
        now={clock}
        onCreate={(newOperation) => void createRealPreview(newOperation)}
        onRetryStatus={() => void lifecycleQuery.refetch()}
        onClose={async () => {
          if (!displayedSession || actionRef.current) return;
          actionRef.current = true;
          setAction('closing');
          setActionError(null);
          try {
            setSession(await previewClient.close(displayedSession.id));
          } catch {
            setActionError('结束真实预览失败，请稍后重试。');
          } finally {
            actionRef.current = false;
            setAction(null);
          }
        }}
      />
      <div className="preview-stage">
        <div
          className="draft-preview-frame"
          data-testid="draft-preview-frame"
          data-preview-mode={mode}
          style={{ width: mode === 'desktop' ? '960px' : '390px' }}
        >
          <div className="draft-preview-label">快速预览 · 本地草稿渲染，不会提交答案</div>
          <DraftRenderer definition={definition} />
        </div>
      </div>
    </main>
  );
}

interface RealPreviewPanelProps {
  action: 'creating' | 'closing' | null;
  error: string | null;
  lifecycleError: boolean;
  requestId: string | null;
  session: PreviewSessionView | null;
  sessionExpired: boolean;
  sessionHistory: PreviewSessionView[];
  now: number;
  onCreate(newOperation: boolean): void;
  onClose(): void;
  onRetryStatus(): void;
}

function RealPreviewPanel({
  action,
  error,
  lifecycleError,
  now,
  requestId,
  session,
  sessionExpired,
  sessionHistory,
  onCreate,
  onClose,
  onRetryStatus,
}: RealPreviewPanelProps) {
  const status = session?.status;
  const previewUrl = session?.previewUrl ?? null;
  const canOpen = status === 'ready' && !sessionExpired && previewUrl !== null;
  const canClose = (status === 'ready' && !sessionExpired) || status === 'cleanup_failed';
  const pending = status === 'creating' || status === 'closing';
  const canStartNew = status === 'closed' || status === 'failed' || sessionExpired;
  return (
    <section className="real-preview-panel" aria-labelledby="real-preview-title">
      <div>
        <p className="real-preview-eyebrow">LimeSurvey 隔离运行时</p>
        <h2 id="real-preview-title">真实预览</h2>
        <p>在独立问卷中验证真实作答体验，不计入正式答卷。</p>
      </div>
      <div className="real-preview-status" aria-live="polite">
        <strong>{previewStatusText(session, action, sessionExpired)}</strong>
        {session ? <span>草稿版本 {session.draftVersion}</span> : null}
        {session && status !== 'closed' ? <span>有效期至 {formatDateTime(session.expiresAt)}</span> : null}
        {requestId && (error || status === 'failed') ? <span>请求编号：{requestId.slice(0, 8)}</span> : null}
        {error ? <p role="alert">{error}</p> : null}
        {status === 'failed' ? <p role="alert">真实预览创建失败，请使用同一请求重试。</p> : null}
        {status === 'cleanup_failed' ? <p role="alert">预览结束未完成，请再次结束。</p> : null}
        {lifecycleError && pending ? (
          <div className="real-preview-query-error" role="alert">
            <p>真实预览状态读取失败，页面将继续尝试。</p>
            <button type="button" onClick={onRetryStatus}>
              <RotateCw aria-hidden="true" />重试查询预览状态
            </button>
          </div>
        ) : null}
      </div>
      <div className="real-preview-actions">
        {!session || error ? (
          <button type="button" disabled={action !== null} onClick={() => onCreate(false)}>
            {error ? <RotateCw aria-hidden="true" /> : <Play aria-hidden="true" />}
            {error ? '重试真实预览' : '创建真实预览'}
          </button>
        ) : null}
        {status === 'failed' ? (
          <button type="button" disabled={action !== null} onClick={() => onCreate(false)}>
            <RotateCw aria-hidden="true" />重试同一请求
          </button>
        ) : null}
        {canStartNew ? (
          <button type="button" disabled={action !== null} onClick={() => onCreate(true)}>
            <Play aria-hidden="true" />再次创建真实预览
          </button>
        ) : null}
        {canOpen ? (
          <button
            type="button"
            onClick={() => window.open(previewUrl, '_blank', 'noopener,noreferrer')}
          >
            <ExternalLink aria-hidden="true" />
            在新窗口打开真实预览
          </button>
        ) : null}
        {canClose ? (
          <button type="button" disabled={action !== null} onClick={onClose}>
            {status === 'cleanup_failed' ? <RotateCw aria-hidden="true" /> : <Square aria-hidden="true" />}
            {status === 'cleanup_failed' ? '再次结束真实预览' : '结束真实预览'}
          </button>
        ) : null}
      </div>
      {sessionHistory.length ? (
        <section className="real-preview-history" role="region" aria-label="真实预览历史">
          <h3>预览历史</h3>
          <ul>{sessionHistory.map((item) => (
            <li key={item.id}>
              <span>{item.id.slice(0, 8)}</span>
              <strong>{previewStatusText(item, null, Date.parse(item.expiresAt) <= now)}</strong>
              <time dateTime={item.updatedAt}>{formatDateTime(item.updatedAt)}</time>
            </li>
          ))}</ul>
        </section>
      ) : null}
    </section>
  );
}

function previewStatusText(
  session: PreviewSessionView | null,
  action: RealPreviewPanelProps['action'],
  expired = false,
) {
  if (action === 'creating' || session?.status === 'creating') return '正在创建隔离预览';
  if (action === 'closing' || session?.status === 'closing') return '正在结束真实预览';
  if (expired) return '真实预览已过期';
  if (session?.status === 'ready') return '真实预览已就绪';
  if (session?.status === 'closed') return '真实预览已结束';
  if (session?.status === 'failed') return '真实预览创建失败';
  if (session?.status === 'cleanup_failed') return '真实预览结束异常';
  return '尚未创建';
}

function appendSession(history: PreviewSessionView[], session: PreviewSessionView) {
  return history.some((item) => item.id === session.id) ? history : [...history, session];
}

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(new Date(value));
}

export function PreviewRoutePage() {
  const { api, logout, session } = useAuth();
  const surveyId = z.string().uuid().safeParse(useParams().surveyId);
  const previewClient = useMemo(() => createPreviewClient({
    getToken: () => session?.token ?? null,
    onUnauthorized: () => logout(),
  }), [logout, session?.token]);
  if (!surveyId.success || !session) return <p role="alert">问卷标识无效</p>;
  return (
    <PreviewPage
      api={api}
      previewClient={previewClient}
      surveyId={surveyId.data}
      tenantId={session.me.tenantId}
    />
  );
}
