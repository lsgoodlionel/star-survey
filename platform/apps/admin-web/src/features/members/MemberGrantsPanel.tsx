import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { z } from 'zod';
import { useAuth } from '../auth/AuthProvider';

const rolesSchema = z.array(z.object({ code: z.string(), tenantWideOnly: z.boolean(), maxGrantHours: z.number().nullable(), requiresSeat: z.boolean() }));
const grantsSchema = z.object({ items: z.array(z.object({ id: z.string().uuid(), actorId: z.string(), roleCode: z.string(), resourceId: z.string().uuid().nullable(), expiresAt: z.string().nullable() })), nextCursor: z.string().uuid().nullable() });
export function MemberGrantsPanel({ members }: { members: { actorId: string }[] }) {
  const { api, session } = useAuth();
  const client = useQueryClient();
  const key = ['member-grants', session?.me.tenantId, session?.me.actorId];
  const [after, setAfter] = useState('');
  const [actor, setActor] = useState('');
  const [role, setRole] = useState('');
  const [resource, setResource] = useState('');
  const [expiry, setExpiry] = useState('');
  const [revoke, setRevoke] = useState<string | null>(null);
  const roles = useQuery({ queryKey: [...key, 'roles'], queryFn: ({ signal }) => api.request({ path: '/v1/member-grants/roles', schema: rolesSchema, signal }), retry: false, staleTime: 0 });
  const grants = useQuery({ queryKey: [...key, 'list', after], queryFn: ({ signal }) => api.request({ path: `/v1/member-grants${after ? `?after=${encodeURIComponent(after)}` : ''}`, schema: grantsSchema, signal }), retry: false, staleTime: 0 });
  const mutation = useMutation({
    mutationFn: (command: { action: 'grant' | 'revoke'; id?: string }) => command.action === 'grant'
      ? api.request({ path: '/v1/member-grants', method: 'POST', body: { actorId: actor, roleCode: role, resourceId: resource || null, expiresAt: expiry ? new Date(expiry).toISOString() : null }, schema: z.object({ id: z.string().uuid() }) })
      : api.request({ path: `/v1/member-grants/${encodeURIComponent(command.id ?? '')}/revoke`, method: 'POST', schema: z.undefined() }),
    onSuccess: async () => { setRevoke(null); await client.invalidateQueries({ queryKey: key }); },
    onError: () => { void client.invalidateQueries({ queryKey: key }); },
  });
  if (roles.isError || grants.isError) return <section><h2>成员授权</h2><div role="alert">无法加载授权，请确认权限后重试 <button type="button" onClick={() => { void roles.refetch(); void grants.refetch(); }}>重试</button></div></section>;
  if (!roles.data || !grants.data || roles.isFetching || grants.isFetching) return <p>正在加载成员授权</p>;
  return <section><h2>成员授权</h2>
    <table aria-label="授权列表"><thead><tr><th>成员</th><th>角色</th><th>范围</th><th>有效期</th><th>操作</th></tr></thead><tbody>
      {grants.data.items.map((grant) => <tr key={grant.id}><td>{grant.actorId}</td><td>{grant.roleCode}</td><td>{grant.resourceId ?? '整个租户'}</td><td>{grant.expiresAt ?? '无到期时间'}</td><td><button type="button" onClick={() => setRevoke(grant.id)}>撤销</button></td></tr>)}
    </tbody></table>
    {after ? <button type="button" onClick={() => setAfter('')}>授权首页</button> : null}
    {grants.data.nextCursor ? <button type="button" onClick={() => setAfter(grants.data.nextCursor!)}>下一页授权</button> : null}
    {revoke ? <div><p>确认撤销该授权？</p><button type="button" disabled={mutation.isPending} onClick={() => mutation.mutate({ action: 'revoke', id: revoke })}>确认撤销</button><button type="button" onClick={() => setRevoke(null)}>取消</button></div> : null}
    <form onSubmit={(event) => { event.preventDefault(); if (actor && role) mutation.mutate({ action: 'grant' }); }}>
      <label>授权成员<select required value={actor} onChange={(e) => setActor(e.target.value)}><option value="">请选择</option>{members.map((member) => <option key={member.actorId} value={member.actorId}>{member.actorId}</option>)}</select></label>
      <label>角色<select required value={role} onChange={(e) => setRole(e.target.value)}><option value="">请选择</option>{roles.data.map((item) => <option key={item.code} value={item.code}>{item.code}{item.maxGrantHours ? `（最长 ${item.maxGrantHours} 小时）` : ''}</option>)}</select></label>
      <label>资源标识（留空为整个租户）<input value={resource} onChange={(e) => setResource(e.target.value)} /></label>
      <label>到期时间（可选）<input type="datetime-local" value={expiry} onChange={(e) => setExpiry(e.target.value)} /></label>
      <button type="submit" disabled={mutation.isPending}>授予角色</button>
    </form>
    {mutation.isError ? <p role="alert">授权操作失败，请核对权限、范围和有效期</p> : null}
    {mutation.isSuccess ? <p role="status">授权操作已完成</p> : null}
  </section>;
}
