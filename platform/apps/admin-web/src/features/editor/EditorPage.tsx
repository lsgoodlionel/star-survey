import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Download, Eye, FileInput, RefreshCw, Rocket, Save } from 'lucide-react';
import { Link, useBlocker, useParams, useSearchParams } from 'react-router-dom';
import { z } from 'zod';
import { useAuth } from '../auth/AuthProvider';
import { ApiError } from '../../shared/api/errors';
import type { ApiClient } from '../../shared/api/http';
import { getResourceCapabilities } from '../../shared/api/resources';
import {
  getSurvey,
  getSurveyDraft,
  saveDraft,
  surveyCapabilitiesQueryKey,
  surveyDetailQueryKey,
  surveyDraftQueryKey,
  type DraftView,
  type SurveyView,
} from '../../shared/api/surveys';
import { Outline } from './Outline';
import { PropertiesPanel } from './PropertiesPanel';
import { QuestionEditor } from './QuestionEditor';
import {
  parseDefinition,
  serializeDefinition,
  type EditableSurveyDefinition,
} from './model/definition';
import { moveQuestion, updateQuestion, validateDefinition } from './model/operations';
import {
  clearActiveEditorRecovery,
  discardEditorRecovery,
  registerActiveEditorRecovery,
  takeEditorRecovery,
} from './recovery';
import './editor.css';

interface EditorPageProps {
  api: ApiClient;
  surveyId: string;
  tenantId: string;
}

type MobilePanel = 'outline' | 'editor' | 'properties';

export function EditorPage({ api, surveyId, tenantId }: EditorPageProps) {
  const surveyQuery = useQuery({
    queryKey: surveyDetailQueryKey(tenantId, surveyId),
    queryFn: ({ signal }) => getSurvey(api, surveyId, signal),
  });
  const draftQuery = useQuery({
    queryKey: surveyDraftQueryKey(tenantId, surveyId),
    queryFn: ({ signal }) => getSurveyDraft(api, surveyId, signal),
  });
  const capabilitiesQuery = useQuery({
    queryKey: surveyCapabilitiesQueryKey(tenantId, surveyId),
    queryFn: ({ signal }) => getResourceCapabilities(api, surveyId, signal),
  });

  const loading = surveyQuery.isPending || draftQuery.isPending || capabilitiesQuery.isPending;
  if (loading) return <p className="editor-loading">正在加载问卷草稿</p>;
  if (surveyQuery.error || draftQuery.error || capabilitiesQuery.error || !draftQuery.data) {
    return <p role="alert">问卷草稿暂时不可用，请稍后重试。</p>;
  }

  return (
    <LoadedEditor
      key={`${tenantId}:${surveyId}`}
      api={api}
      surveyId={surveyId}
      tenantId={tenantId}
      surveyTitle={surveyQuery.data?.title ?? ''}
      initialDraft={draftQuery.data}
      canEdit={capabilitiesQuery.data?.canEdit === true}
    />
  );
}

interface LoadedEditorProps extends EditorPageProps {
  canEdit: boolean;
  initialDraft: DraftView;
  surveyTitle: string;
}

function LoadedEditor({ api, canEdit, initialDraft, surveyId, surveyTitle, tenantId }: LoadedEditorProps) {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const [initialState] = useState(() => {
    const recovered = takeEditorRecovery(tenantId, surveyId);
    return {
      definition: parseDefinition(recovered?.definition ?? initialDraft.definition),
      dirty: Boolean(recovered),
      recoveredSelection: recovered?.selectedQuestionUuid ?? null,
      version: recovered?.version ?? initialDraft.version,
    };
  });
  const [definition, setDefinition] = useState(initialState.definition);
  const [version, setVersion] = useState(initialState.version);
  const [dirty, setDirty] = useState(initialState.dirty);
  const [savedVersion, setSavedVersion] = useState<number | null>(null);
  const [conflict, setConflict] = useState(false);
  const [validationMessages, setValidationMessages] = useState<string[]>([]);
  const [mobilePanel, setMobilePanel] = useState<MobilePanel>('outline');
  const revisionRef = useRef(0);
  const narrow = useNarrowViewport();
  const leaveBlocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      dirty &&
      !isAuthenticationPath(nextLocation.pathname) &&
      !isQuestionSelectionNavigation(currentLocation, nextLocation),
  );
  const requestedQuestionUuid = searchParams.get('question');
  const selectedQuestionUuid =
    (requestedQuestionUuid && hasQuestion(definition, requestedQuestionUuid)
      ? requestedQuestionUuid
      : initialState.recoveredSelection && hasQuestion(definition, initialState.recoveredSelection)
        ? initialState.recoveredSelection
        : definition.groups.flatMap((group) => group.questions).at(0)?.uuid) ?? null;

  useEffect(() => {
    if (!dirty) {
      clearActiveEditorRecovery(tenantId, surveyId);
      return;
    }
    registerActiveEditorRecovery({
      definition: serializeDefinition(definition),
      selectedQuestionUuid,
      surveyId,
      tenantId,
      version,
    });
    return () => {
      clearActiveEditorRecovery(tenantId, surveyId);
    };
  }, [definition, dirty, selectedQuestionUuid, surveyId, tenantId, version]);

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  const selectedQuestion = useMemo(
    () =>
      definition.groups
        .flatMap((group) => group.questions)
        .find((question) => question.uuid === selectedQuestionUuid) ?? null,
    [definition, selectedQuestionUuid],
  );

  const saveMutation = useMutation({
    mutationFn: async () => {
      const issues = validateDefinition(definition);
      if (issues.length) {
        setValidationMessages(issues.map((issue) => issue.message));
        throw new Error('请先修正草稿中的问题');
      }
      setValidationMessages([]);
      const submittedRevision = revisionRef.current;
      const saved = await saveDraft(api, surveyId, version, serializeDefinition(definition));
      return { saved, submittedRevision };
    },
    onSuccess: ({ saved, submittedRevision }) => {
      synchronizeDraftCache(saved);
      setVersion(saved.version);
      setSavedVersion(saved.version);
      if (revisionRef.current === submittedRevision) {
        setDefinition(parseDefinition(saved.definition));
        setDirty(false);
      }
      setConflict(false);
    },
    onError: (error) => {
      if (error instanceof ApiError && error.kind === 'conflict') setConflict(true);
    },
  });

  function synchronizeDraftCache(saved: DraftView) {
    queryClient.setQueryData(surveyDraftQueryKey(tenantId, surveyId), saved);
    queryClient.setQueryData<SurveyView>(
      surveyDetailQueryKey(tenantId, surveyId),
      (current) => current
        ? { ...current, draftVersion: saved.version }
        : current,
    );
  }

  function changeDefinition(next: EditableSurveyDefinition) {
    revisionRef.current += 1;
    setDefinition(next);
    setDirty(true);
    setSavedVersion(null);
    setConflict(false);
  }

  async function reloadDraft() {
    const result = await getSurveyDraft(api, surveyId);
    synchronizeDraftCache(result);
    discardEditorRecovery(tenantId, surveyId);
    revisionRef.current += 1;
    setDefinition(parseDefinition(result.definition));
    setVersion(result.version);
    setDirty(false);
    setConflict(false);
    setValidationMessages([]);
  }

  function exportLocalDraft() {
    const blob = new Blob([JSON.stringify(serializeDefinition(definition), null, 2)], {
      type: 'application/json',
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${surveyId}-local-draft.json`;
    link.click();
    URL.revokeObjectURL(url);
  }

  const outline = (
    <Outline
      definition={definition}
      selectedQuestionUuid={selectedQuestionUuid}
      disabled={!canEdit}
      onSelect={(uuid) => setSearchParams({ question: uuid }, { replace: true })}
      onMoveQuestion={(uuid, groupUuid, targetIndex) => {
        changeDefinition(moveQuestion(definition, uuid, groupUuid, targetIndex));
      }}
    />
  );
  const questionEditor = selectedQuestion ? (
    <QuestionEditor
      question={selectedQuestion}
      disabled={!canEdit}
      onChange={(update) => changeDefinition(updateQuestion(definition, selectedQuestion.uuid, update))}
    />
  ) : (
    <p>请选择题目</p>
  );
  const properties = (
    <PropertiesPanel
      definition={definition}
      disabled={!canEdit}
      onChange={(update) => changeDefinition({ ...definition, ...update })}
    />
  );

  return (
    <main className="survey-editor">
      <header className="survey-editor-header">
        <div>
          <p>问卷编辑</p>
          <h1>{surveyTitle || definition.title}</h1>
        </div>
        <div className="survey-editor-actions">
          <nav className="survey-editor-nav" aria-label="问卷工作流">
            <Link to={`/surveys/${surveyId}/import`}>
              <FileInput aria-hidden="true" />
              批量导入
            </Link>
            <Link to={`/surveys/${surveyId}/preview`}>
              <Eye aria-hidden="true" />
              草稿预览
            </Link>
            <Link to={`/surveys/${surveyId}/publish`}>
              <Rocket aria-hidden="true" />
              发布管理
            </Link>
          </nav>
          <div className="survey-editor-save">
            {!canEdit ? <span>当前账号仅可查看此问卷</span> : null}
            {savedVersion ? <span>已保存版本 {savedVersion}</span> : null}
            <button
              type="button"
              aria-label="保存草稿"
              disabled={!canEdit || !dirty || saveMutation.isPending}
              onClick={() => saveMutation.mutate()}
            >
              <Save aria-hidden="true" />
              保存
            </button>
          </div>
        </div>
      </header>

      {validationMessages.length ? (
        <div className="editor-validation" role="alert">
          {validationMessages.map((message) => <p key={message}>{message}</p>)}
        </div>
      ) : null}
      {saveMutation.error && !conflict && validationMessages.length === 0 ? (
        <p className="editor-validation" role="alert">
          {saveMutation.error instanceof Error ? saveMutation.error.message : '保存失败，请稍后重试'}
        </p>
      ) : null}

      {narrow ? (
        <div className="editor-mobile-layout">
          <div role="tablist" aria-label="编辑区域" className="editor-tabs">
            {(['outline', 'editor', 'properties'] as const).map((panel) => (
              <button
                key={panel}
                type="button"
                role="tab"
                aria-selected={mobilePanel === panel}
                onClick={() => setMobilePanel(panel)}
              >
                {panelLabel(panel)}
              </button>
            ))}
          </div>
          <section role="tabpanel" aria-label={panelLabel(mobilePanel)} className="editor-mobile-panel">
            {mobilePanel === 'outline' ? outline : mobilePanel === 'editor' ? questionEditor : properties}
          </section>
        </div>
      ) : (
        <div className="editor-desktop-grid">
          <aside aria-label="问卷大纲">{outline}</aside>
          <section aria-label="题目编辑">{questionEditor}</section>
          <aside aria-label="问卷属性">{properties}</aside>
        </div>
      )}

      {conflict ? (
        <div className="editor-dialog-backdrop">
          <div role="dialog" aria-modal="true" aria-label="草稿版本冲突" className="editor-dialog">
            <h2>草稿版本冲突</h2>
            <p>服务器上的草稿已更新。你的本地修改仍在当前页面中。</p>
            <div className="editor-dialog-actions">
              <button type="button" onClick={() => void reloadDraft()}>
                <RefreshCw aria-hidden="true" />
                重新载入
              </button>
              <button type="button" onClick={exportLocalDraft}>
                <Download aria-hidden="true" />
                导出本地草稿
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {leaveBlocker.state === 'blocked' ? (
        <div className="editor-dialog-backdrop">
          <div role="dialog" aria-modal="true" aria-label="未保存的修改" className="editor-dialog">
            <h2>未保存的修改</h2>
            <p>当前草稿尚未保存，离开后这些修改将被放弃。</p>
            <div className="editor-dialog-actions">
              <button type="button" onClick={() => leaveBlocker.reset()}>
                继续编辑
              </button>
              <button type="button" onClick={() => leaveBlocker.proceed()}>
                放弃修改并离开
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </main>
  );
}

export function EditorRoutePage() {
  const { api, session } = useAuth();
  const surveyId = z.string().uuid().safeParse(useParams().surveyId);
  if (!surveyId.success || !session) return <p role="alert">问卷标识无效</p>;
  return <EditorPage api={api} surveyId={surveyId.data} tenantId={session.me.tenantId} />;
}

function hasQuestion(definition: EditableSurveyDefinition, uuid: string) {
  return definition.groups.some((group) =>
    group.questions.some((question) => question.uuid === uuid),
  );
}

function panelLabel(panel: MobilePanel) {
  if (panel === 'outline') return '大纲';
  if (panel === 'editor') return '编辑';
  return '属性';
}

function isAuthenticationPath(pathname: string) {
  return (
    pathname === '/login' ||
    pathname === '/auth/callback' ||
    (import.meta.env.DEV && pathname === '/dev/token')
  );
}

interface RouteLocation {
  hash: string;
  pathname: string;
  search: string;
}

function isQuestionSelectionNavigation(current: RouteLocation, next: RouteLocation) {
  if (current.pathname !== next.pathname || current.hash !== next.hash) return false;

  const currentParams = new URLSearchParams(current.search);
  const nextParams = new URLSearchParams(next.search);
  const questionChanged = currentParams.get('question') !== nextParams.get('question');
  currentParams.delete('question');
  nextParams.delete('question');

  return questionChanged && normalizedSearch(currentParams) === normalizedSearch(nextParams);
}

function normalizedSearch(params: URLSearchParams) {
  return JSON.stringify([...params.entries()].sort(([left], [right]) => left.localeCompare(right)));
}

function useNarrowViewport() {
  const [narrow, setNarrow] = useState(
    () => typeof window.matchMedia === 'function' && window.matchMedia('(max-width: 760px)').matches,
  );
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return;
    const media = window.matchMedia('(max-width: 760px)');
    const update = () => setNarrow(media.matches);
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  return narrow;
}
