import { useQuery } from '@tanstack/react-query';
import { RefreshCw } from 'lucide-react';
import { Link } from 'react-router-dom';
import {
  dashboardQueryKey,
  getDashboard,
  type DashboardSurvey,
  type DashboardTask,
  type DashboardView,
  type RecentWork,
} from '../../shared/api/dashboard';
import { ApiError } from '../../shared/api/errors';
import type { DashboardAction, DashboardSection } from '../../shared/api/schemas';
import { useAuth } from '../auth/AuthProvider';
import './dashboard.css';

const summaryItems: ReadonlyArray<{
  key: keyof DashboardView['summary'];
  label: string;
  section: DashboardSection;
}> = [
  { key: 'pendingApprovals', label: '待我审批', section: 'approval' },
  { key: 'publishExceptions', label: '发布异常', section: 'publish' },
  { key: 'activePreviews', label: '运行中预览', section: 'preview' },
  { key: 'activeExports', label: '处理中导出', section: 'export' },
];

const taskPresentation: Record<DashboardTask['kind'], {
  action: string;
  label: string;
  section: DashboardSection;
}> = {
  pending_approval: { action: '查看审批', label: '待我审批', section: 'approval' },
  publish_exception: { action: '查看发布', label: '发布异常', section: 'publish' },
  preview_exception: { action: '查看预览', label: '预览异常', section: 'preview' },
  export_exception: { action: '查看导出', label: '导出异常', section: 'export' },
  draft_pending_publish: { action: '前往发布', label: '待发布草稿', section: 'publish' },
};

const publishStateLabels: Record<DashboardSurvey['publishState'], string> = {
  draft: '草稿',
  pending_approval: '待审批',
  approved: '审批通过',
  publishing: '发布中',
  published: '已发布',
  failed: '发布失败',
  needs_reconciliation: '待核对',
};

const taskStatusLabels: Record<string, string> = {
  approved: '审批通过',
  close_failed: '关闭失败',
  draft: '待发布',
  expired: '文件已过期',
  failed: '处理失败',
  pending: '待处理',
  pending_approval: '待审批',
  publish_failed: '发布失败',
  publishing: '发布中',
  running: '处理中',
};

const actionPresentation: Record<DashboardAction, { label: string; route: string }> = {
  edit: { label: '编辑', route: 'edit' },
  preview: { label: '真实预览', route: 'preview' },
  publish: { label: '发布与版本', route: 'publish' },
  responses: { label: '答卷与导出', route: 'responses' },
};

const recentPageLabels: Record<RecentWork['page'], string> = {
  edit: '继续编辑',
  import: '继续批量导入',
  preview: '继续真实预览',
  publish: '继续发布',
  responses: '继续查看答卷',
  version: '继续查看版本',
};

export function DashboardPage() {
  const { api, session } = useAuth();
  const tenantId = session?.me.tenantId ?? '';
  const actorId = session?.me.actorId ?? '';
  const dashboard = useQuery({
    queryKey: [...dashboardQueryKey(tenantId), actorId] as const,
    queryFn: ({ signal }) => getDashboard(api, undefined, signal),
    enabled: Boolean(tenantId && actorId),
    retry: false,
    staleTime: 0,
    refetchOnMount: 'always',
  });

  const busy = dashboard.isPending || dashboard.isFetching;

  return (
    <main className="dashboard-page">
      <header className="dashboard-header">
        <div>
          <h1>工作台</h1>
          {tenantId ? <p>当前租户：{tenantId}</p> : null}
        </div>
        <div className="dashboard-refresh">
          <span aria-live="polite" className="sr-only">
            {dashboard.isFetching && !dashboard.isPending ? '正在刷新工作台' : null}
          </span>
          <button
            type="button"
            aria-label="刷新工作台"
            title="刷新工作台"
            disabled={busy || !tenantId || !actorId}
            onClick={() => void dashboard.refetch()}
          >
            <RefreshCw aria-hidden="true" />
          </button>
        </div>
      </header>

      {dashboard.isRefetchError && dashboard.data ? (
        <p className="dashboard-stale" role="status" aria-label="工作台数据状态">
          刷新失败，当前显示上次加载的数据，数据可能已过期。
        </p>
      ) : null}

      {dashboard.isPending ? <DashboardSkeleton /> : null}
      {!dashboard.data && dashboard.isError ? (
        <InitialError error={dashboard.error} onRetry={() => void dashboard.refetch()} />
      ) : null}
      {dashboard.data ? <DashboardContent dashboard={dashboard.data} /> : null}
    </main>
  );
}

function DashboardSkeleton() {
  return (
    <div className="dashboard-skeleton" role="status" aria-label="正在加载工作台">
      <span className="sr-only">正在加载工作台</span>
      <div className="dashboard-summary" aria-hidden="true">
        {summaryItems.map((item) => <span className="dashboard-skeleton__summary" key={item.key} />)}
      </div>
      <span className="dashboard-skeleton__line" aria-hidden="true" />
      <span className="dashboard-skeleton__line" aria-hidden="true" />
      <span className="dashboard-skeleton__line dashboard-skeleton__line--short" aria-hidden="true" />
    </div>
  );
}

function InitialError({ error, onRetry }: { error: Error; onRetry: () => void }) {
  const forbidden = error instanceof ApiError && error.kind === 'forbidden';
  return (
    <section className="dashboard-initial-error" aria-labelledby="dashboard-error-title">
      <h2 id="dashboard-error-title">工作台不可用</h2>
      <p role="alert">
        {forbidden ? '你没有访问工作台的权限。' : '工作台数据暂时不可用，请稍后重试。'}
      </p>
      {!forbidden ? (
        <button type="button" onClick={onRetry}>
          <RefreshCw aria-hidden="true" />重新加载工作台
        </button>
      ) : null}
    </section>
  );
}

function DashboardContent({ dashboard }: { dashboard: DashboardView }) {
  const visibleSections = new Set(dashboard.visibleSections);
  const visibleSummaryItems = summaryItems.filter((item) => visibleSections.has(item.section));
  const hasBusinessSections = visibleSummaryItems.length > 0;
  const visibleTasks = dashboard.tasks.filter(
    (task) => visibleSections.has(taskPresentation[task.kind].section),
  );

  return (
    <>
      {hasBusinessSections ? (
        <>
          <section className="dashboard-section dashboard-section--summary" aria-labelledby="dashboard-summary-title">
            <div className="dashboard-section-heading">
              <h2 id="dashboard-summary-title">业务摘要</h2>
              <time dateTime={dashboard.generatedAt}>更新于 {formatDateTime(dashboard.generatedAt)}</time>
            </div>
            <dl className="dashboard-summary">
              {visibleSummaryItems.map((item) => (
                <div className="dashboard-summary__item" key={item.key}>
                  <dt>{item.label}</dt>
                  <dd>{dashboard.summary[item.key]}</dd>
                </div>
              ))}
            </dl>
          </section>

          <section className="dashboard-section" aria-labelledby="dashboard-tasks-title">
            <div className="dashboard-section-heading">
              <h2 id="dashboard-tasks-title">需要处理</h2>
              <span>{visibleTasks.length} 项</span>
            </div>
            {visibleTasks.length ? <TaskTable tasks={visibleTasks} /> : (
              <p className="dashboard-empty">当前没有需要处理的事项。</p>
            )}
          </section>
        </>
      ) : null}

      {visibleSections.has('survey') ? (
        <section className="dashboard-section" aria-labelledby="dashboard-surveys-title">
          <div className="dashboard-section-heading">
            <h2 id="dashboard-surveys-title">问卷运行情况</h2>
            <span>{dashboard.surveys.length} 份</span>
          </div>
          {dashboard.surveys.length ? <SurveyTable surveys={dashboard.surveys} /> : (
            <p className="dashboard-empty">当前没有可查看的问卷。</p>
          )}
        </section>
      ) : null}

      <section className="dashboard-section" aria-labelledby="dashboard-recent-title">
        <div className="dashboard-section-heading">
          <h2 id="dashboard-recent-title">最近工作</h2>
          <span>{dashboard.recentWork.length} 条</span>
        </div>
        {dashboard.recentWork.length ? <RecentWorkList items={dashboard.recentWork} /> : (
          <p className="dashboard-empty">还没有可恢复的最近工作。</p>
        )}
      </section>
    </>
  );
}

function TaskTable({ tasks }: { tasks: DashboardTask[] }) {
  return (
    <div className="dashboard-table-wrap">
      <table className="dashboard-table dashboard-task-table" aria-label="需要处理">
        <thead><tr><th>事项</th><th>状态</th><th>更新时间</th><th>操作</th></tr></thead>
        <tbody>
          {tasks.map((task) => {
            const presentation = taskPresentation[task.kind];
            return (
              <tr key={task.taskKey}>
                <td data-label="事项"><strong>{task.surveyName}</strong><span>{presentation.label}</span></td>
                <td data-label="状态"><span className="dashboard-state">{taskStatusLabel(task.status)}</span></td>
                <td data-label="更新时间"><time dateTime={task.updatedAt}>{formatDateTime(task.updatedAt)}</time></td>
                <td data-label="操作"><Link className="dashboard-primary-link" to={task.targetPath}>{presentation.action}</Link></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function SurveyTable({ surveys }: { surveys: DashboardSurvey[] }) {
  return (
    <div className="dashboard-table-wrap">
      <table className="dashboard-table dashboard-survey-table" aria-label="问卷运行情况">
        <thead><tr><th>问卷</th><th>发布状态</th><th>答卷</th><th>更新时间</th><th>快捷操作</th></tr></thead>
        <tbody>
          {surveys.map((survey) => (
            <tr key={`${survey.surveyId}:${survey.publishState}`}>
              <td data-label="问卷"><strong>{survey.name}</strong><span>草稿版本 {survey.draftVersion}</span></td>
              <td data-label="发布状态"><span className="dashboard-state">{publishStateLabels[survey.publishState]}</span></td>
              <td data-label="答卷">{survey.completedResponses == null ? '无权查看' : `${survey.completedResponses} 份`}</td>
              <td data-label="更新时间"><time dateTime={survey.updatedAt}>{formatDateTime(survey.updatedAt)}</time></td>
              <td data-label="快捷操作"><SurveyActions survey={survey} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SurveyActions({ survey }: { survey: DashboardSurvey }) {
  return (
    <div className="dashboard-actions">
      {survey.actions.map((action) => {
        const presentation = actionPresentation[action];
        return (
          <Link key={action} to={`/surveys/${survey.surveyId}/${presentation.route}`}>
            {presentation.label}
          </Link>
        );
      })}
    </div>
  );
}

function RecentWorkList({ items }: { items: RecentWork[] }) {
  return (
    <ol className="dashboard-recent-list">
      {items.map((item) => (
        <li key={`${item.surveyId}:${item.page}:${item.targetPath}`}>
          <div><strong>{item.surveyName}</strong><time dateTime={item.visitedAt}>{formatDateTime(item.visitedAt)}</time></div>
          <Link to={item.targetPath}>{recentPageLabels[item.page]}</Link>
        </li>
      ))}
    </ol>
  );
}

function taskStatusLabel(status: string) {
  if (taskStatusLabels[status]) return taskStatusLabels[status];
  return /[\u3400-\u9fff]/u.test(status) ? status : '状态待确认';
}

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value));
}
