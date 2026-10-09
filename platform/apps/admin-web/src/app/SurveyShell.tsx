import { useQuery } from '@tanstack/react-query';
import { ArrowLeft, Eye, FileInput, Pencil, Rocket } from 'lucide-react';
import { createContext, useContext, useState } from 'react';
import { Link, Outlet, useLocation, useParams } from 'react-router-dom';
import { z } from 'zod';
import { useAuth } from '../features/auth/AuthProvider';
import type { ApiClient } from '../shared/api/http';
import { getResourcePath } from '../shared/api/resources';
import { getSurvey, surveyDetailQueryKey, type SurveyView } from '../shared/api/surveys';

interface SurveyShellContextValue {
  setUnsavedChanges(value: boolean): void;
}

const SurveyShellContext = createContext<SurveyShellContextValue | null>(null);

const workflowTabs = [
  { path: 'edit', label: '编辑', icon: Pencil },
  { path: 'import', label: '批量导入', icon: FileInput },
  { path: 'preview', label: '快速预览', icon: Eye },
  { path: 'publish', label: '发布与版本', icon: Rocket },
] as const;

export function SurveyShell() {
  const { api, session } = useAuth();
  const parsedSurveyId = z.string().uuid().safeParse(useParams().surveyId);
  if (!session || !parsedSurveyId.success) return <p role="alert">问卷标识无效</p>;
  return (
    <LoadedSurveyShell
      key={`${session.me.tenantId}:${parsedSurveyId.data}`}
      api={api}
      surveyId={parsedSurveyId.data}
      tenantId={session.me.tenantId}
    />
  );
}

function LoadedSurveyShell({ api, surveyId, tenantId }: {
  api: ApiClient;
  surveyId: string;
  tenantId: string;
}) {
  const location = useLocation();
  const [unsavedChanges, setUnsavedChanges] = useState(false);
  const surveyQuery = useQuery({
    queryKey: surveyDetailQueryKey(tenantId, surveyId),
    queryFn: ({ signal }) => getSurvey(api, surveyId, signal),
  });
  const pathQuery = useQuery({
    queryKey: ['resource-path', tenantId, surveyId],
    queryFn: ({ signal }) => getResourcePath(api, surveyId, signal),
    retry: false,
  });
  const context: SurveyShellContextValue = { setUnsavedChanges };

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
          <Link to="/workspace">工作台</Link>
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

export function useSurveyShell() {
  return useContext(SurveyShellContext);
}

export function surveyWorkflowHref(surveyId: string, path: string, question: string | null) {
  const href = `/surveys/${surveyId}/${path}`;
  return question ? `${href}?question=${encodeURIComponent(question)}` : href;
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
