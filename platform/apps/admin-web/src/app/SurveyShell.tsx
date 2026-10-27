import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, BarChart3, Eye, FileInput, Pencil, Rocket } from 'lucide-react';
import {
  useCallback,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { Link, Outlet, useLocation, useParams } from 'react-router-dom';
import { z } from 'zod';
import { useAuth } from '../features/auth/AuthProvider';
import type { ApiClient } from '../shared/api/http';
import {
  dashboardQueryKey,
  recordRecentWork,
  type RecentWorkCommand,
} from '../shared/api/dashboard';
import { getResourceCapabilities, getResourcePath } from '../shared/api/resources';
import { getSurvey, surveyDetailQueryKey, type SurveyView } from '../shared/api/surveys';
import {
  addTenantTemplateVersion, createTenantTemplate, listManagedTemplates, templateManagedQueryKey,
} from '../shared/api/templates';
import { parsePositiveIntegerParam } from '../features/publish/routeParams';
import {
  SurveyShellContext,
  surveyWorkflowHref,
  type RecentWorkReadiness,
  type SurveyShellContextValue,
} from './surveyShellContext';

export {
  surveyWorkflowHref,
  useSurveyPageReady,
  useSurveyShell,
} from './surveyShellContext';
import {
  RecentWorkCoordinatorRegistry,
  type RecentWorkVisit,
} from './recentWorkCoordinator';

const recentWorkCoordinators = new RecentWorkCoordinatorRegistry<RecentWorkCommand>();

const workflowTabs = [
  { path: 'edit', label: '编辑', icon: Pencil },
  { path: 'import', label: '批量导入', icon: FileInput },
  { path: 'preview', label: '快速预览', icon: Eye },
  { path: 'publish', label: '发布与版本', icon: Rocket },
  { path: 'responses', label: '答卷与导出', icon: BarChart3 },
] as const;

export function SurveyShell() {
  const { api, session } = useAuth();
  const parsedSurveyId = z.string().uuid().safeParse(useParams().surveyId);
  if (!session || !parsedSurveyId.success) return <p role="alert">问卷标识无效</p>;
  return (
    <LoadedSurveyShell
      key={`${session.me.tenantId}:${session.me.actorId}:${parsedSurveyId.data}`}
      actorId={session.me.actorId}
      api={api}
      surveyId={parsedSurveyId.data}
      tenantId={session.me.tenantId}
    />
  );
}

function LoadedSurveyShell({ actorId, api, surveyId, tenantId }: {
  actorId: string;
  api: ApiClient;
  surveyId: string;
  tenantId: string;
}) {
  const location = useLocation();
  const queryClient = useQueryClient();
  const [unsavedChanges, setUnsavedChanges] = useState(false);
  const [templateFormOpen, setTemplateFormOpen] = useState(false);
  const [templateTarget, setTemplateTarget] = useState('new');
  const [templateName, setTemplateName] = useState('');
  const [templateDescription, setTemplateDescription] = useState('');
  const [templateValidation, setTemplateValidation] = useState<string | null>(null);
  const templateLock = useRef(false);
  const [owner] = useState(() => Symbol('survey-shell'));
  const activeVisit = useRef<RecentWorkVisit<RecentWorkCommand> | null>(null);
  const identity = `${tenantId}:${actorId}`;
  const coordinator = recentWorkCoordinators.get(queryClient, identity);
  const surveyQuery = useQuery({
    queryKey: surveyDetailQueryKey(tenantId, surveyId),
    queryFn: ({ signal }) => getSurvey(api, surveyId, signal),
  });
  const pathQuery = useQuery({
    queryKey: ['resource-path', tenantId, surveyId],
    queryFn: ({ signal }) => getResourcePath(api, surveyId, signal),
    retry: false,
  });
  const templateCapabilities = useQuery({
    queryKey: ['resource-capabilities', tenantId, surveyId],
    queryFn: ({ signal }) => getResourceCapabilities(api, surveyId, signal),
    retry: false,
  });
  const managedTemplates = useQuery({
    queryKey: templateManagedQueryKey(tenantId),
    queryFn: ({ signal }) => listManagedTemplates(api, signal),
    enabled: templateFormOpen && templateCapabilities.data?.canEdit === true,
    retry: false,
  });
  const createTemplate = useMutation({
    mutationFn: (target: string) => target === 'new'
      ? createTenantTemplate(api, surveyId, templateName.trim(), templateDescription.trim() || null)
      : addTenantTemplateVersion(api, target, surveyId),
    onSuccess: async () => {
      setTemplateValidation(null);
      await queryClient.invalidateQueries({ queryKey: ['templates', tenantId] });
    },
    onSettled: () => { templateLock.current = false; },
  });

  function submitTemplateForm() {
    if (templateLock.current) return;
    const name = templateName.trim();
    if (templateTarget === 'new' && !name) { setTemplateValidation('请填写模板名称'); return; }
    if (templateTarget === 'new' && name.length > 200) { setTemplateValidation('模板名称不能超过 200 字'); return; }
    if (templateTarget === 'new' && templateDescription.trim().length > 1000) {
      setTemplateValidation('模板说明不能超过 1000 字'); return;
    }
    if (templateTarget !== 'new' && !managedTemplates.data?.some((item) =>
      item.id === templateTarget && item.status !== 'pending')) {
      setTemplateValidation('所选模板当前不可添加版本'); return;
    }
    setTemplateValidation(null);
    templateLock.current = true;
    createTemplate.mutate(templateTarget);
  }
  const recentWorkCommand = useMemo(
    () => recentWorkCommandForPath(location.pathname, surveyId),
    [location.pathname, surveyId],
  );
  const shellReady = surveyQuery.isSuccess
    && pathQuery.isSuccess
    && !surveyQuery.isFetching
    && !pathQuery.isFetching
    && Boolean(surveyQuery.data && pathQuery.data);

  useLayoutEffect(() => {
    recentWorkCoordinators.retain(coordinator, owner);
    activeVisit.current = recentWorkCoordinators.activate(
      coordinator,
      owner,
      recentWorkCommand ? recentWorkCommandKey(recentWorkCommand) : null,
      recentWorkCommand,
    );
    return () => {
      activeVisit.current = null;
      recentWorkCoordinators.release(queryClient, identity, coordinator, owner);
    };
  }, [coordinator, identity, owner, queryClient, recentWorkCommand]);

  const reportPageReady = useCallback((readiness: RecentWorkReadiness) => {
    const visit = activeVisit.current;
    const command: RecentWorkCommand = readiness.page === 'version'
      ? { surveyId, page: readiness.page, version: readiness.version }
      : { surveyId, page: readiness.page, version: null };
    if (
      !shellReady ||
      !visit ||
      coordinator.activeVisit?.id !== visit.id ||
      recentWorkCommandKey(command) !== visit.key
    ) return;
    recentWorkCoordinators.schedule(queryClient, identity, coordinator, visit, async () => {
      await recordRecentWork(api, visit.value);
      await queryClient.invalidateQueries({ queryKey: dashboardQueryKey(tenantId) });
    });
  }, [api, coordinator, identity, queryClient, shellReady, surveyId, tenantId]);
  const context = useMemo<SurveyShellContextValue>(
    () => ({ reportPageReady, setUnsavedChanges }),
    [reportPageReady],
  );

  if (surveyQuery.isPending || pathQuery.isPending) {
    return <p className="survey-shell-loading">正在加载问卷上下文</p>;
  }
  if (surveyQuery.isError || pathQuery.isError || !surveyQuery.data || !pathQuery.data) {
    return <p role="alert">问卷上下文暂时不可用，请稍后重试。</p>;
  }

  const question = new URLSearchParams(location.search).get('question');
  return (
    <section className="survey-shell">
      <header className="survey-shell-header">
        <nav className="survey-breadcrumb" aria-label="面包屑">
          <Link to="/workspace">项目与问卷</Link>
          {pathQuery.data.map((resource, index) => {
            const current = index === pathQuery.data.length - 1;
            return current ? (
              <span key={resource.id} aria-current="page">{surveyQuery.data.title}</span>
            ) : (
              <Link key={resource.id} to={`/workspace?resource=${resource.id}`}>
                {resource.name}
              </Link>
            );
          })}
        </nav>
        <div className="survey-shell-heading">
          <div>
            <h1>{surveyQuery.data.title}</h1>
            <div className="survey-shell-status">
              <span>草稿版本 {surveyQuery.data.draftVersion}</span>
              <span>{surveyStatusLabel(surveyQuery.data.status)}</span>
              {unsavedChanges ? <span className="survey-unsaved">有未保存修改</span> : null}
            </div>
          </div>
          <div className="survey-shell-commands">
            {templateCapabilities.data?.canEdit ? <button type="button"
              onClick={() => { setTemplateFormOpen((open) => !open); createTemplate.reset(); }}>
              保存为模板
            </button> : null}
            <Link className="survey-workspace-return" to={`/workspace?resource=${surveyId}`}>
              <ArrowLeft aria-hidden="true" />返回工作区
            </Link>
          </div>
        </div>
        {templateFormOpen && templateCapabilities.data?.canEdit ? <div className="survey-template-form">
          <label>保存方式 <select aria-label="保存方式" value={templateTarget}
            onChange={(event) => { setTemplateTarget(event.target.value); createTemplate.reset(); }}>
            <option value="new">新建模板</option>
            {managedTemplates.data?.filter((item) => item.status !== 'pending').map((item) =>
              <option key={item.id} value={item.id}>添加版本：{item.name}</option>)}
          </select></label>
          {managedTemplates.isError ? <p role="alert">管理模板加载失败，请重试</p> : null}
          {templateTarget === 'new' ? <>
          <label>模板名称 <input aria-label="模板名称" value={templateName}
            onChange={(event) => setTemplateName(event.target.value)} /></label>
          <label>模板说明 <textarea aria-label="模板说明" value={templateDescription}
            onChange={(event) => setTemplateDescription(event.target.value)} /></label>
          </> : null}
          <button type="button" disabled={createTemplate.isPending} onClick={submitTemplateForm}>
            {templateTarget === 'new' ? '创建模板' : '添加到模板版本'}
          </button>
          {createTemplate.isPending ? <p role="status">正在创建模板</p> : null}
          {templateValidation ? <p role="alert">{templateValidation}</p> : null}
          {createTemplate.isError ? <p role="alert">创建失败，请检查问卷权限或稍后重试</p> : null}
          {createTemplate.data ? <p role="status">{createTemplate.variables === 'new' ? '模板已创建。' : '模板版本已添加。'}<Link to={`/templates/${createTemplate.data.id}?mode=managed`}>{createTemplate.variables === 'new' ? '查看新模板' : '查看模板'}</Link></p> : null}
        </div> : null}
        <nav className="survey-workflow-tabs" aria-label="问卷工作流">
          {workflowTabs.map((tab) => {
            const Icon = tab.icon;
            const active = isActiveTab(location.pathname, tab.path);
            return (
              <Link
                key={tab.path}
                to={surveyWorkflowHref(surveyId, tab.path, question)}
                aria-current={active ? 'page' : undefined}
              >
                <Icon aria-hidden="true" />
                {tab.label}
              </Link>
            );
          })}
        </nav>
      </header>
      <SurveyShellContext.Provider value={context}>
        <Outlet />
      </SurveyShellContext.Provider>
    </section>
  );
}

function isActiveTab(pathname: string, tab: (typeof workflowTabs)[number]['path']) {
  if (tab === 'publish') return pathname.endsWith('/publish') || pathname.includes('/versions/');
  return pathname.endsWith(`/${tab}`);
}

function surveyStatusLabel(status: SurveyView['status']) {
  const labels: Record<SurveyView['status'], string> = {
    draft: '草稿',
    publishing: '发布处理中',
    published: '已发布',
    publish_failed: '发布失败',
    pending_reconciliation: '发布结果正在核对',
  };
  return labels[status];
}

function recentWorkCommandForPath(pathname: string, surveyId: string): RecentWorkCommand | null {
  const parts = pathname.split('/').filter(Boolean);
  if (parts[0] !== 'surveys' || parts[1] !== surveyId) return null;
  if (parts.length === 3) {
    const page = parts[2];
    if (
      page === 'edit' ||
      page === 'import' ||
      page === 'preview' ||
      page === 'publish' ||
      page === 'responses'
    ) {
      return { surveyId, page, version: null };
    }
    return null;
  }
  if (parts.length !== 4 || parts[2] !== 'versions') return null;
  const version = parsePositiveIntegerParam(parts[3]);
  if (version === null) return null;
  return { surveyId, page: 'version', version };
}

function recentWorkCommandKey(command: RecentWorkCommand) {
  return `${command.surveyId}:${command.page}:${command.version ?? ''}`;
}
