import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Ban, ChevronLeft, ChevronRight, Download, FileDown, RefreshCw } from 'lucide-react';
import { useParams } from 'react-router-dom';
import { useAuth } from '../auth/AuthProvider';
import { ApiError } from '../../shared/api/errors';
import {
  createExportClient,
  exportJobQueryKey,
  type ExportClient,
  type ExportFormat,
  type ExportJobView,
} from '../../shared/api/exports';
import type { ApiClient } from '../../shared/api/http';
import {
  getResponseSummary,
  listResponses,
  responsePageQueryKey,
  responseSummaryQueryKey,
  type ResponseFilters,
  type ResponseRow,
  type ResponseState,
  type ResponseSummary,
} from '../../shared/api/responses';
import { parseSurveyIdParam } from '../publish/routeParams';
import './responses.css';

interface ResponsesPageProps {
  api: ApiClient;
  exportClient: ExportClient;
  surveyId: string;
  tenantId: string;
}

const exportFormats: ReadonlyArray<{ value: ExportFormat; label: string }> = [
  { value: 'csv', label: 'CSV 数据包' },
  { value: 'xlsx', label: 'Excel 工作簿' },
  { value: 'sav', label: 'SPSS 数据包' },
  { value: 'docx', label: 'Word 答卷文档' },
  { value: 'attachments', label: '附件包' },
];

const stateOptions: ReadonlyArray<{ value: ResponseState; label: string }> = [
  { value: 'engine_completed', label: '已完成' },
  { value: 'in_progress', label: '填写中' },
  { value: 'deleted', label: '已删除' },
];

export function ResponsesPage({ api, exportClient, surveyId, tenantId }: ResponsesPageProps) {
  const queryClient = useQueryClient();
  const [state, setState] = useState<ResponseState | null>(null);
  const [version, setVersion] = useState<number | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [cursorHistory, setCursorHistory] = useState<Array<string | null>>([]);
  const [format, setFormat] = useState<ExportFormat>('csv');
  const [currentJob, setCurrentJob] = useState<ExportJobView | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const filters: ResponseFilters = { state, version, cursor, limit: 50 };

  const summary = useQuery({
    queryKey: responseSummaryQueryKey(tenantId, surveyId),
    queryFn: ({ signal }) => getResponseSummary(api, surveyId, signal),
    retry: false,
  });
  const responses = useQuery({
    queryKey: responsePageQueryKey(tenantId, surveyId, filters),
    queryFn: ({ signal }) => listResponses(api, surveyId, filters, signal),
    retry: false,
  });
  const job = useQuery({
    queryKey: exportJobQueryKey(tenantId, surveyId, currentJob?.jobId ?? 'none'),
    queryFn: ({ signal }) => exportClient.get(currentJob!.jobId, signal),
    enabled: currentJob?.status === 'queued' || currentJob?.status === 'running',
    initialData: currentJob?.status === 'queued' || currentJob?.status === 'running' ? currentJob : undefined,
    retry: false,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === 'queued' || status === 'running' ? 1_500 : false;
    },
  });
  const visibleJob = job.data ?? currentJob;

  const createJob = useMutation({
    mutationFn: () => exportClient.create(surveyId, {
      format,
      filter: { states: state ? [state] : null, versions: version ? [version] : null },
      templateVersion: null,
    }, { idempotencyKey: crypto.randomUUID() }),
    onMutate: () => setDownloadError(null),
    onSuccess: (created) => {
      queryClient.setQueryData(exportJobQueryKey(tenantId, surveyId, created.jobId), created);
      setCurrentJob(created);
    },
  });
  const cancelJob = useMutation({
    mutationFn: (jobId: string) => exportClient.cancel(jobId),
    onSuccess: (cancelled) => {
      queryClient.setQueryData(exportJobQueryKey(tenantId, surveyId, cancelled.jobId), cancelled);
      setCurrentJob(cancelled);
    },
  });
  const downloadJob = useMutation({
    mutationFn: (jobId: string) => exportClient.download(jobId),
    onMutate: () => setDownloadError(null),
    onSuccess: (file) => triggerDownload(file.blob, file.filename),
    onError: (error) => setDownloadError(publicMessage(error)),
  });

  const summaryForbidden = isForbidden(summary.error);
  const detailForbidden = isForbidden(responses.error);
  const loadFailed = (summary.isError && !summaryForbidden) || (responses.isError && !detailForbidden);

  return (
    <main className="responses-page">
      <header className="responses-page__header">
        <div>
          <p>数据回收</p>
          <h2>答卷与导出</h2>
        </div>
        <span>跨发布版本统一查看</span>
      </header>

      {loadFailed ? (
        <div className="responses-error">
          <p role="alert">答卷数据加载失败，请稍后重试。</p>
          <button type="button" onClick={() => void Promise.all([summary.refetch(), responses.refetch()])}>
            <RefreshCw aria-hidden="true" />重新加载
          </button>
        </div>
      ) : null}

      {!summaryForbidden && summary.data ? <Summary summary={summary.data} /> : null}
      {summaryForbidden ? <p className="responses-notice">你没有查看答卷统计摘要的权限。</p> : null}

      <section className="responses-workspace" aria-labelledby="response-list-title">
        <div className="responses-section-heading">
          <div><h3 id="response-list-title">答卷明细</h3><p>筛选条件同时用于创建导出任务。</p></div>
          {responses.data && !responses.data.sensitiveRevealed ? <span>敏感字段已按权限遮蔽</span> : null}
        </div>

        {!detailForbidden ? (
          <div className="response-filters">
            <label>答卷状态
              <select value={state ?? ''} onChange={(event) => {
                setState((event.target.value || null) as ResponseState | null);
                resetPagination(setCursor, setCursorHistory);
              }}>
                <option value="">全部状态</option>
                {stateOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
            </label>
            <label>发布版本
              <select value={version ?? ''} onChange={(event) => {
                setVersion(event.target.value ? Number(event.target.value) : null);
                resetPagination(setCursor, setCursorHistory);
              }}>
                <option value="">全部版本</option>
                {(summary.data?.versions ?? []).map((item) => (
                  <option key={item.version} value={item.version}>版本 {item.version}</option>
                ))}
              </select>
            </label>
          </div>
        ) : null}

        {detailForbidden ? (
          <p className="responses-notice">你可以查看统计摘要，但没有查看答卷明细和导出的权限。</p>
        ) : responses.isPending ? (
          <p className="responses-empty">正在加载答卷</p>
        ) : responses.data ? (
          <>
            {responses.data.items.length ? <ResponseTable rows={responses.data.items} /> : (
              <p className="responses-empty">
                {summary.data && totalResponses(summary.data) === 0 && !state && version === null
                  ? '当前还没有答卷。'
                  : '当前筛选下没有答卷。'}
              </p>
            )}
            <div className="response-pagination" aria-label="答卷分页">
              <button type="button" disabled={!cursorHistory.length} onClick={() => {
                const previous = cursorHistory.at(-1) ?? null;
                setCursorHistory((history) => history.slice(0, -1));
                setCursor(previous);
              }}><ChevronLeft aria-hidden="true" />上一页</button>
              <span>第 {cursorHistory.length + 1} 页</span>
              <button type="button" disabled={!responses.data.nextCursor} onClick={() => {
                if (!responses.data?.nextCursor) return;
                setCursorHistory((history) => [...history, cursor]);
                setCursor(responses.data.nextCursor);
              }}>下一页<ChevronRight aria-hidden="true" /></button>
            </div>
          </>
        ) : null}
      </section>

      {!detailForbidden ? (
        <section className="export-workspace" aria-labelledby="create-export-title">
          <div className="responses-section-heading">
            <div><h3 id="create-export-title">创建导出</h3><p>导出任务按创建时的筛选快照执行。</p></div>
          </div>
          <div className="export-create-row">
            <label>导出格式
              <select value={format} onChange={(event) => setFormat(event.target.value as ExportFormat)}>
                {exportFormats.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
            </label>
            <button type="button" disabled={createJob.isPending} onClick={() => createJob.mutate()}>
              <FileDown aria-hidden="true" />创建导出任务
            </button>
          </div>
          {createJob.isError ? <p role="alert">{publicMessage(createJob.error)}</p> : null}
          {visibleJob ? (
            <>
              <ExportJob
                job={visibleJob}
                cancelling={cancelJob.isPending}
                downloading={downloadJob.isPending}
                onCancel={() => {
                  cancelJob.reset();
                  cancelJob.mutate(visibleJob.jobId);
                }}
                onDownload={() => downloadJob.mutate(visibleJob.jobId)}
              />
              {job.isError ? (
                <div className="export-job-error" role="alert" aria-label="导出任务状态刷新失败">
                  <p>任务状态刷新失败，任务仍已保留。请重新加载最新状态。</p>
                  <button type="button" onClick={() => void job.refetch()}>
                    <RefreshCw aria-hidden="true" />重新加载任务状态
                  </button>
                </div>
              ) : null}
              {cancelJob.isError ? (
                <div className="export-job-error" role="alert" aria-label="取消导出任务失败">
                  <p>取消失败，任务仍已保留。你可以重试取消或刷新任务状态。</p>
                  <div>
                    <button type="button" onClick={() => cancelJob.mutate(visibleJob.jobId)}>
                      <Ban aria-hidden="true" />重试取消
                    </button>
                    <button type="button" onClick={() => void job.refetch()}>
                      <RefreshCw aria-hidden="true" />刷新任务状态
                    </button>
                  </div>
                </div>
              ) : null}
            </>
          ) : <p className="responses-empty">尚未创建导出任务。</p>}
          {downloadError ? <p role="alert">{downloadError}</p> : null}
        </section>
      ) : null}
    </main>
  );
}

function Summary({ summary }: { summary: ResponseSummary }) {
  return (
    <section className="responses-summary" aria-label="答卷摘要">
      <div aria-label={`已完成 ${summary.total.engineCompleted}`}><span>已完成</span><strong>{summary.total.engineCompleted}</strong></div>
      <div aria-label={`填写中 ${summary.total.inProgress}`}><span>填写中</span><strong>{summary.total.inProgress}</strong></div>
      <div aria-label={`已删除 ${summary.total.deleted}`}><span>已删除</span><strong>{summary.total.deleted}</strong></div>
      <div aria-label={`发布版本 ${summary.versions.length}`}><span>发布版本</span><strong>{summary.versions.length}</strong></div>
    </section>
  );
}

function ResponseTable({ rows }: { rows: ResponseRow[] }) {
  return (
    <div className="response-table-wrap">
      <table>
        <thead><tr><th>答卷</th><th>状态</th><th>版本</th><th>开始/完成时间</th><th>作答内容</th></tr></thead>
        <tbody>{rows.map((row) => (
          <tr key={`${row.generation}:${row.responseId}`}>
            <td>#{row.responseId}</td>
            <td>{responseStateLabel(row.state)}</td>
            <td>版本 {row.version}</td>
            <td><time dateTime={row.startedAt}>{formatDateTime(row.startedAt)}</time>{row.completedAt ? <><br /><time dateTime={row.completedAt}>{formatDateTime(row.completedAt)}</time></> : null}</td>
            <td>{row.answers ? (
              <dl className="response-answers">{Object.entries(row.answers).map(([key, value]) => (
                <div key={key}><dt>{key}</dt><dd>{value ?? '未填写'}</dd></div>
              ))}</dl>
            ) : answersStatusLabel(row.answersStatus)}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

function ExportJob({ job, cancelling, downloading, onCancel, onDownload }: {
  job: ExportJobView;
  cancelling: boolean;
  downloading: boolean;
  onCancel: () => void;
  onDownload: () => void;
}) {
  const active = job.status === 'queued' || job.status === 'running';
  return (
    <article className="export-job" aria-live="polite">
      <div className="export-job__heading">
        <div><strong>{exportStatusLabel(job.status)}</strong><span>{exportFormatLabel(job.format)}</span></div>
        <span>{job.processedRows}{job.totalRows !== null ? ` / ${job.totalRows}` : ''} 行</span>
      </div>
      {job.status === 'running' && job.totalRows ? (
        <progress max={job.totalRows} value={job.processedRows}>{job.processedRows} / {job.totalRows}</progress>
      ) : null}
      {job.error ? <p role="alert">导出失败，请重新创建任务。</p> : null}
      <div className="export-job__actions">
        {active ? <button type="button" disabled={cancelling} onClick={onCancel}><Ban aria-hidden="true" />取消任务</button> : null}
        {job.status === 'completed' ? <button type="button" disabled={downloading} onClick={onDownload}><Download aria-hidden="true" />下载导出文件</button> : null}
      </div>
    </article>
  );
}

export function ResponsesRoutePage() {
  const { api, logout, session } = useAuth();
  const surveyId = parseSurveyIdParam(useParams().surveyId);
  const exportClient = useMemo(() => createExportClient({
    getToken: () => session?.token ?? null,
    onUnauthorized: () => logout(),
  }), [logout, session?.token]);
  if (!session || !surveyId) return <p role="alert">问卷标识无效</p>;
  return <ResponsesPage api={api} exportClient={exportClient} surveyId={surveyId} tenantId={session.me.tenantId} />;
}

function resetPagination(
  setCursor: (cursor: string | null) => void,
  setHistory: (history: Array<string | null>) => void,
) {
  setCursor(null);
  setHistory([]);
}

function totalResponses(summary: ResponseSummary) {
  return summary.total.inProgress + summary.total.engineCompleted + summary.total.deleted;
}

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

function isForbidden(error: unknown) {
  return error instanceof ApiError && error.kind === 'forbidden';
}

function publicMessage(error: unknown) {
  return error instanceof ApiError ? error.message : '操作失败，请稍后重试';
}

function responseStateLabel(state: ResponseState) {
  return stateOptions.find((option) => option.value === state)?.label ?? '未知状态';
}

function answersStatusLabel(status: ResponseRow['answersStatus']) {
  const labels: Record<ResponseRow['answersStatus'], string> = {
    available: '可查看',
    deleted: '答卷已删除',
    archived: '答案已归档',
    missing: '答案暂不可用',
  };
  return labels[status];
}

function exportStatusLabel(status: ExportJobView['status']) {
  const labels: Record<ExportJobView['status'], string> = {
    queued: '等待处理',
    running: '正在导出',
    completed: '导出完成',
    failed: '导出失败',
    cancelled: '已取消',
    expired: '文件已过期',
  };
  return labels[status];
}

function exportFormatLabel(format: ExportFormat) {
  return exportFormats.find((option) => option.value === format)?.label ?? format;
}

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value));
}
