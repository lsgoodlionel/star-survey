import { useEffect, useMemo, useRef, useState } from 'react';
import { type InfiniteData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Download, Eye, FileInput, RefreshCw, Rocket, Save } from 'lucide-react';
import { Link, useBlocker, useParams, useSearchParams } from 'react-router-dom';
import { z } from 'zod';
import {
  surveyWorkflowHref,
  useSurveyPageReady,
  useSurveyShell,
} from '../../app/surveyShellContext';
import { useAuth } from '../auth/AuthProvider';
import { approvalRequestsQueryKey, surveyOverviewQueryKey } from '../../shared/api/approvals';
import { ApiError } from '../../shared/api/errors';
import type { ApiClient } from '../../shared/api/http';
import {
  getResourceCapabilities,
  type ResourcePage,
  type ResourceView,
} from '../../shared/api/resources';
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
  createBasicQuestion,
  type BasicQuestionType,
  type EditableSurveyDefinition,
} from './model/definition';
import { moveGroup, moveQuestion, preserveCarouselSlideIds, setCarouselSlideAsset, updateQuestion, validateDefinition } from './model/operations';
import { readTimeWindow, updateTimeWindow, validateTimeWindow } from './model/timeWindow';
import { updateIpDenyList, validateIpDenyList } from './model/ipDenyList';
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
type PaletteType = BasicQuestionType | 'mjy-collapsible' | 'mjy-cascading-select' | 'mjy-carousel';

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
  const ready = surveyQuery.isSuccess
    && draftQuery.isSuccess
    && capabilitiesQuery.isSuccess
    && !surveyQuery.isFetching
    && !draftQuery.isFetching
    && !capabilitiesQuery.isFetching
    && Boolean(surveyQuery.data && draftQuery.data && capabilitiesQuery.data);
  useSurveyPageReady('edit', ready);
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
  const surveyShell = useSurveyShell();
  const setShellUnsavedChanges = surveyShell?.setUnsavedChanges;
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
  const definitionRef = useRef(definition);
  const [version, setVersion] = useState(initialState.version);
  const [dirty, setDirty] = useState(initialState.dirty);
  const [savedVersion, setSavedVersion] = useState<number | null>(null);
  const [savedAt, setSavedAt] = useState<Date | null>(null);
  const [conflict, setConflict] = useState(false);
  const [validationMessages, setValidationMessages] = useState<string[]>([]);
  const [mobilePanel, setMobilePanel] = useState<MobilePanel>('outline');
  const [newQuestionType, setNewQuestionType] = useState<PaletteType>('L');
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const editorRoot = useRef<HTMLElement>(null);
  const inspectorButton = useRef<HTMLButtonElement>(null);
  const inspectorClose = useRef<HTMLButtonElement>(null);
  const revisionRef = useRef(0);
  const narrow = useNarrowViewport();
  const medium = useMediaQuery('(min-width: 820px) and (max-width: 1179px)');
  useEffect(() => {
    if (inspectorOpen && medium) inspectorClose.current?.focus();
  }, [inspectorOpen, medium]);
  function closeInspector() {
    setInspectorOpen(false);
    inspectorButton.current?.focus();
  }
  const leaveBlocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      dirty &&
      !isAuthenticationPath(nextLocation.pathname) &&
      !isQuestionSelectionNavigation(currentLocation, nextLocation),
  );
  const conflictDialog = useDialogFocus(conflict, editorRoot);
  const leaveDialog = useDialogFocus(leaveBlocker.state === 'blocked', editorRoot);
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

  useEffect(() => {
    setShellUnsavedChanges?.(dirty);
    return () => setShellUnsavedChanges?.(false);
  }, [dirty, setShellUnsavedChanges]);

  const selectedQuestion = useMemo(
    () =>
      definition.groups
        .flatMap((group) => group.questions)
        .find((question) => question.uuid === selectedQuestionUuid) ?? null,
    [definition, selectedQuestionUuid],
  );
  const conditionSources = useMemo(() => {
    const questions = definition.groups.flatMap((group) => group.questions);
    const selectedIndex = questions.findIndex((question) => question.uuid === selectedQuestionUuid);
    return questions.slice(0, Math.max(0, selectedIndex)).filter((question) =>
      question.type === 'L' && /^[A-Za-z][A-Za-z0-9]*$/.test(question.code)
      && question.answers?.some((answer) => /^[A-Za-z0-9]{1,5}$/.test(answer.code)));
  }, [definition, selectedQuestionUuid]);

  const saveMutation = useMutation({
    mutationFn: async () => {
      const issues = [...validateDefinition(definition).map((issue) => issue.message), ...validateTimeWindow(definition), ...validateIpDenyList(definition)];
      if (issues.length) {
        setValidationMessages(issues);
        throw new Error('请先修正草稿中的问题');
      }
      setValidationMessages([]);
      const submittedRevision = revisionRef.current;
      const saved = await saveDraft(api, surveyId, version, serializeDefinition(definition));
      return { saved, submittedRevision };
    },
    onSuccess: ({ saved, submittedRevision }) => {
      const savedDefinition = preserveCarouselSlideIds(parseDefinition(saved.definition), definitionRef.current);
      synchronizeDraftCache(saved, savedDefinition.title);
      setVersion(saved.version);
      setSavedVersion(saved.version);
      setSavedAt(new Date());
      if (revisionRef.current === submittedRevision) {
        definitionRef.current = savedDefinition;
        setDefinition(savedDefinition);
        setDirty(false);
      }
      setConflict(false);
    },
    onError: (error) => {
      if (error instanceof ApiError && error.kind === 'conflict') setConflict(true);
    },
  });

  function synchronizeDraftCache(saved: DraftView, savedTitle?: string) {
    void queryClient.invalidateQueries({ queryKey: surveyOverviewQueryKey(tenantId, surveyId), exact: true });
    void queryClient.invalidateQueries({ queryKey: approvalRequestsQueryKey(tenantId, surveyId), exact: true });
    queryClient.setQueryData(surveyDraftQueryKey(tenantId, surveyId), saved);
    queryClient.setQueryData<SurveyView>(
      surveyDetailQueryKey(tenantId, surveyId),
      (current) => current
        ? { ...current, draftVersion: saved.version, title: savedTitle ?? current.title }
        : current,
    );
    if (savedTitle === undefined) return;
    queryClient.setQueriesData<ResourceView[]>(
      { queryKey: ['resource-path', tenantId] },
      (current) => current?.map((resource) =>
        resource.id === surveyId ? { ...resource, name: savedTitle } : resource),
    );
    queryClient.setQueriesData<InfiniteData<ResourcePage>>(
      { queryKey: ['resources', tenantId] },
      (current) => current
        ? {
            ...current,
            pages: current.pages.map((page) => ({
              ...page,
              items: page.items.map((resource) =>
                resource.id === surveyId ? { ...resource, name: savedTitle } : resource),
            })),
          }
        : current,
    );
    void queryClient.invalidateQueries({
      queryKey: ['resources', tenantId],
      refetchType: 'inactive',
    });
  }

  function changeDefinition(next: EditableSurveyDefinition) {
    definitionRef.current = next;
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
    const reloaded = parseDefinition(result.definition);
    definitionRef.current = reloaded;
    setDefinition(reloaded);
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
    <><div className="editor-question-palette">
      <label>添加题型 <select aria-label="添加题型" value={newQuestionType} disabled={!canEdit || !definition.groups.length} onChange={(event) => setNewQuestionType(event.target.value as PaletteType)}>
        <option value="L">单选题</option><option value="M">多选题</option><option value="S">短文本题</option><option value="T">长文本题</option><option value="X">说明文字</option>
        <option value="mjy-collapsible">折叠栏目</option>
        <option value="mjy-cascading-select">多级下拉</option><option value="mjy-carousel">轮播图</option>
      </select></label>
      <button type="button" disabled={!canEdit || !definition.groups.length} onClick={() => {
        const target = definition.groups.find((group) => group.questions.some((item) => item.uuid === selectedQuestionUuid)) ?? definition.groups[0];
        if (!target) return;
        const codes = new Set(definition.groups.flatMap((group) => group.questions.map((item) => item.code.toUpperCase())));
        let index = 1;
        while (codes.has(`Q${index}`)) index++;
        const uuid = crypto.randomUUID();
        const baseType: BasicQuestionType = newQuestionType === 'mjy-collapsible' ? 'X' : newQuestionType === 'mjy-cascading-select' ? 'T' : newQuestionType === 'mjy-carousel' ? 'L' : newQuestionType;
        const question = createBasicQuestion(baseType, uuid, `Q${index}`);
        if (newQuestionType === 'mjy-collapsible') {
          question.source.theme = 'mjy-collapsible';
          question.themeOptions = { summary: '新栏目', collapsed: true };
        }
        if (newQuestionType === 'mjy-cascading-select') {
          question.source.theme = newQuestionType;
          question.themeOptions = { structureVersion: 'r1', dictionary: '', levels: [''] };
        }
        if (newQuestionType === 'mjy-carousel') {
          question.source.theme = newQuestionType;
          question.themeOptions = { slides: [{ code: 'A1', assetId: '', alt: '' }], autoplay: false };
          question.slideIds = [crypto.randomUUID()];
        }
        changeDefinition({ ...definition, groups: definition.groups.map((group) => group.uuid === target.uuid ? { ...group, questions: [...group.questions, question] } : group) });
        setSearchParams({ question: uuid }, { replace: true });
        if (narrow) setMobilePanel('editor');
      }}>添加题目</button>
    </div><Outline
      definition={definition}
      selectedQuestionUuid={selectedQuestionUuid}
      disabled={!canEdit}
      onSelect={(uuid) => setSearchParams({ question: uuid }, { replace: true })}
      onMoveGroup={(uuid, targetIndex) => {
        changeDefinition(moveGroup(definition, uuid, targetIndex));
      }}
      onMoveQuestion={(uuid, groupUuid, targetIndex) => {
        changeDefinition(moveQuestion(definition, uuid, groupUuid, targetIndex));
      }}
    /></>
  );
  const questionEditor = selectedQuestion ? (
    <QuestionEditor
      key={selectedQuestion.uuid}
      supportsLogic={definition.definitionVersion >= 2}
      conditionSources={conditionSources}
      api={api}
      tenantId={tenantId}
      question={selectedQuestion}
      disabled={!canEdit}
      onChange={(update) => changeDefinition(updateQuestion(definition, selectedQuestion.uuid, update))}
      onSlideAssetChange={(questionUuid, slideId, assetId) => {
        const current = definitionRef.current;
        const next = setCarouselSlideAsset(current, questionUuid, slideId, assetId);
        if (next !== current) changeDefinition(next);
      }}
    />
  ) : (
    <p>请选择题目</p>
  );
  const properties = (
    <PropertiesPanel
      definition={definition}
      disabled={!canEdit}
      onChange={(update) => changeDefinition({ ...definition, ...update })}
      onTimeWindowChange={(window) => changeDefinition(updateTimeWindow(definition, window))}
      onIpDenyListChange={(denyIps) => changeDefinition(updateIpDenyList(definition, denyIps))}
    />
  );

  return (
    <main ref={editorRoot} tabIndex={-1} className="survey-editor">
      <header className="survey-editor-header">
        {!surveyShell ? (
          <div>
            <p>问卷编辑</p>
            <h1>{surveyTitle || definition.title}</h1>
          </div>
        ) : <h2 className="sr-only">问卷编辑</h2>}
        <div className="survey-editor-actions">
          {!surveyShell ? (
            <nav className="survey-editor-nav" aria-label="问卷工作流">
              <Link to={surveyWorkflowHref(surveyId, 'import', requestedQuestionUuid)}>
                <FileInput aria-hidden="true" />
                批量导入
              </Link>
              <Link to={surveyWorkflowHref(surveyId, 'preview', requestedQuestionUuid)}>
                <Eye aria-hidden="true" />
                快速预览
              </Link>
              <Link to={surveyWorkflowHref(surveyId, 'publish', requestedQuestionUuid)}>
                <Rocket aria-hidden="true" />
                发布管理
              </Link>
            </nav>
          ) : null}
          <div className="survey-editor-save">
            {!canEdit ? <span>当前账号仅可查看此问卷</span> : null}
            <span role="status" aria-label="保存状态">版本 {version} · {saveMutation.isPending ? '正在保存' : dirty ? '未保存' : savedAt ? `保存完成 ${savedAt.toLocaleTimeString()}` : '草稿已载入'}</span>
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
          {saveMutation.error instanceof ApiError && saveMutation.error.kind === 'validation' && readTimeWindow(definition).window
            ? '；若为开放时间窗，请检查时区和夏令时、起止顺序及原生时间设置。草稿修改仍留在本页。' : null}
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
                id={`editor-tab-${panel}`}
                aria-selected={mobilePanel === panel}
                aria-controls={`editor-panel-${panel}`}
                tabIndex={mobilePanel === panel ? 0 : -1}
                onKeyDown={(event) => {
                  const panels: MobilePanel[] = ['outline', 'editor', 'properties'];
                  const current = panels.indexOf(panel);
                  const next = event.key === 'ArrowRight' ? (current + 1) % panels.length
                    : event.key === 'ArrowLeft' ? (current + panels.length - 1) % panels.length
                    : event.key === 'Home' ? 0 : event.key === 'End' ? panels.length - 1 : -1;
                  if (next < 0) return;
                  event.preventDefault();
                  setMobilePanel(panels[next]);
                  document.getElementById(`editor-tab-${panels[next]}`)?.focus();
                }}
                onClick={() => setMobilePanel(panel)}
              >
                {panelLabel(panel)}
              </button>
            ))}
          </div>
          {(['outline', 'editor', 'properties'] as const).map((panel) => (
            <section key={panel} role="tabpanel" id={`editor-panel-${panel}`} aria-labelledby={`editor-tab-${panel}`} hidden={mobilePanel !== panel} className="editor-mobile-panel">
              {mobilePanel === panel ? panel === 'outline' ? outline : panel === 'editor' ? questionEditor : properties : null}
            </section>
          ))}
        </div>
      ) : (
        <div className={`editor-desktop-grid${medium ? ' editor-medium-grid' : ''}`}>
          <aside aria-label="问卷大纲">{outline}</aside>
          <section aria-label="题目编辑">
            {medium ? <button ref={inspectorButton} type="button" className="editor-inspector-open" onClick={() => setInspectorOpen(true)}>打开属性检查器</button> : null}
            {questionEditor}
          </section>
          {medium ? inspectorOpen && (
            <aside role="dialog" aria-label="问卷属性" className="editor-inspector" onKeyDown={(event) => { if (event.key === 'Escape') closeInspector(); }}>
              <button ref={inspectorClose} type="button" onClick={closeInspector}>关闭属性检查器</button>
              {properties}
            </aside>
          ) : <aside aria-label="问卷属性">{properties}</aside>}
        </div>
      )}

      {conflict ? (
        <div className="editor-dialog-backdrop">
          <div ref={conflictDialog} role="dialog" aria-modal="true" aria-label="草稿版本冲突" className="editor-dialog" onKeyDown={trapDialogFocus}>
            <h2>草稿版本冲突</h2>
            <p>服务器上的草稿已更新。你的本地修改仍在当前页面中。</p>
            <div className="editor-dialog-actions">
              <button type="button" onClick={() => setConflict(false)}>继续编辑</button>
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
          <div ref={leaveDialog} role="dialog" aria-modal="true" aria-label="未保存的修改" className="editor-dialog" onKeyDown={trapDialogFocus}>
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
  return (
    <EditorPage
      key={`${session.me.tenantId}:${session.me.actorId}:${surveyId.data}`}
      api={api}
      surveyId={surveyId.data}
      tenantId={session.me.tenantId}
    />
  );
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
  return useMediaQuery('(max-width: 819px)');
}

function useDialogFocus(active: boolean, fallback: React.RefObject<HTMLElement | null>) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!active) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const fallbackElement = fallback.current;
    ref.current?.querySelector<HTMLElement>('button:not(:disabled)')?.focus();
    return () => {
      if (previous && previous !== document.body && previous.isConnected && !previous.matches(':disabled')) previous.focus();
      else fallbackElement?.focus();
    };
  }, [active, fallback]);
  return ref;
}

function trapDialogFocus(event: React.KeyboardEvent<HTMLDivElement>) {
  if (event.key !== 'Tab') return;
  const buttons = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')];
  if (!buttons.length) return;
  const first = buttons[0];
  const last = buttons[buttons.length - 1];
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
}

function useMediaQuery(query: string) {
  const [matches, setMatches] = useState(
    () => typeof window.matchMedia === 'function' && window.matchMedia(query).matches,
  );
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return;
    const media = window.matchMedia(query);
    const update = () => setMatches(media.matches);
    update();
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, [query]);
  return matches;
}
