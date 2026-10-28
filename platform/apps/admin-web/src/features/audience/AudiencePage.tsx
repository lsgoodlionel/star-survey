import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { z } from 'zod';
import { useAuth } from '../auth/AuthProvider';
import { audienceQueryKey, getSurveyAudience, setSurveyAudience } from '../../shared/api/audience';
import {
  contactImportOutcomesQueryKey,
  contactImportQueryKey,
  contactListsQueryKey,
  createContactList,
  createRespondentContact,
  getContactImport,
  listContactImportOutcomes,
  listContactLists,
  submitContactImport,
  type ContactImportJob,
} from '../../shared/api/contacts';
import {
  cancelDeliveryTask,
  createAudienceDeliveryTask,
  deliveryTaskQueryKey,
  getDeliveryTask,
  listDeliveryRecipients,
  type DeliveryTask,
} from '../../shared/api/delivery';
import { ApiError } from '../../shared/api/errors';
import type { ApiClient } from '../../shared/api/http';
import './audience.css';

const defaultTemplate = '请点击 {{link}} 填写问卷。如不再接收，请点击 {{unsubscribe}}。';

export function AudienceRoutePage() {
  const { api, session } = useAuth();
  const parsedSurveyId = z.string().uuid().safeParse(useParams().surveyId);
  if (!session || !parsedSurveyId.success) return <p role="alert">问卷标识无效</p>;
  return <AudiencePage
    api={api}
    surveyId={parsedSurveyId.data}
    tenantId={session.me.tenantId}
    actorId={session.me.actorId}
  />;
}

export function AudiencePage({ api, surveyId, tenantId, actorId }: {
  api: ApiClient;
  surveyId: string;
  tenantId: string;
  actorId: string;
}) {
  const client = useQueryClient();
  const [listName, setListName] = useState('');
  const [selectedListId, setSelectedListId] = useState('');
  const [contactName, setContactName] = useState('');
  const [contactEmail, setContactEmail] = useState('');
  const [importFormat, setImportFormat] = useState<'csv' | 'json'>('csv');
  const [importContent, setImportContent] = useState('');
  const [subject, setSubject] = useState('邀请填写问卷');
  const [bodyTemplate, setBodyTemplate] = useState(defaultTemplate);
  const storageKey = `survey-delivery:${tenantId}:${actorId}:${surveyId}`;
  const [taskId, setTaskId] = useState<string | null>(() => sessionStorage.getItem(storageKey));

  const lists = useQuery({ queryKey: contactListsQueryKey(tenantId), queryFn: ({ signal }) => listContactLists(api, signal), retry: false });
  const audience = useQuery({ queryKey: audienceQueryKey(tenantId, surveyId), queryFn: ({ signal }) => getSurveyAudience(api, surveyId, signal), retry: false });
  const currentAudienceList = audience.data?.audience.listId ?? '';
  const effectiveListId = selectedListId || currentAudienceList;
  const importStorageKey = effectiveListId
    ? `contact-import:${tenantId}:${actorId}:${surveyId}:${effectiveListId}`
    : null;
  const [importScope, setImportScope] = useState(importStorageKey);
  const [importJobId, setImportJobId] = useState<string | null>(() => importStorageKey ? sessionStorage.getItem(importStorageKey) : null);
  if (importStorageKey !== importScope) {
    setImportScope(importStorageKey);
    setImportJobId(importStorageKey ? sessionStorage.getItem(importStorageKey) : null);
  }
  const createList = useMutation({
    mutationFn: () => createContactList(api, { name: listName.trim(), kind: 'respondent', dedupeKey: 'email' }),
    onSuccess: async (created) => {
      setListName(''); setSelectedListId(created.id);
      await client.invalidateQueries({ queryKey: contactListsQueryKey(tenantId) });
    },
  });
  const addContact = useMutation({
    mutationFn: (listId: string) => createRespondentContact(api, { listId, displayName: contactName.trim(), email: contactEmail.trim() }),
    onSuccess: async () => {
      setContactName(''); setContactEmail('');
      await client.invalidateQueries({ queryKey: audienceQueryKey(tenantId, surveyId) });
    },
  });
  const submitImport = useMutation({
    mutationFn: (listId: string) => submitContactImport(api, listId, {
      format: importFormat, content: importContent, idempotencyKey: crypto.randomUUID(),
    }),
    onSuccess: (created, listId) => {
      const key = `contact-import:${tenantId}:${actorId}:${surveyId}:${listId}`;
      setImportJobId(created.jobId);
      sessionStorage.setItem(key, created.jobId);
      client.setQueryData(contactImportQueryKey(tenantId, created.jobId), created);
    },
  });
  const saveAudience = useMutation({
    mutationFn: () => setSurveyAudience(api, surveyId, { listId: selectedListId }),
    onSuccess: (saved) => { client.setQueryData(audienceQueryKey(tenantId, surveyId), saved); },
  });
  const createTask = useMutation({
    mutationFn: () => createAudienceDeliveryTask(api, surveyId, {
      channel: 'email', subject: subject.trim() || null, bodyTemplate: bodyTemplate.trim(), idempotencyKey: crypto.randomUUID(),
    }),
    onSuccess: (created) => {
      setTaskId(created.id);
      sessionStorage.setItem(storageKey, created.id);
      client.setQueryData(deliveryTaskQueryKey(tenantId, surveyId, created.id), created);
    },
  });
  const task = useQuery({
    queryKey: deliveryTaskQueryKey(tenantId, surveyId, taskId ?? 'none'),
    queryFn: async ({ signal }) => {
      try {
        return await getDeliveryTask(api, taskId!, signal);
      } catch (error) {
        if (isTaskUnavailable(error)) sessionStorage.removeItem(storageKey);
        throw error;
      }
    }, enabled: Boolean(taskId), retry: false,
    refetchInterval: (query) => ['queued', 'running'].includes(query.state.data?.status ?? '') ? 1_500 : false,
  });
  const recipients = useQuery({
    queryKey: ['delivery-recipients', tenantId, taskId], queryFn: ({ signal }) => listDeliveryRecipients(api, taskId!, signal),
    enabled: Boolean(taskId && task.data), retry: false,
  });
  const importJob = useQuery({
    queryKey: contactImportQueryKey(tenantId, importJobId ?? 'none'),
    queryFn: async ({ signal }) => {
      try {
        return await getContactImport(api, importJobId!, signal);
      } catch (error) {
        if (isImportUnavailable(error) && importStorageKey) {
          sessionStorage.removeItem(importStorageKey);
        }
        throw error;
      }
    },
    enabled: Boolean(importJobId), retry: false,
    refetchInterval: (query) => ['queued', 'running'].includes(query.state.data?.status ?? '') ? 1_500 : false,
  });
  const importOutcomes = useQuery({
    queryKey: contactImportOutcomesQueryKey(tenantId, importJobId ?? 'none', 1, 100),
    queryFn: ({ signal }) => listContactImportOutcomes(api, importJobId!, { signal }),
    enabled: Boolean(importJobId && importJob.data && !['queued', 'running'].includes(importJob.data.status)), retry: false,
  });
  const cancel = useMutation({
    mutationFn: () => cancelDeliveryTask(api, taskId!),
    onSuccess: (cancelled) => { client.setQueryData(deliveryTaskQueryKey(tenantId, surveyId, cancelled.id), cancelled); },
  });
  const taskUnavailable = isTaskUnavailable(task.error);
  const importUnavailable = isImportUnavailable(importJob.error);
  return <main className="audience-page">
    <header><p>问卷投放</p><h2>受众与投放</h2><p>联系人地址只用于创建名单和联系人的受控操作；投放时由服务端按已保存受众冻结收件人。</p></header>
    {(lists.isError || (audience.isError && !isNotFound(audience.error))) ? <p role="alert">受众数据加载失败，请稍后重试。</p> : null}
    <section aria-labelledby="audience-lists-title"><h3 id="audience-lists-title">联系人名单</h3>
      <div className="audience-form-row"><label>新名单名称<input aria-label="新名单名称" value={listName} onChange={(event) => setListName(event.target.value)} /></label>
      <button type="button" disabled={!listName.trim() || createList.isPending} onClick={() => createList.mutate()}>创建名单</button></div>
      {createList.isError ? <p role="alert">创建名单失败，请检查权限或稍后重试。</p> : null}
      <label>用于当前问卷的名单<select aria-label="用于当前问卷的名单" value={effectiveListId} onChange={(event) => setSelectedListId(event.target.value)}>
        <option value="">请选择名单</option>{lists.data?.map((list) => <option key={list.id} value={list.id}>{list.name}</option>)}</select></label>
      {lists.data?.length === 0 ? <p>暂无联系人名单，请先创建名单。</p> : null}
    </section>
    <section aria-labelledby="audience-contact-title"><h3 id="audience-contact-title">新增联系人</h3>
      <div className="audience-form-row"><label>姓名<input aria-label="联系人姓名" value={contactName} onChange={(event) => setContactName(event.target.value)} /></label>
      <label>邮箱<input aria-label="联系人邮箱" type="email" value={contactEmail} onChange={(event) => setContactEmail(event.target.value)} /></label>
      <button type="button" disabled={!effectiveListId || !contactName.trim() || !contactEmail.trim() || addContact.isPending}
        onClick={() => { if (!selectedListId) setSelectedListId(effectiveListId); addContact.mutate(effectiveListId); }}>新增联系人</button></div>
      {addContact.isError ? <p role="alert">新增联系人失败，请检查名单和邮箱后重试。</p> : null}
    </section>
    <section aria-labelledby="audience-import-title"><h3 id="audience-import-title">批量导入联系人</h3>
      <p>仅提交到选中名单。支持 CSV 或 JSON；结果只显示行号、状态与原因，不回显原始联系人内容。</p>
      <label>导入格式<select aria-label="导入格式" value={importFormat} onChange={(event) => setImportFormat(event.target.value as 'csv' | 'json')}>
        <option value="csv">CSV</option><option value="json">JSON</option></select></label>
      <label>导入内容<textarea aria-label="导入内容" value={importContent} onChange={(event) => setImportContent(event.target.value)}
        placeholder={importFormat === 'csv' ? 'name,email\n王同学,student@example.test' : '[{"name":"王同学","email":"student@example.test"}]'} /></label>
      <button type="button" disabled={!effectiveListId || !importContent.trim() || submitImport.isPending}
        onClick={() => { if (!selectedListId) setSelectedListId(effectiveListId); submitImport.mutate(effectiveListId); }}>提交批量导入</button>
      {submitImport.isPending ? <p role="status">正在创建导入作业</p> : null}
      {submitImport.isError ? <p role="alert">创建导入作业失败，请检查名单、格式和权限后重试。</p> : null}
      {importUnavailable ? <p role="alert">导入作业不可访问或已不存在</p> : null}
      {importJob.isError && !importUnavailable ? <p role="alert">导入作业状态加载失败，请稍后重试。</p> : null}
      {importJob.data && !importUnavailable ? <ImportStatus job={importJob.data} outcomes={importOutcomes.data} outcomesError={importOutcomes.isError} /> : null}
    </section>
    <section aria-labelledby="audience-rule-title"><h3 id="audience-rule-title">当前问卷受众</h3>
      {audience.data ? <p>服务端当前可邀请 <strong>{audience.data.recipients}</strong> 人。</p> : <p>尚未设置受众。</p>}
      <button type="button" disabled={!effectiveListId || saveAudience.isPending} onClick={() => { if (!selectedListId) setSelectedListId(effectiveListId); saveAudience.mutate(); }}>保存当前受众</button>
      {saveAudience.isSuccess ? <p role="status">受众已保存，当前可邀请 {saveAudience.data.recipients} 人。</p> : null}
      {saveAudience.isError ? <p role="alert">保存受众失败，请检查问卷权限或稍后重试。</p> : null}
    </section>
    <section aria-labelledby="audience-delivery-title"><h3 id="audience-delivery-title">创建 Email 投放任务</h3>
      <p>提交后服务器从当前受众生成收件人快照，页面仅显示脱敏地址。</p>
      <label>邮件主题<input aria-label="邮件主题" value={subject} onChange={(event) => setSubject(event.target.value)} /></label>
      <label>邮件正文<textarea aria-label="邮件正文" value={bodyTemplate} onChange={(event) => setBodyTemplate(event.target.value)} /></label>
      <button type="button" disabled={!audience.data || audience.data.recipients === 0 || createTask.isPending} onClick={() => createTask.mutate()}>从受众创建 Email 任务</button>
      {createTask.isPending ? <p role="status">正在创建任务</p> : null}
      {isDeliveryUnavailable(createTask.error) ? <p role="alert">渠道未配置，未创建任务</p> : null}
      {createTask.isError && !isDeliveryUnavailable(createTask.error) ? <p role="alert">创建投放任务失败，请检查受众和邮件内容。</p> : null}
      {taskUnavailable ? <p role="alert">任务不可访问或已不存在</p> : null}
      {task.isError && !isTaskUnavailable(task.error) ? <p role="alert">任务状态加载失败，请稍后重试。</p> : null}
      {task.data && !taskUnavailable ? <TaskStatus task={task.data} recipients={recipients.data} recipientsError={recipients.isError} cancelling={cancel.isPending}
        cancelError={cancel.isError} onCancel={() => cancel.mutate()} /> : null}
    </section>
  </main>;
}

function ImportStatus({ job, outcomes, outcomesError }: { job: ContactImportJob; outcomes?: Array<{ rowNo: number; outcome: string; reason: string | null }>; outcomesError: boolean }) {
  return <section className="audience-task" aria-label="联系人导入状态"><h4>导入作业</h4>
    <p>状态：{importStatusLabel(job.status)}，已处理 {job.processedRows}/{job.totalRows} 行；新增 {job.created}、更新 {job.updated}、重复 {job.duplicate}、无效 {job.invalid}。</p>
    {outcomesError ? <p role="alert">导入行结果加载失败，请稍后重试。</p> : null}
    {outcomes?.length ? <ul aria-label="导入行结果">{outcomes.map((outcome) => <li key={outcome.rowNo}>第 {outcome.rowNo} 行：{importOutcomeLabel(outcome.outcome)}{outcome.reason ? `（${outcome.reason}）` : ''}</li>)}</ul> : null}
  </section>;
}

function TaskStatus({ task, recipients, recipientsError, cancelling, cancelError, onCancel }: { task: DeliveryTask; recipients?: Array<{ id: string; address: string; displayName: string | null; state: string }>; recipientsError: boolean; cancelling: boolean; cancelError: boolean; onCancel(): void }) {
  const cancellable = task.status === 'queued' || task.status === 'running';
  return <section className="audience-task" aria-label="投放任务状态"><h4>投放任务</h4><p>状态：{taskStatusLabel(task.status)}，收件人 {task.recipientCount} 人。</p>
    {cancellable ? <button type="button" disabled={cancelling} onClick={onCancel}>取消任务</button> : null}
    {cancelError ? <p role="alert">取消任务失败，请稍后重试。</p> : null}
    {recipientsError ? <p role="alert">收件人状态加载失败，请稍后重试。</p> : null}
    {recipients ? <ul aria-label="脱敏收件人">{recipients.map((recipient) => <li key={recipient.id}>{recipient.displayName || '未命名联系人'}：{recipient.address}（{recipient.state}）</li>)}</ul> : null}
  </section>;
}

function isNotFound(error: unknown) { return error instanceof ApiError && error.status === 404; }
function isTaskUnavailable(error: unknown) { return error instanceof ApiError && (error.status === 403 || error.status === 404); }
function isImportUnavailable(error: unknown) { return error instanceof ApiError && (error.status === 403 || error.status === 404); }
function isDeliveryUnavailable(error: unknown) { return error instanceof ApiError && error.status === 503; }
function taskStatusLabel(status: string) { return ({ queued: '等待发送', running: '发送中', completed: '已完成', cancelled: '已取消', failed: '发送失败' } as Record<string, string>)[status] ?? status; }
function importStatusLabel(status: string) { return ({ queued: '等待处理', running: '处理中', completed: '已完成', failed: '处理失败' } as Record<string, string>)[status] ?? status; }
function importOutcomeLabel(outcome: string) { return ({ created: '已新增', updated: '已更新', duplicate: '重复', invalid: '无效' } as Record<string, string>)[outcome] ?? outcome; }
