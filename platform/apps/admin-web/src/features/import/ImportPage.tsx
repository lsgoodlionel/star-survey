import { useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, FileSearch, Import } from 'lucide-react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { z } from 'zod';
import {
  surveyWorkflowHref,
  useSurveyPageReady,
  useSurveyShell,
} from '../../app/surveyShellContext';
import { useAuth } from '../auth/AuthProvider';
import { parseDefinition } from '../editor/model/definition';
import { ApiError } from '../../shared/api/errors';
import type { ApiClient } from '../../shared/api/http';
import {
  confirmSurveyImport,
  previewSurveyImport,
  type ImportPreview,
} from '../../shared/api/imports';
import {
  approvalRequestsQueryKey,
  surveyOverviewQueryKey,
  versionsQueryKey,
} from '../../shared/api/approvals';
import { getResourceCapabilities } from '../../shared/api/resources';
import {
  getSurveyDraft,
  surveyCapabilitiesQueryKey,
  surveyDraftQueryKey,
} from '../../shared/api/surveys';
import { ImportPreviewTable } from './ImportPreviewTable';
import './import.css';

interface ImportPageProps {
  api: ApiClient;
  surveyId: string;
  tenantId: string;
}

interface PreviewRequest {
  identity: string;
  revision: number;
  text: string;
}

export function ImportPage(props: ImportPageProps) {
  return <ImportPageInstance key={`${props.tenantId}:${props.surveyId}`} {...props} />;
}

function ImportPageInstance({ api, surveyId, tenantId }: ImportPageProps) {
  const navigate = useNavigate();
  const surveyShell = useSurveyShell();
  const [searchParams] = useSearchParams();
  const selectedQuestion = searchParams.get('question');
  const queryClient = useQueryClient();
  const [text, setText] = useState('');
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [selected, setSelected] = useState<Set<number>>(() => new Set());
  const [groupUuid, setGroupUuid] = useState('');
  const [actionError, setActionError] = useState<string | null>(null);
  const sourceRevisionRef = useRef(0);
  const identity = `${tenantId}:${surveyId}`;
  const draftQuery = useQuery({
    queryKey: surveyDraftQueryKey(tenantId, surveyId),
    queryFn: ({ signal }) => getSurveyDraft(api, surveyId, signal),
  });
  const capabilitiesQuery = useQuery({
    queryKey: surveyCapabilitiesQueryKey(tenantId, surveyId),
    queryFn: ({ signal }) => getResourceCapabilities(api, surveyId, signal),
  });
  const definition = useMemo(() => {
    if (!draftQuery.data) return null;
    try {
      return parseDefinition(draftQuery.data.definition);
    } catch {
      return null;
    }
  }, [draftQuery.data]);
  const canEdit = capabilitiesQuery.data?.canEdit === true;
  const ready = !draftQuery.isPending
    && !capabilitiesQuery.isPending
    && !draftQuery.isError
    && !capabilitiesQuery.isError
    && Boolean(draftQuery.data && capabilitiesQuery.data && definition);
  useSurveyPageReady('import', ready);

  const previewMutation = useMutation({
    mutationFn: (request: PreviewRequest) => previewSurveyImport(api, surveyId, request.text),
    onSuccess: (result, request) => {
      if (request.identity !== identity || request.revision !== sourceRevisionRef.current) return;
      setPreview(result);
      setSelected(new Set(result.questions.filter((question) => question.importable).map((question) => question.index)));
      setActionError(null);
    },
    onError: (error, request) => {
      if (request.identity !== identity || request.revision !== sourceRevisionRef.current) return;
      setActionError(messageFor(error, '预览失败，请稍后重试'));
    },
  });

  const importMutation = useMutation({
    mutationFn: async () => {
      if (!draftQuery.data) throw new Error('草稿尚未加载完成');
      return confirmSurveyImport(api, surveyId, {
        expectedVersion: draftQuery.data.version,
        text,
        accept: [...selected].sort((left, right) => left - right),
        groupUuid: groupUuid || null,
      });
    },
    onSuccess: async (updatedDraft) => {
      queryClient.setQueryData(surveyDraftQueryKey(tenantId, surveyId), updatedDraft);
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
      navigate(surveyWorkflowHref(surveyId, 'edit', selectedQuestion), { replace: true });
    },
    onError: async (error) => {
      setActionError(messageFor(error, '导入失败，请稍后重试'));
      if (!(error instanceof ApiError) || error.kind !== 'validation') return;
      try {
        const refreshed = await previewSurveyImport(api, surveyId, text);
        setPreview(refreshed);
      } catch {
        // The validation message remains useful when a diagnostic refresh is unavailable.
      }
    },
  });

  if (draftQuery.isPending || capabilitiesQuery.isPending) return <p>正在加载导入工具</p>;
  if (draftQuery.isError || capabilitiesQuery.isError || !draftQuery.data || !definition) {
    return <p role="alert">问卷草稿暂时不可用，请稍后重试。</p>;
  }

  const selectedCount = selected.size;
  return (
    <main className="survey-import-page">
      {!surveyShell ? (
        <header className="import-header">
          <div>
            <p>问卷编辑</p>
            <h1>批量文本导入</h1>
          </div>
          <div className="import-header-actions">
            <span>当前草稿版本 {draftQuery.data.version}</span>
            <Link to={surveyWorkflowHref(surveyId, 'edit', selectedQuestion)}>
              <ArrowLeft aria-hidden="true" />
              返回编辑
            </Link>
          </div>
        </header>
      ) : (
        <div className="embedded-page-toolbar">当前草稿版本 {draftQuery.data.version}</div>
      )}

      {!canEdit ? <p role="alert">当前账号仅可查看此问卷，不能导入题目。</p> : null}
      <section className="import-source" aria-labelledby="import-source-title">
        <h2 id="import-source-title">原文</h2>
        <label htmlFor="import-text">待导入文本</label>
        <textarea
          id="import-text"
          rows={12}
          value={text}
          disabled={!canEdit || importMutation.isPending}
          onChange={(event) => {
            sourceRevisionRef.current += 1;
            setText(event.target.value);
            setPreview(null);
            setSelected(new Set());
            setActionError(null);
            previewMutation.reset();
          }}
        />
        <button
          type="button"
          disabled={!canEdit || !text.trim() || previewMutation.isPending || importMutation.isPending}
          onClick={() => previewMutation.mutate({
            identity,
            revision: sourceRevisionRef.current,
            text,
          })}
        >
          <FileSearch aria-hidden="true" />
          {previewMutation.isPending ? '正在解析' : '预览导入'}
        </button>
      </section>

      {actionError ? <p className="import-error" role="alert">{actionError}</p> : null}

      {preview ? (
        <section className="import-preview" aria-labelledby="import-preview-title">
          <div className="import-preview-heading">
            <div>
              <h2 id="import-preview-title">解析结果</h2>
              <p>共读取 {preview.lineCount} 行，合法题目已默认选中。</p>
            </div>
            <label>
              导入到题组
              <select
                aria-label="导入到题组"
                value={groupUuid}
                disabled={importMutation.isPending}
                onChange={(event) => setGroupUuid(event.target.value)}
              >
                <option value="">新建“导入的题目”题组</option>
                {definition.groups.map((group) => (
                  <option key={group.uuid} value={group.uuid}>{group.title}</option>
                ))}
              </select>
            </label>
          </div>
          <ImportPreviewTable
            preview={preview}
            selected={selected}
            onSelectionChange={(index, checked) => {
              setSelected((current) => {
                const next = new Set(current);
                if (checked) next.add(index);
                else next.delete(index);
                return next;
              });
            }}
          />
          <div className="import-actions">
            <button
              type="button"
              disabled={!canEdit || selectedCount === 0 || importMutation.isPending}
              onClick={() => importMutation.mutate()}
            >
              <Import aria-hidden="true" />
              {importMutation.isPending ? '正在导入' : `确认导入 ${selectedCount} 道题`}
            </button>
          </div>
        </section>
      ) : null}
    </main>
  );
}

export function ImportRoutePage() {
  const { api, session } = useAuth();
  const surveyId = z.string().uuid().safeParse(useParams().surveyId);
  if (!surveyId.success || !session) return <p role="alert">问卷标识无效</p>;
  return (
    <ImportPage
      key={`${session.me.tenantId}:${session.me.actorId}:${surveyId.data}`}
      api={api}
      surveyId={surveyId.data}
      tenantId={session.me.tenantId}
    />
  );
}

function messageFor(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}
