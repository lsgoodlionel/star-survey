import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { z } from 'zod';
import { useAuth } from '../auth/AuthProvider';
import { audienceQueryKey, getSurveyAudience, setSurveyAudience } from '../../shared/api/audience';
import { contactListsQueryKey, createContactList, createRespondentContact, listContactLists } from '../../shared/api/contacts';
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
  const [subject, setSubject] = useState('邀请填写问卷');
  const [bodyTemplate, setBodyTemplate] = useState(defaultTemplate);
  const storageKey = `survey-delivery:${tenantId}:${actorId}:${surveyId}`;
  const [taskId, setTaskId] = useState<string | null>(() => sessionStorage.getItem(storageKey));

  const lists = useQuery({ queryKey: contactListsQueryKey(tenantId), queryFn: ({ signal }) => listContactLists(api, signal), retry: false });
  const audience = useQuery({ queryKey: audienceQueryKey(tenantId, surveyId), queryFn: ({ signal }) => getSurveyAudience(api, surveyId, signal), retry: false });
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
  const cancel = useMutation({
    mutationFn: () => cancelDeliveryTask(api, taskId!),
    onSuccess: (cancelled) => { client.setQueryData(deliveryTaskQueryKey(tenantId, surveyId, cancelled.id), cancelled); },
  });
  const taskUnavailable = isTaskUnavailable(task.error);
  const currentAudienceList = audience.data?.audience.listId ?? '';
  const effectiveListId = selectedListId || currentAudienceList;
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
function isDeliveryUnavailable(error: unknown) { return error instanceof ApiError && error.status === 503; }
function taskStatusLabel(status: string) { return ({ queued: '等待发送', running: '发送中', completed: '已完成', cancelled: '已取消', failed: '发送失败' } as Record<string, string>)[status] ?? status; }
