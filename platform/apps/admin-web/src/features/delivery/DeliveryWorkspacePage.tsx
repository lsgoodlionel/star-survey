import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../auth/AuthProvider';
import {
  assignContactTag,
  contactImportOutcomesQueryKey,
  contactImportQueryKey,
  contactListsQueryKey,
  contactTagsQueryKey,
  contactsQueryKey,
  createContactList,
  createContactTag,
  createOrgUnit,
  createRespondentContact,
  disableContact,
  getContactImport,
  listContactLists,
  listContacts,
  listContactTags,
  listContactImportOutcomes,
  listOrgUnits,
  orgUnitsQueryKey,
  submitContactImport,
} from '../../shared/api/contacts';
import './delivery-workspace.css';

export function DeliveryWorkspacePage() {
  const { api, session } = useAuth();
  const client = useQueryClient();
  const tenantId = session?.me.tenantId ?? '';
  const [listId, setListId] = useState('');
  const [listName, setListName] = useState('');
  const [unitName, setUnitName] = useState('');
  const [parentUnitId, setParentUnitId] = useState('');
  const [tagName, setTagName] = useState('');
  const [contactName, setContactName] = useState('');
  const [contactEmail, setContactEmail] = useState('');
  const [contactUnitId, setContactUnitId] = useState('');
  const [tagSelections, setTagSelections] = useState<Record<string, string>>({});
  const [importFormat, setImportFormat] = useState<'csv' | 'json'>('csv');
  const [importContent, setImportContent] = useState('');
  const [importJobId, setImportJobId] = useState<string | null>(null);

  const lists = useQuery({ queryKey: contactListsQueryKey(tenantId), queryFn: ({ signal }) => listContactLists(api, signal), enabled: Boolean(tenantId), retry: false });
  const units = useQuery({ queryKey: orgUnitsQueryKey(tenantId), queryFn: ({ signal }) => listOrgUnits(api, signal), enabled: Boolean(tenantId), retry: false });
  const tags = useQuery({ queryKey: contactTagsQueryKey(tenantId), queryFn: ({ signal }) => listContactTags(api, signal), enabled: Boolean(tenantId), retry: false });
  const effectiveListId = listId || lists.data?.[0]?.id || '';
  const contacts = useQuery({
    queryKey: contactsQueryKey(tenantId, effectiveListId),
    queryFn: ({ signal }) => listContacts(api, effectiveListId, signal),
    enabled: Boolean(effectiveListId), retry: false,
  });
  const importJob = useQuery({
    queryKey: contactImportQueryKey(tenantId, importJobId ?? 'none'),
    queryFn: ({ signal }) => getContactImport(api, importJobId!, signal),
    enabled: Boolean(importJobId), retry: false,
    refetchInterval: (query) => ['queued', 'running'].includes(query.state.data?.status ?? '') ? 1_500 : false,
  });
  const importOutcomes = useQuery({
    queryKey: contactImportOutcomesQueryKey(tenantId, importJobId ?? 'none', 1, 100),
    queryFn: ({ signal }) => listContactImportOutcomes(api, importJobId!, { signal }),
    enabled: Boolean(importJobId && importJob.data && !['queued', 'running'].includes(importJob.data.status)), retry: false,
  });

  const createList = useMutation({
    mutationFn: () => createContactList(api, { name: listName.trim(), kind: 'respondent', dedupeKey: 'email' }),
    onSuccess: async (created) => { setListName(''); setListId(created.id); await client.invalidateQueries({ queryKey: contactListsQueryKey(tenantId) }); },
  });
  const createUnit = useMutation({
    mutationFn: () => createOrgUnit(api, { name: unitName.trim(), parentId: parentUnitId || null }),
    onSuccess: async () => { setUnitName(''); setParentUnitId(''); await client.invalidateQueries({ queryKey: orgUnitsQueryKey(tenantId) }); },
  });
  const createTag = useMutation({
    mutationFn: () => createContactTag(api, tagName.trim()),
    onSuccess: async () => { setTagName(''); await client.invalidateQueries({ queryKey: contactTagsQueryKey(tenantId) }); },
  });
  const createContact = useMutation({
    mutationFn: () => createRespondentContact(api, { listId: effectiveListId, displayName: contactName.trim(), email: contactEmail.trim(), orgUnitId: contactUnitId || null }),
    onSuccess: async () => { setContactName(''); setContactEmail(''); setContactUnitId(''); await client.invalidateQueries({ queryKey: contactsQueryKey(tenantId, effectiveListId) }); },
  });
  const tagContact = useMutation({
    mutationFn: ({ contactId, tagId }: { contactId: string; tagId: string }) => assignContactTag(api, contactId, tagId),
    onSuccess: async () => client.invalidateQueries({ queryKey: contactsQueryKey(tenantId, effectiveListId) }),
  });
  const stopContact = useMutation({
    mutationFn: (contactId: string) => disableContact(api, contactId),
    onSuccess: async () => client.invalidateQueries({ queryKey: contactsQueryKey(tenantId, effectiveListId) }),
  });
  const startImport = useMutation({
    mutationFn: () => submitContactImport(api, effectiveListId, { format: importFormat, content: importContent, idempotencyKey: crypto.randomUUID() }),
    onSuccess: (job) => { setImportJobId(job.jobId); client.setQueryData(contactImportQueryKey(tenantId, job.jobId), job); },
  });

  const loadingError = lists.isError || units.isError || tags.isError;
  return <main className="delivery-workspace">
    <header>
      <p>投放工作区</p><h1>通讯录与投放</h1>
      <p>在当前租户管理联系人、部门、标签与名单，再进入具体问卷设置受众并创建投放任务。</p>
      <div className="delivery-workspace-actions"><Link to="/workspace">选择投放问卷</Link><Link to="/settings/email">邮件服务设置</Link></div>
    </header>
    {loadingError ? <p role="alert">通讯录数据加载失败，请检查权限或稍后重试。</p> : null}

    <section><h2>名单</h2>
      <div className="delivery-form-row"><label>当前名单<select aria-label="当前联系人名单" value={effectiveListId} onChange={(event) => setListId(event.target.value)}><option value="">请选择名单</option>{lists.data?.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      <label>新名单名称<input aria-label="投放工作区新名单名称" value={listName} onChange={(event) => setListName(event.target.value)} /></label>
      <button type="button" disabled={!listName.trim() || createList.isPending} onClick={() => createList.mutate()}>创建名单</button></div>
      {createList.isError ? <p role="alert">创建名单失败。</p> : null}
    </section>

    <section><h2>部门</h2>
      <div className="delivery-form-row"><label>上级部门<select aria-label="上级部门" value={parentUnitId} onChange={(event) => setParentUnitId(event.target.value)}><option value="">无（顶级部门）</option>{units.data?.map((unit) => <option key={unit.id} value={unit.id}>{unit.name}</option>)}</select></label>
      <label>部门名称<input aria-label="部门名称" value={unitName} onChange={(event) => setUnitName(event.target.value)} /></label>
      <button type="button" disabled={!unitName.trim() || createUnit.isPending} onClick={() => createUnit.mutate()}>创建部门</button></div>
      {units.data?.length ? <ul>{units.data.map((unit) => <li key={unit.id}>{unit.name}{unit.parentId ? '（下级部门）' : '（顶级部门）'}</li>)}</ul> : <p>暂无部门。</p>}
      {createUnit.isError ? <p role="alert">创建部门失败。</p> : null}
    </section>

    <section><h2>标签</h2>
      <div className="delivery-form-row"><label>标签名称<input aria-label="标签名称" value={tagName} onChange={(event) => setTagName(event.target.value)} /></label>
      <button type="button" disabled={!tagName.trim() || createTag.isPending} onClick={() => createTag.mutate()}>创建标签</button></div>
      {tags.data?.length ? <p>现有标签：{tags.data.map((tag) => tag.name).join('、')}</p> : <p>暂无标签。</p>}
      {createTag.isError ? <p role="alert">创建标签失败。</p> : null}
    </section>

    <section><h2>联系人</h2>
      <div className="delivery-form-row"><label>姓名<input aria-label="工作区联系人姓名" value={contactName} onChange={(event) => setContactName(event.target.value)} /></label>
      <label>邮箱<input aria-label="工作区联系人邮箱" type="email" value={contactEmail} onChange={(event) => setContactEmail(event.target.value)} /></label>
      <label>部门<select aria-label="联系人部门" value={contactUnitId} onChange={(event) => setContactUnitId(event.target.value)}><option value="">不指定</option>{units.data?.map((unit) => <option key={unit.id} value={unit.id}>{unit.name}</option>)}</select></label>
      <button type="button" disabled={!effectiveListId || !contactName.trim() || !contactEmail.trim() || createContact.isPending} onClick={() => createContact.mutate()}>新增联系人</button></div>
      {createContact.isError ? <p role="alert">新增联系人失败，请核对名单、邮箱与权限。</p> : null}
      {contacts.isError ? <p role="alert">联系人加载失败。</p> : null}
      {contacts.data?.items.length === 0 ? <p>当前名单暂无联系人。</p> : null}
      {contacts.data?.items.length ? <div className="delivery-contact-table" role="region" aria-label="联系人列表"><table><thead><tr><th>姓名</th><th>邮箱</th><th>部门</th><th>标签</th><th>状态与操作</th></tr></thead><tbody>{contacts.data.items.map((contact) => <tr key={contact.id}><td>{contact.displayName}</td><td>{contact.email ?? '—'}</td><td>{units.data?.find((unit) => unit.id === contact.orgUnitId)?.name ?? '—'}</td><td>{contact.tags.length ? contact.tags.join('、') : '—'}</td><td><div className="delivery-inline-actions"><select aria-label={`为 ${contact.displayName} 选择标签`} value={tagSelections[contact.id] ?? ''} onChange={(event) => setTagSelections((current) => ({ ...current, [contact.id]: event.target.value }))}><option value="">选择标签</option>{tags.data?.filter((tag) => !contact.tags.includes(tag.name)).map((tag) => <option key={tag.id} value={tag.id}>{tag.name}</option>)}</select><button type="button" disabled={!tagSelections[contact.id] || tagContact.isPending} onClick={() => tagContact.mutate({ contactId: contact.id, tagId: tagSelections[contact.id] })}>添加标签</button>{contact.status === 'active' ? <button type="button" disabled={stopContact.isPending} onClick={() => stopContact.mutate(contact.id)}>停用</button> : <span>已停用</span>}</div></td></tr>)}</tbody></table></div> : null}
    </section>

    <section><h2>批量导入任务</h2>
      <p>向当前名单提交 CSV 或 JSON。结果只显示行号和状态，不回显原始联系人内容。</p>
      <label>导入格式<select aria-label="投放工作区导入格式" value={importFormat} onChange={(event) => setImportFormat(event.target.value as 'csv' | 'json')}><option value="csv">CSV</option><option value="json">JSON</option></select></label>
      <label>导入内容<textarea aria-label="投放工作区导入内容" value={importContent} onChange={(event) => setImportContent(event.target.value)} placeholder={importFormat === 'csv' ? 'name,email\n王同学,student@example.test' : '[{"name":"王同学","email":"student@example.test"}]'} /></label>
      <button type="button" disabled={!effectiveListId || !importContent.trim() || startImport.isPending} onClick={() => startImport.mutate()}>提交导入任务</button>
      {startImport.isError ? <p role="alert">导入任务创建失败，请核对格式和权限。</p> : null}
      {importJob.data ? <div role="status"><p>状态：{importJob.data.status === 'completed' ? '已完成' : importJob.data.status === 'failed' ? '失败' : '处理中'}；已处理 {importJob.data.processedRows}/{importJob.data.totalRows} 行，新增 {importJob.data.created}、更新 {importJob.data.updated}、重复 {importJob.data.duplicate}、无效 {importJob.data.invalid}。</p>{importOutcomes.data?.length ? <ul>{importOutcomes.data.map((item) => <li key={item.rowNo}>第 {item.rowNo} 行：{item.outcome}{item.reason ? `（${item.reason}）` : ''}</li>)}</ul> : null}</div> : null}
    </section>
  </main>;
}
