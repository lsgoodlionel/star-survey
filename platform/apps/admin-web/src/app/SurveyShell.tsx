import { useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
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
import { getResourcePath } from '../shared/api/resources';
import { getSurvey, surveyDetailQueryKey, type SurveyView } from '../shared/api/surveys';
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

interface RecentWorkVisit {
  command: RecentWorkCommand;
  id: number;
  key: string;
  owner: symbol;
}

interface RecentWorkCoordinator {
  activeVisit: RecentWorkVisit | null;
  nextId: number;
  queue: Promise<void>;
  scheduledVisits: Set<number>;
}

const recentWorkCoordinators = new WeakMap<QueryClient, Map<string, RecentWorkCoordinator>>();

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
  const [owner] = useState(() => Symbol('survey-shell'));
  const activeVisit = useRef<RecentWorkVisit | null>(null);
  const coordinator = getRecentWorkCoordinator(queryClient, `${tenantId}:${actorId}`);
  const surveyQuery = useQuery({
    queryKey: surveyDetailQueryKey(tenantId, surveyId),
    queryFn: ({ signal }) => getSurvey(api, surveyId, signal),
  });
  const pathQuery = useQuery({
    queryKey: ['resource-path', tenantId, surveyId],
    queryFn: ({ signal }) => getResourcePath(api, surveyId, signal),
    retry: false,
  });
  const recentWorkCommand = useMemo(
    () => recentWorkCommandForPath(location.pathname, surveyId),
    [location.pathname, surveyId],
  );

  useLayoutEffect(() => {
    activeVisit.current = activateRecentWorkVisit(
      coordinator,
      owner,
      recentWorkCommand,
    );
  }, [coordinator, owner, recentWorkCommand]);

  const reportPageReady = useCallback((readiness: RecentWorkReadiness) => {
    const visit = activeVisit.current;
    const command: RecentWorkCommand = readiness.page === 'version'
      ? { surveyId, page: readiness.page, version: readiness.version }
      : { surveyId, page: readiness.page, version: null };
    if (
      !surveyQuery.data ||
      !pathQuery.data ||
      !visit ||
      coordinator.activeVisit?.id !== visit.id ||
      recentWorkCommandKey(command) !== visit.key
    ) return;
    scheduleRecentWorkRegistration({
      api,
      coordinator,
      queryClient,
      tenantId,
      visit,
    });
  }, [api, coordinator, pathQuery.data, queryClient, surveyId, surveyQuery.data, tenantId]);
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
          <Link className="survey-workspace-return" to={`/workspace?resource=${surveyId}`}>
            <ArrowLeft aria-hidden="true" />
            返回工作区
          </Link>
        </div>
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

function getRecentWorkCoordinator(queryClient: QueryClient, identity: string) {
  let coordinators = recentWorkCoordinators.get(queryClient);
  if (!coordinators) {
    coordinators = new Map();
    recentWorkCoordinators.set(queryClient, coordinators);
  }
  let coordinator = coordinators.get(identity);
  if (!coordinator) {
    coordinator = {
      activeVisit: null,
      nextId: 1,
      queue: Promise.resolve(),
      scheduledVisits: new Set(),
    };
    coordinators.set(identity, coordinator);
  }
  return coordinator;
}

function activateRecentWorkVisit(
  coordinator: RecentWorkCoordinator,
  owner: symbol,
  command: RecentWorkCommand | null,
) {
  if (!command) {
    coordinator.activeVisit = null;
    return null;
  }
  const key = recentWorkCommandKey(command);
  if (coordinator.activeVisit?.owner === owner && coordinator.activeVisit.key === key) {
    return coordinator.activeVisit;
  }
  coordinator.scheduledVisits.clear();
  const visit = { command, id: coordinator.nextId++, key, owner };
  coordinator.activeVisit = visit;
  return visit;
}

function scheduleRecentWorkRegistration({
  api,
  coordinator,
  queryClient,
  tenantId,
  visit,
}: {
  api: ApiClient;
  coordinator: RecentWorkCoordinator;
  queryClient: QueryClient;
  tenantId: string;
  visit: RecentWorkVisit;
}) {
  if (coordinator.scheduledVisits.has(visit.id)) return;
  coordinator.scheduledVisits.add(visit.id);
  coordinator.queue = coordinator.queue
    .catch(() => undefined)
    .then(async () => {
      if (coordinator.activeVisit?.id !== visit.id) return;
      await recordRecentWork(api, visit.command);
      await queryClient.invalidateQueries({ queryKey: dashboardQueryKey(tenantId) });
    })
    .catch(() => undefined);
}

function recentWorkCommandKey(command: RecentWorkCommand) {
  return `${command.surveyId}:${command.page}:${command.version ?? ''}`;
}
