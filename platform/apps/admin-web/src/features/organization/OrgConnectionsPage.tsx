import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { z } from 'zod';
import { useAuth } from '../auth/AuthProvider';
import { orgCapabilitiesSchema } from '../../shared/api/organization';

const connectionSchema = z.object({ id: z.string().uuid(), provider: z.enum(['wecom', 'dingtalk', 'feishu']), corpId: z.string(), appId: z.string(), secretRef: z.string(), unknownUserPolicy: z.enum(['require_preauthorized', 'jit_staff']), enabled: z.boolean(), redirectUri: z.string().nullable(), eventTokenRef: z.string().nullable(), eventKeyRef: z.string().nullable(), eventUrl: z.string().nullable() });
type ActionResult = z.infer<typeof connectionSchema> | { checked: number; revoked: number; unknown: number } | { principalId: string; externalId: string };
const providerLabels = { wecom: '企业微信', dingtalk: '钉钉', feishu: '飞书' };
export function OrgConnectionsPage() {
  const { session } = useAuth();
  return <OrganizationWorkspace key={`${session?.me.tenantId ?? ''}:${session?.me.actorId ?? ''}`} />;
}
function OrganizationWorkspace() {
  const { api, session } = useAuth();
  const client = useQueryClient();
  const identity = [session?.me.tenantId, session?.me.actorId];
  const capabilityKey = ['organization-capabilities', ...identity];
  const listKey = ['organization-connections', ...identity];
  const capabilities = useQuery({ queryKey: capabilityKey, queryFn: ({ signal }) => api.request({ path: '/v1/org-connections/capabilities', schema: orgCapabilitiesSchema, signal }), retry: false, staleTime: 0 });
  const canRead = !capabilities.isFetching && !capabilities.isError && capabilities.data?.canManageSettings === true;
  const query = useQuery({ queryKey: listKey, queryFn: ({ signal }) => api.request({ path: '/v1/org-connections', schema: z.array(connectionSchema), signal }), enabled: canRead, retry: false, staleTime: 0 });
  const [provider, setProvider] = useState<'wecom' | 'dingtalk' | 'feishu'>('wecom');
  const [corp, setCorp] = useState(''); const [app, setApp] = useState(''); const [secretRef, setSecretRef] = useState('');
  const [policy, setPolicy] = useState<'require_preauthorized' | 'jit_staff'>('require_preauthorized');
  const [pending, setPending] = useState<{ type: 'toggle' | 'sync'; id: string; enabled?: boolean } | null>(null);
  const [externalIds, setExternalIds] = useState<Record<string, string>>({}); const [notice, setNotice] = useState('');
  const [editing, setEditing] = useState<{ id: string; secretRef: string; policy: 'require_preauthorized' | 'jit_staff'; eventTokenRef: string; eventKeyRef: string } | null>(null);
  const create = useMutation({ mutationFn: () => api.request({ path: '/v1/org-connections', method: 'POST', body: { provider, corpId: corp.trim(), appId: app.trim(), secretRef: secretRef.trim(), unknownUserPolicy: policy }, schema: connectionSchema }),
    onSuccess: async () => { setNotice('组织连接已创建'); setCorp(''); setApp(''); setSecretRef(''); await client.invalidateQueries({ queryKey: listKey }); }, onError: () => { setNotice('创建失败，请核对权限和配置'); void client.invalidateQueries({ queryKey: capabilityKey }); } });
  const saveSettings = useMutation({ mutationFn: (item: NonNullable<typeof editing>) => api.request({ path: `/v1/org-connections/${encodeURIComponent(item.id)}`, method: 'PUT', body: { secretRef: item.secretRef.trim(), unknownUserPolicy: item.policy, eventTokenRef: item.eventTokenRef.trim() || null, eventKeyRef: item.eventKeyRef.trim() || null }, schema: connectionSchema }),
    onSuccess: async () => { setNotice('连接设置已保存'); setEditing(null); await client.invalidateQueries({ queryKey: listKey }); }, onError: () => { setNotice('保存失败，请核对权限及引用名'); void client.invalidateQueries({ queryKey: capabilityKey }); } });
  const action = useMutation({ mutationFn: async (command: { type: 'toggle' | 'sync' | 'preauthorize'; id: string; enabled?: boolean; external?: string }): Promise<ActionResult> => {
    const path = `/v1/org-connections/${encodeURIComponent(command.id)}`;
    if (command.type === 'toggle') return api.request({ path, method: 'PUT', body: { enabled: command.enabled }, schema: connectionSchema });
    if (command.type === 'sync') return api.request({ path: `${path}/sync`, method: 'POST', schema: z.object({ checked: z.number(), revoked: z.number(), unknown: z.number() }) });
    return api.request({ path: `${path}/authorized-users`, method: 'POST', body: { externalId: command.external }, schema: z.object({ principalId: z.string().uuid(), externalId: z.string() }) });
  }, onSuccess: async (result, command) => { setNotice(command.type === 'sync' && 'checked' in result ? `同步结果：检查 ${result.checked}，撤销 ${result.revoked}，无法确认 ${result.unknown}` : command.type === 'preauthorize' ? '成员已预授权' : '连接状态已更新'); setPending(null); setExternalIds((current) => ({ ...current, [command.id]: '' })); await client.invalidateQueries({ queryKey: listKey }); }, onError: () => { setNotice('操作失败，请确认权限和连接状态'); void client.invalidateQueries({ queryKey: capabilityKey }); void client.invalidateQueries({ queryKey: listKey }); } });
  return <main className="settings-page"><h1>组织连接</h1>
    {capabilities.isFetching ? <p>正在检查权限</p> : null}
    {capabilities.isError || (!capabilities.isFetching && !capabilities.data?.canManageSettings) ? <div role="alert"><p>无权管理组织连接</p><button type="button" onClick={() => void capabilities.refetch()}>重试</button> <Link to="/dashboard">返回工作台</Link></div> : null}
    {canRead && query.isPending ? <p>正在加载组织连接</p> : null}
    {canRead && query.isError ? <div role="alert"><p>无法加载组织连接，请确认权限后重试</p><button type="button" onClick={() => void query.refetch()}>重试</button> <Link to="/dashboard">返回工作台</Link></div> : null}
    {canRead && query.data && !query.isFetching && !query.isError ? <>
      <p>配置中只填写部署方提供的密钥引用名，不输入密钥值。默认仅允许预授权成员登录。</p>
      <button type="button" onClick={() => { void capabilities.refetch(); void query.refetch(); }}>刷新连接</button>
      {query.data.length ? <ul>{query.data.map((item) => <li key={item.id}>
        <h2>{providerLabels[item.provider]}</h2><dl><dt>组织标识</dt><dd>{item.corpId}</dd><dt>应用标识</dt><dd>{item.appId}</dd><dt>密钥引用名</dt><dd>{item.secretRef}</dd><dt>状态</dt><dd>{item.enabled ? '已启用' : '已停用'}</dd><dt>未知成员策略</dt><dd>{item.unknownUserPolicy === 'jit_staff' ? '自动开通员工' : '仅预授权'}</dd><dt>登录回调地址</dt><dd>{item.redirectUri ?? '尚未配置'}</dd><dt>事件回调地址</dt><dd>{item.eventUrl ?? '尚未配置'}</dd></dl>
        <button type="button" onClick={() => setEditing({ id: item.id, secretRef: item.secretRef, policy: item.unknownUserPolicy, eventTokenRef: item.eventTokenRef ?? '', eventKeyRef: item.eventKeyRef ?? '' })}>编辑连接</button>
        {editing?.id === item.id ? <form onSubmit={(event) => { event.preventDefault(); if (Boolean(editing.eventTokenRef.trim()) === Boolean(editing.eventKeyRef.trim())) saveSettings.mutate(editing); }}>
          <label htmlFor={`edit-ref-${item.id}`}>新的密钥引用名</label><input id={`edit-ref-${item.id}`} required maxLength={64} value={editing.secretRef} onChange={(event) => setEditing({ ...editing, secretRef: event.target.value })} />
          <label htmlFor={`edit-policy-${item.id}`}>新的未知成员策略</label><select id={`edit-policy-${item.id}`} value={editing.policy} onChange={(event) => setEditing({ ...editing, policy: event.target.value as typeof editing.policy })}><option value="require_preauthorized">仅预授权</option><option value="jit_staff">自动开通员工（占用席位）</option></select>
          <label htmlFor={`event-token-${item.id}`}>事件 Token 引用名</label><input id={`event-token-${item.id}`} maxLength={64} value={editing.eventTokenRef} onChange={(event) => setEditing({ ...editing, eventTokenRef: event.target.value })} />
          <label htmlFor={`event-key-${item.id}`}>事件密钥引用名</label><input id={`event-key-${item.id}`} maxLength={64} value={editing.eventKeyRef} onChange={(event) => setEditing({ ...editing, eventKeyRef: event.target.value })} />
          <p>事件引用名必须成对填写；留空保留现有事件配置。</p><button type="submit" disabled={saveSettings.isPending || Boolean(editing.eventTokenRef.trim()) !== Boolean(editing.eventKeyRef.trim())}>保存连接设置</button><button type="button" onClick={() => setEditing(null)}>取消编辑</button>
        </form> : null}
        <button type="button" onClick={() => setPending({ type: 'toggle', id: item.id, enabled: !item.enabled })}>{item.enabled ? '停用连接' : '启用连接'}</button>
        {capabilities.data?.canManageMembers ? <><button type="button" onClick={() => setPending({ type: 'sync', id: item.id })}>同步组织</button>
          <form onSubmit={(event) => { event.preventDefault(); if (externalIds[item.id]?.trim()) action.mutate({ type: 'preauthorize', id: item.id, external: externalIds[item.id].trim() }); }}><label htmlFor={`external-${item.id}`}>组织成员标识</label><input id={`external-${item.id}`} value={externalIds[item.id] ?? ''} onChange={(event) => setExternalIds((current) => ({ ...current, [item.id]: event.target.value }))} required /><button type="submit" disabled={action.isPending}>预授权成员</button></form></> : null}
        {pending?.id === item.id ? <div><p>{pending.type === 'sync' ? '同步将访问组织供应商，并可能撤销已离职成员的权限。' : `确认${pending.enabled ? '启用' : '停用'}此连接？`}</p><button type="button" disabled={action.isPending} onClick={() => action.mutate(pending)}>确认操作</button><button type="button" onClick={() => setPending(null)}>取消</button></div> : null}
      </li>)}</ul> : <p>尚无组织连接</p>}
      <form onSubmit={(event) => { event.preventDefault(); create.mutate(); }}><h2>创建组织连接</h2>
        <label htmlFor="org-provider">供应商</label><select id="org-provider" value={provider} onChange={(event) => setProvider(event.target.value as typeof provider)}><option value="wecom">企业微信</option><option value="dingtalk">钉钉</option><option value="feishu">飞书</option></select>
        <label htmlFor="org-corp">组织标识</label><input id="org-corp" required maxLength={128} value={corp} onChange={(event) => setCorp(event.target.value)} />
        <label htmlFor="org-app">应用标识</label><input id="org-app" required maxLength={128} value={app} onChange={(event) => setApp(event.target.value)} />
        <label htmlFor="org-ref">密钥引用名</label><input id="org-ref" required maxLength={64} value={secretRef} onChange={(event) => setSecretRef(event.target.value)} />
        <label htmlFor="org-policy">未知成员策略</label><select id="org-policy" value={policy} onChange={(event) => setPolicy(event.target.value as typeof policy)}><option value="require_preauthorized">仅预授权</option><option value="jit_staff">自动开通员工（占用席位）</option></select>
        <button type="submit" disabled={create.isPending}>创建连接</button>
      </form>
    </> : null}
    {notice ? <p role="status">{notice}</p> : null}
  </main>;
}
