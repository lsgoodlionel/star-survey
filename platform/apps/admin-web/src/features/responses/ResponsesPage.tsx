import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Ban, ChevronLeft, ChevronRight, Download, FileDown, RefreshCw } from 'lucide-react';
import { useParams, useSearchParams } from 'react-router-dom';
import { useSurveyPageReady } from '../../app/surveyShellContext';
import { useAuth } from '../auth/AuthProvider';
import { ApiError } from '../../shared/api/errors';
import {
  createExportClient,
  exportJobQueryKey,
  wordTemplatesQueryKey,
  type ExportClient,
  type ExportFormat,
  type ExportJobView,
} from '../../shared/api/exports';
import type { ApiClient } from '../../shared/api/http';
import {
  getResponseSummary,
  getResponseFields,
  listResponses,
  responseFieldsQueryKey,
  responsePageQueryKey,
  responseSummaryQueryKey,
  type ResponseFilters,
  type ResponseRow,
  type ResponseState,
  type ResponseSummary,
  type FieldDictionary,
} from '../../shared/api/responses';
import { parseSurveyIdParam } from '../publish/routeParams';
import './responses.css';

interface ResponsesPageProps {
  api: ApiClient;
  exportClient: ExportClient;
  surveyId: string;
  tenantId: string;
  actorId?: string;
  urlState?: {
    searchParams: URLSearchParams;
    setSearchParams: (next: URLSearchParams, options?: { replace?: boolean }) => void;
  };
}

const exportFormats: ReadonlyArray<{ value: ExportFormat; label: string }> = [
  { value: 'csv', label: 'CSV 数据包' },
  { value: 'xlsx', label: 'Excel 工作簿' },
  { value: 'sav', label: 'SPSS 数据包' },
  { value: 'docx', label: 'Word 答卷文档' },
  { value: 'pdf', label: 'PDF 答卷文档' },
  { value: 'report-docx', label: 'Word 统计报告' },
  { value: 'attachments', label: '附件包' },
];

const stateOptions: ReadonlyArray<{ value: ResponseState; label: string }> = [
  { value: 'engine_completed', label: '已完成' },
  { value: 'in_progress', label: '填写中' },
  { value: 'deleted', label: '已删除' },
];

export function ResponsesPage({ api, exportClient, surveyId, tenantId, actorId, urlState }: ResponsesPageProps) {
  const queryClient = useQueryClient();
  const [initialUrlState] = useState(() => readUrlState(urlState?.searchParams));
  const [state, setState] = useState<ResponseState | null>(initialUrlState.state);
  const [version, setVersion] = useState<number | null>(initialUrlState.version);
  const [startedFrom, setStartedFrom] = useState(initialUrlState.startedFrom);
  const [startedTo, setStartedTo] = useState(initialUrlState.startedTo);
  const [completedFrom, setCompletedFrom] = useState(initialUrlState.completedFrom);
  const [completedTo, setCompletedTo] = useState(initialUrlState.completedTo);
  const [fieldPickerOpen, setFieldPickerOpen] = useState(false);
  const [selectedFields, setSelectedFields] = useState<string[] | null>(initialUrlState.fields);
  const [cursor, setCursor] = useState<string | null>(null);
  const [cursorHistory, setCursorHistory] = useState<Array<string | null>>([]);
  const [format, setFormat] = useState<ExportFormat>('csv');
  const [wordTemplateVersion, setWordTemplateVersion] = useState('default');
  const [wordTemplateName, setWordTemplateName] = useState('');
  const [wordTemplateFile, setWordTemplateFile] = useState<File | null>(null);
  const [wordTemplateValidation, setWordTemplateValidation] = useState<string | null>(null);
  const [currentJob, setCurrentJob] = useState<ExportJobView | null>(null);
  const storageKey = actorId ? `survey-export:${tenantId}:${actorId}:${surveyId}` : null;
  const [currentJobId, setCurrentJobId] = useState<string | null>(() => storageKey
    ? sessionStorage.getItem(storageKey) : null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [refreshState, setRefreshState] = useState<'idle' | 'refreshing' | 'done'>('idle');
  const invalidTimeRange = Boolean((startedFrom && startedTo && startedFrom > startedTo)
    || (completedFrom && completedTo && completedFrom > completedTo));
  const filters: ResponseFilters = {
    state, version, cursor, limit: 50,
    startedFrom: toInstant(startedFrom), startedTo: toInstant(startedTo),
    completedFrom: toInstant(completedFrom), completedTo: toInstant(completedTo),
  };

  useEffect(() => {
    if (!urlState) return;
    const next = new URLSearchParams(urlState.searchParams);
    writeParam(next, 'state', state);
    writeParam(next, 'version', version === null ? null : String(version));
    writeParam(next, 'startedFrom', startedFrom || null);
    writeParam(next, 'startedTo', startedTo || null);
    writeParam(next, 'completedFrom', completedFrom || null);
    writeParam(next, 'completedTo', completedTo || null);
    writeParam(next, 'fields', selectedFields === null ? null : JSON.stringify(selectedFields));
    if (next.toString() !== urlState.searchParams.toString()) {
      urlState.setSearchParams(next, { replace: true });
    }
  }, [completedFrom, completedTo, selectedFields, startedFrom, startedTo, state, urlState, version]);

  const summary = useQuery({
    queryKey: responseSummaryQueryKey(tenantId, surveyId),
    queryFn: ({ signal }) => getResponseSummary(api, surveyId, signal),
    retry: false,
    staleTime: 0,
    refetchOnMount: 'always',
  });
  const responses = useQuery({
    queryKey: responsePageQueryKey(tenantId, surveyId, filters),
    queryFn: ({ signal }) => listResponses(api, surveyId, filters, signal),
    retry: false,
    staleTime: 0,
    refetchOnMount: 'always',
    enabled: !invalidTimeRange,
  });
  const fields = useQuery({
    queryKey: responseFieldsQueryKey(tenantId, surveyId),
    queryFn: ({ signal }) => getResponseFields(api, surveyId, signal),
    enabled: fieldPickerOpen && responses.isSuccess,
    retry: false,
  });
  const availableFields = useMemo(() => {
    const entries = fields.data?.versions.filter((item) => version === null || item.version === version)
      .flatMap((item) => item.fields) ?? [];
    return [...new Map(entries.map((field) => [field.fieldname, field])).values()];
  }, [fields.data, version]);
  const wordTemplates = useQuery({
    queryKey: wordTemplatesQueryKey(tenantId, surveyId),
    queryFn: ({ signal }) => exportClient.listWordTemplates(surveyId, signal),
    enabled: format === 'docx' && responses.isSuccess,
    retry: false,
  });
  const job = useQuery({
    queryKey: exportJobQueryKey(tenantId, surveyId, currentJobId ?? 'none'),
    queryFn: ({ signal }) => exportClient.get(currentJobId!, signal),
    enabled: Boolean(currentJobId),
    initialData: currentJob && currentJob.jobId === currentJobId ? currentJob : undefined,
    retry: false,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === 'queued' || status === 'running' ? 1_500 : false;
    },
  });
  const jobAccessRevoked = job.error instanceof ApiError
    && (job.error.kind === 'forbidden' || job.error.kind === 'not_found');
  const visibleJob = jobAccessRevoked ? null : (job.data ?? currentJob);

  const createJob = useMutation({
    mutationFn: () => exportClient.create(surveyId, {
      format,
      filter: { states: state ? [state] : null, versions: version ? [version] : null },
      templateVersion: format === 'docx' ? wordTemplateVersion : null,
    }, { idempotencyKey: crypto.randomUUID() }),
    onMutate: () => setDownloadError(null),
    onSuccess: (created) => {
      queryClient.setQueryData(exportJobQueryKey(tenantId, surveyId, created.jobId), created);
      setCurrentJob(created);
      setCurrentJobId(created.jobId);
      if (storageKey) sessionStorage.setItem(storageKey, created.jobId);
    },
  });
  const uploadWordTemplate = useMutation({
    mutationFn: ({ name, file }: { name: string; file: File }) =>
      exportClient.uploadWordTemplate(surveyId, name, file),
    onSuccess: async (created) => {
      queryClient.setQueryData(wordTemplatesQueryKey(tenantId, surveyId), (current: unknown) => {
        const templates = Array.isArray(current) ? current : [];
        return [created, ...templates.filter((item) => typeof item !== 'object' || item === null
          || !('id' in item) || item.id !== created.id)];
      });
      setWordTemplateVersion(customTemplateVersion(created.id, created.version));
      setWordTemplateName('');
      setWordTemplateFile(null);
      setWordTemplateValidation(null);
      await queryClient.invalidateQueries({ queryKey: wordTemplatesQueryKey(tenantId, surveyId) });
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
    onError: (error) => {
      setDownloadError(publicMessage(error));
      if (error instanceof ApiError && error.status === 410) void job.refetch();
    },
  });

  const summaryForbidden = isForbidden(summary.error);
  const detailForbidden = isForbidden(responses.error);
  const loadFailed = (summary.isError && !summaryForbidden) || (responses.isError && !detailForbidden);
  const ready = !summary.isFetching
    && !responses.isFetching
    && !loadFailed && !invalidTimeRange
    && Boolean(summary.data || summaryForbidden)
    && Boolean(responses.data || detailForbidden)
    && Boolean(summary.data || responses.data);
  useSurveyPageReady('responses', ready);
  const refreshResponses = async () => {
    setRefreshState('refreshing');
    try {
      await Promise.all([summary.refetch(), responses.refetch()]);
      setRefreshState('done');
    } catch {
      setRefreshState('idle');
    }
  };

  return (
    <main className="responses-page">
      <header className="responses-page__header">
        <div>
          <p>数据回收</p>
          <h2>答卷与导出</h2>
        </div>
        <div className="responses-page__refresh">
          <span aria-live="polite">
            {refreshState === 'refreshing' ? '正在刷新答卷数据' : null}
            {refreshState === 'done' ? '答卷数据已更新' : null}
          </span>
          <button type="button" disabled={refreshState === 'refreshing'} onClick={() => void refreshResponses()}>
            <RefreshCw aria-hidden="true" />刷新答卷数据
          </button>
        </div>
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
          <div><h3 id="response-list-title">答卷明细</h3><p>状态和版本筛选用于创建导出任务；时间与显示字段仅影响当前列表。</p></div>
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
            {([
              ['开始时间起', startedFrom, setStartedFrom],
              ['开始时间止', startedTo, setStartedTo],
              ['完成时间起', completedFrom, setCompletedFrom],
              ['完成时间止', completedTo, setCompletedTo],
            ] as const).map(([label, value, setter]) => (
              <label key={label}>{label}
                <input type="datetime-local" value={value} onChange={(event) => {
                  setter(event.target.value);
                  resetPagination(setCursor, setCursorHistory);
                }} />
              </label>
            ))}
          </div>
        ) : null}

        {invalidTimeRange ? <p role="alert">时间起点不能晚于终点，请调整筛选条件。</p> : null}
        {!detailForbidden && !invalidTimeRange ? (
          <div className="response-fields">
            <button type="button" onClick={() => setFieldPickerOpen((open) => !open)}>
              {fieldPickerOpen ? '收起显示字段' : '选择显示字段'}
            </button>
            {fieldPickerOpen && fields.isPending ? <span>正在加载字段</span> : null}
            {fieldPickerOpen && fields.isError ? <p role="alert">字段字典加载失败。<button type="button" onClick={() => void fields.refetch()}>重试</button></p> : null}
            {fieldPickerOpen && fields.data ? (
              <div className="response-fields__options" role="group" aria-label="显示字段">
                {availableFields.map((field) => (
                  <label key={field.fieldname}>
                    <input type="checkbox" checked={selectedFields === null || selectedFields.includes(field.fieldname)}
                      onChange={() => setSelectedFields((current) => {
                        const visible = current ?? availableFields.map((item) => item.fieldname);
                        return visible.includes(field.fieldname)
                          ? visible.filter((name) => name !== field.fieldname)
                          : [...visible, field.fieldname];
                      })} />
                    {field.label || field.code || field.fieldname}{field.sensitive ? '（敏感）' : ''}
                  </label>
                ))}
              </div>
            ) : null}
          </div>
        ) : null}

        {detailForbidden ? (
          <p className="responses-notice">你可以查看统计摘要，但没有查看答卷明细和导出的权限。</p>
        ) : responses.isPending ? (
          <p className="responses-empty">正在加载答卷</p>
        ) : responses.data ? (
          <>
            {responses.data.items.length ? <ResponseTable rows={responses.data.items} dictionary={fields.data} selectedFields={selectedFields} /> : (
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
            <div><h3 id="create-export-title">创建导出</h3><p>导出任务按创建时的状态和版本筛选快照执行。时间条件仅用于答卷列表，显示字段选择也不改变导出文件。</p></div>
          </div>
          <div className="export-create-row">
            <label>导出格式
              <select value={format} onChange={(event) => setFormat(event.target.value as ExportFormat)}>
                {exportFormats.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
            </label>
            {format === 'docx' ? <label>Word 模板
              <select value={wordTemplateVersion} onChange={(event) => setWordTemplateVersion(event.target.value)}>
                <option value="default">平台默认模板</option>
                {(wordTemplates.data ?? []).map((template) => (
                  <option key={`${template.id}:${template.version}`}
                    value={customTemplateVersion(template.id, template.version)}>
                    {template.name}（版本 {template.version}）
                  </option>
                ))}
              </select>
            </label> : null}
            <button type="button" disabled={createJob.isPending} onClick={() => createJob.mutate()}>
              <FileDown aria-hidden="true" />创建导出任务
            </button>
          </div>
          {format === 'docx' ? <section className="word-template-panel" aria-labelledby="word-template-title">
            <div><h4 id="word-template-title">自定义 Word 模板</h4>
              <p>仅上传受信任的 .docx 模板。平台只展示名称、版本和处理状态，不会在页面显示或回传模板正文。</p>
            </div>
            {wordTemplates.isPending ? <p role="status">正在加载自定义 Word 模板</p> : null}
            {wordTemplates.isError ? <p role="alert">自定义 Word 模板加载失败，可继续使用平台默认模板。
              <button type="button" onClick={() => void wordTemplates.refetch()}>重试加载模板</button></p> : null}
            <div className="word-template-upload">
              <label>模板名称<input value={wordTemplateName} maxLength={200}
                onChange={(event) => { setWordTemplateName(event.target.value); setWordTemplateValidation(null); }} /></label>
              <label>Word 模板文件<input type="file" accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                onChange={(event) => { setWordTemplateFile(event.target.files?.[0] ?? null); setWordTemplateValidation(null); }} /></label>
              <button type="button" disabled={uploadWordTemplate.isPending} onClick={() => {
                const name = wordTemplateName.trim();
                if (!name) { setWordTemplateValidation('请填写模板名称。'); return; }
                if (!wordTemplateFile) { setWordTemplateValidation('请选择 .docx 模板文件。'); return; }
                if (!wordTemplateFile.name.toLowerCase().endsWith('.docx')) {
                  setWordTemplateValidation('模板文件必须使用 .docx 格式。'); return;
                }
                uploadWordTemplate.mutate({ name, file: wordTemplateFile });
              }}>上传 Word 模板</button>
            </div>
            {wordTemplateValidation ? <p role="alert">{wordTemplateValidation}</p> : null}
            {uploadWordTemplate.isError ? <p role="alert">{publicMessage(uploadWordTemplate.error)}</p> : null}
            {uploadWordTemplate.isSuccess ? <p role="status">模板已上传，可用于本次导出。</p> : null}
          </section> : null}
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
          ) : currentJobId && job.isError ? (
            <div className="export-job-error" role="alert">
              <p>{jobAccessRevoked ? '当前导出任务不可访问。' : '导出任务加载失败，请重试。'}</p>
              <button type="button" onClick={() => void job.refetch()}>重新加载任务状态</button>
            </div>
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

function ResponseTable({ rows, dictionary, selectedFields }: {
  rows: ResponseRow[];
  dictionary?: FieldDictionary;
  selectedFields: string[] | null;
}) {
  return (
    <div className="response-table-wrap">
      <table>
        <thead><tr><th>答卷</th><th>状态</th><th>版本</th><th>开始/完成时间</th><th>作答内容</th></tr></thead>
        <tbody>{rows.map((row) => (
          <tr key={`${row.version}:${row.generation}:${row.responseId}`}>
            <td>#{row.responseId}</td>
            <td>{responseStateLabel(row.state)}</td>
            <td>版本 {row.version}</td>
            <td><time dateTime={row.startedAt}>{formatDateTime(row.startedAt)}</time>{row.completedAt ? <><br /><time dateTime={row.completedAt}>{formatDateTime(row.completedAt)}</time></> : null}</td>
            <td>{row.answers ? (
              <dl className="response-answers">{Object.entries(row.answers)
                .filter(([key]) => selectedFields === null || selectedFields.includes(key))
                .map(([key, value]) => (
                <div key={key}><dt>{fieldLabel(dictionary, row.version, key)}</dt><dd>{value ?? '未填写'}</dd></div>
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
      {job.status === 'failed' ? <p role="alert">导出失败，请按当前筛选重新创建任务。</p> : null}
      {job.status === 'expired' ? <p>导出文件已过期，请重新创建任务。</p> : null}
      {job.status === 'cancelled' ? <p>任务已取消，可按当前筛选重新创建。</p> : null}
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
  const [searchParams, setSearchParams] = useSearchParams();
  const exportClient = useMemo(() => createExportClient({
    getToken: () => session?.token ?? null,
    onUnauthorized: () => logout(),
  }), [logout, session?.token]);
  if (!session || !surveyId) return <p role="alert">问卷标识无效</p>;
  return (
    <ResponsesPage
      key={`${session.me.tenantId}:${session.me.actorId}:${surveyId}`}
      api={api}
      exportClient={exportClient}
      surveyId={surveyId}
      tenantId={session.me.tenantId}
      actorId={session.me.actorId}
      urlState={{ searchParams, setSearchParams }}
    />
  );
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

function toInstant(value: string): string | null {
  return value ? new Date(value).toISOString() : null;
}

function fieldLabel(dictionary: FieldDictionary | undefined, version: number, fieldname: string) {
  const field = dictionary?.versions.find((item) => item.version === version)
    ?.fields.find((item) => item.fieldname === fieldname);
  return field?.label || field?.code || fieldname;
}

function readUrlState(searchParams?: URLSearchParams) {
  const stateValue = searchParams?.get('state');
  const versionValue = Number(searchParams?.get('version'));
  return {
    state: stateOptions.some((option) => option.value === stateValue) ? stateValue as ResponseState : null,
    version: Number.isSafeInteger(versionValue) && versionValue > 0 ? versionValue : null,
    startedFrom: validLocalDateTime(searchParams?.get('startedFrom')),
    startedTo: validLocalDateTime(searchParams?.get('startedTo')),
    completedFrom: validLocalDateTime(searchParams?.get('completedFrom')),
    completedTo: validLocalDateTime(searchParams?.get('completedTo')),
    fields: parseFields(searchParams?.get('fields')),
  };
}

function validLocalDateTime(value: string | null | undefined) {
  if (!value || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value) || Number.isNaN(new Date(value).getTime())) return '';
  return value;
}

function parseFields(value: string | null | undefined): string[] | null {
  if (value == null) return null;
  try {
    const fields: unknown = JSON.parse(value);
    return Array.isArray(fields) && fields.every((field) => typeof field === 'string') ? [...new Set(fields)] : null;
  } catch {
    return null;
  }
}

function writeParam(params: URLSearchParams, name: string, value: string | null) {
  if (value === null) params.delete(name);
  else params.set(name, value);
}

function customTemplateVersion(id: string, version: number) {
  return `custom:${id}:${version}`;
}
