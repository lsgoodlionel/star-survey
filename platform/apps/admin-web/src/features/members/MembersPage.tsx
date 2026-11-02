import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { z } from 'zod';
import { useAuth } from '../auth/AuthProvider';
import { ApiError } from '../../shared/api/errors';
import { MemberGrantsPanel } from './MemberGrantsPanel';

const pageSchema = z.object({ items: z.array(z.object({ actorId: z.string(), status: z.string(), usesSeat: z.boolean() })), nextCursor: z.string().nullable(), seatsInUse: z.number().int().nonnegative() });
const labels: Record<string, string> = { active: '在职', invited: '待接受' };
export function MembersPage() {
  const { session } = useAuth();
  return <MembersWorkspace key={`${session?.me.tenantId ?? ''}:${session?.me.actorId ?? ''}`} />;
}

function MembersWorkspace() {
  const { api, session } = useAuth();
  const client = useQueryClient();
  const [after, setAfter] = useState('');
  const [actorId, setActorId] = useState('');
  const [removeActor, setRemoveActor] = useState<string | null>(null);
  const [message, setMessage] = useState('');
  const key = ['members', session?.me.tenantId, session?.me.actorId];
  const query = useQuery({ queryKey: [...key, after], retry: false, staleTime: 0,
    queryFn: ({ signal }) => api.request({ path: `/v1/members?after=${encodeURIComponent(after)}`, schema: pageSchema, signal }) });
  const mutation = useMutation({
    mutationFn: ({ action, actor }: { action: 'invite' | 'remove'; actor: string }) => api.request({
      path: action === 'invite' ? '/v1/members' : '/v1/members/remove', method: 'POST',
      body: action === 'invite' ? { actorId: actor, kind: 'STAFF' } : { actorId: actor }, schema: z.undefined(),
    }),
    onSuccess: async (_, variables) => { setMessage(variables.action === 'invite' ? '邀请已提交' : '成员已移除'); setActorId(''); setRemoveActor(null); await client.invalidateQueries({ queryKey: key }); },
    onError: (error) => { setMessage(error instanceof ApiError && error.status === 409 ? '操作未完成：席位不足或最后一名所有者不能移除' : '操作未完成，请确认权限后重试'); void client.invalidateQueries({ queryKey: key }); },
  });
  const forbidden = query.error instanceof ApiError && query.error.status === 403;
  return <main className="settings-page"><h1>成员管理</h1>
    {query.isPending ? <p>正在加载成员</p> : null}
    {query.isError ? <div role="alert"><p>{forbidden ? '无权管理成员' : '成员加载失败，请重试'}</p><button type="button" onClick={() => void query.refetch()}>重试</button> <Link to="/dashboard">返回工作台</Link></div> : null}
    {query.data && !query.isError && !query.isFetching ? <>
      <button type="button" onClick={() => void query.refetch()}>刷新成员</button>
      <p>已占用席位：{query.data.seatsInUse}（含待接受邀请）</p>
      <table aria-label="成员列表"><thead><tr><th>成员标识</th><th>状态</th><th>席位</th><th>操作</th></tr></thead><tbody>
        {query.data.items.map((member) => <tr key={member.actorId}><td>{member.actorId}</td><td>{labels[member.status] ?? member.status}</td><td>{member.usesSeat ? '占用' : '不占用'}</td><td><button type="button" onClick={() => setRemoveActor(member.actorId)}>移除</button></td></tr>)}
      </tbody></table>
      {after ? <button type="button" onClick={() => setAfter('')}>返回首页</button> : null}
      {query.data.nextCursor ? <button type="button" onClick={() => setAfter(query.data.nextCursor!)}>下一页</button> : null}
      {removeActor ? <div><p>移除 {removeActor} 将撤销其授权并释放席位。</p><button type="button" disabled={mutation.isPending} onClick={() => mutation.mutate({ action: 'remove', actor: removeActor })}>确认移除</button><button type="button" onClick={() => setRemoveActor(null)}>取消</button></div> : null}
      <form onSubmit={(event) => { event.preventDefault(); if (actorId.trim()) mutation.mutate({ action: 'invite', actor: actorId.trim() }); }}>
        <label>成员标识<input required maxLength={200} value={actorId} onChange={(event) => setActorId(event.target.value)} /></label>
        <button type="submit" disabled={mutation.isPending}>邀请成员</button>
      </form>
      <p>邀请占用席位；组织身份需要另行绑定并由本人登录接受。</p>
      <MemberGrantsPanel members={query.data.items} />
    </> : null}
    {message ? <p role="status">{message}</p> : null}
  </main>;
}
