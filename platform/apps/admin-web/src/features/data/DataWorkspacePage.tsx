import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, Database, RefreshCw } from 'lucide-react';
import { Link } from 'react-router-dom';
import { dashboardQueryKey, getDashboard } from '../../shared/api/dashboard';
import { ApiError } from '../../shared/api/errors';
import { useAuth } from '../auth/AuthProvider';
import './data-workspace.css';

export function DataWorkspacePage() {
  const { api, session } = useAuth();
  const tenantId = session?.me.tenantId ?? '';
  const actorId = session?.me.actorId ?? '';
  const workspace = useQuery({
    queryKey: [...dashboardQueryKey(tenantId), actorId, 'data-workspace'] as const,
    queryFn: ({ signal }) => getDashboard(api, { surveyLimit: 50, taskLimit: 50 }, signal),
    enabled: Boolean(tenantId && actorId),
    retry: false,
    staleTime: 0,
    refetchOnMount: 'always',
  });

  const forbidden = (workspace.error instanceof ApiError && workspace.error.kind === 'forbidden')
    || (workspace.data !== undefined && !workspace.data.visibleSections.includes('data'));
  const authorizedData = workspace.data?.visibleSections.includes('data') ? workspace.data : undefined;
  const surveys = authorizedData?.surveys.filter((survey) => survey.actions.includes('responses')) ?? [];
  const exportIssues = authorizedData?.tasks.filter((task) => task.kind === 'export_exception') ?? [];

  return (
    <main className="data-workspace-page">
      <header className="data-workspace-header">
        <div><p>答卷交付</p><h1>数据</h1><span>查看有权访问的答卷、摘要和导出任务。</span></div>
        <button type="button" aria-label="刷新数据工作区" disabled={workspace.isFetching}
          onClick={() => void workspace.refetch()}>
          <RefreshCw aria-hidden="true" />刷新
        </button>
      </header>

      {workspace.isPending ? (
        <div className="data-workspace-state" role="status">
          <Database aria-hidden="true" /><p>正在加载数据工作区</p>
        </div>
      ) : null}

      {(!workspace.data && workspace.isError) || forbidden ? (
        <section className="data-workspace-state" aria-labelledby="data-workspace-error-title">
          <AlertTriangle aria-hidden="true" />
          <h2 id="data-workspace-error-title">数据工作区不可用</h2>
          <p role="alert">{forbidden ? '你没有访问数据工作区的权限。' : '数据工作区暂时不可用，请稍后重试。'}</p>
          {!forbidden ? <button type="button" onClick={() => void workspace.refetch()}>重新加载数据工作区</button> : null}
        </section>
      ) : null}

      {authorizedData ? (
        <>
          {workspace.isRefetchError ? <p className="data-workspace-stale" role="status">刷新失败，当前显示上次加载的数据。</p> : null}
          <section className="data-workspace-summary" aria-label="数据任务摘要">
            <div><span>可访问问卷</span><strong>{surveys.length}</strong></div>
            <div><span>处理中导出</span><strong>{authorizedData.summary.activeExports}</strong></div>
            <div><span>导出问题</span><strong>{exportIssues.length}</strong></div>
          </section>

          <section className="data-workspace-section" aria-labelledby="data-surveys-title">
            <div className="data-workspace-section__heading"><h2 id="data-surveys-title">问卷数据</h2><span>{surveys.length} 份</span></div>
            {surveys.length ? (
              <div className="data-workspace-table-wrap"><table aria-label="可访问的数据问卷">
                <thead><tr><th>问卷</th><th>已完成答卷</th><th>最近更新</th><th>操作</th></tr></thead>
                <tbody>{surveys.map((survey) => <tr key={survey.surveyId}>
                  <td data-label="问卷"><strong>{survey.name}</strong><span>发布版本 {survey.publishedVersion ?? '暂无'}</span></td>
                  <td data-label="已完成答卷">{survey.completedResponses == null ? '无权查看' : `${survey.completedResponses} 份`}</td>
                  <td data-label="最近更新"><time dateTime={survey.updatedAt}>{formatDateTime(survey.updatedAt)}</time></td>
                  <td data-label="操作"><Link to={`/surveys/${survey.surveyId}/responses`}>查看答卷与导出</Link></td>
                </tr>)}</tbody>
              </table></div>
            ) : <p className="data-workspace-empty">当前没有可访问的答卷数据。</p>}
          </section>

          <section className="data-workspace-section" aria-labelledby="data-issues-title">
            <div className="data-workspace-section__heading"><h2 id="data-issues-title">导出问题</h2><span>{exportIssues.length} 项</span></div>
            {exportIssues.length ? <ul className="data-workspace-issues">{exportIssues.map((task) => <li key={task.taskKey}>
              <div><strong>{task.surveyName}</strong><span>{task.status}</span><time dateTime={task.updatedAt}>{formatDateTime(task.updatedAt)}</time></div>
              <Link to={task.targetPath}>处理导出问题</Link>
            </li>)}</ul> : <p className="data-workspace-empty">当前没有需要处理的导出问题。</p>}
          </section>
        </>
      ) : null}
    </main>
  );
}

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value));
}
