import { useMutation, useQuery } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { z } from 'zod';
import { useAuth } from '../auth/AuthProvider';
import { ApiError } from '../../shared/api/errors';
import { usePlatformCapabilities } from './usePlatformCapabilities';

const tenantSchema = z.object({ id: z.string().uuid(), code: z.string(), name: z.string(), status: z.enum(['provisioning', 'active', 'suspended', 'closed']) });
const engineSchema = z.object({ id: z.string(), tenantId: z.string().uuid(), baseUrl: z.string(),
  status: z.enum(['active', 'draining', 'retired']), createdAt: z.string().datetime({ offset: true }) });
const labels = { active: '接收流量', draining: '停止接收新流量', retired: '已退役' };
const transitions: Record<string, string[]> = { active: ['draining', 'retired'], draining: ['active', 'retired'], retired: [] };
const actions: Record<string, string> = { active: '恢复接收流量', draining: '停止接收新流量', retired: '退役实例' };
function displayUrl(value: string) {
  try { const url = new URL(value); return `${url.protocol}//${url.host}${url.pathname}`; }
  catch { return '地址不可显示'; }
}

export function EngineManagementPage() {
  const { tenantId = '' } = useParams();
  const { session } = useAuth();
  return <EngineManagementContent key={`${session?.me.tenantId}:${session?.me.actorId}:${tenantId}`} tenantId={tenantId} />;
}

function EngineManagementContent({ tenantId }: { tenantId: string }) {
  const { api, session, logout } = useAuth();
  const capability = usePlatformCapabilities();
  const [denied, setDenied] = useState(false);
  const [id, setId] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [registerConfirm, setRegisterConfirm] = useState(false);
  const [change, setChange] = useState<{ id: string; status: string } | null>(null);
  const [notice, setNotice] = useState('');
  const lock = useRef(false);
  const allowed = !denied && !capability.isError && !capability.isFetching && capability.data?.managePlatform === true;
  const validTenant = z.string().uuid().safeParse(tenantId).success;
  const path = `/v1/platform/tenants/${encodeURIComponent(tenantId)}`;
  const tenant = useQuery({ queryKey: ['platform-tenant', session?.me.tenantId, session?.me.actorId, tenantId],
    queryFn: ({ signal }) => api.request({ path, schema: tenantSchema.refine((value) => value.id === tenantId), signal }), enabled: allowed && validTenant, retry: false, staleTime: 0 });
  const instances = useQuery({ queryKey: ['platform-engine-instances', session?.me.tenantId, session?.me.actorId, tenantId],
    queryFn: ({ signal }) => api.request({ path: `${path}/engine-instances`, schema: z.array(engineSchema).refine((rows) => rows.every((row) => row.tenantId === tenantId)), signal }), enabled: allowed && validTenant, retry: false, staleTime: 0 });
  const failed = async (error: Error) => {
    setRegisterConfirm(false); setChange(null);
    if (error instanceof ApiError && (error.status === 401 || error.status === 403)) { setDenied(true); return; }
    setNotice(error instanceof ApiError && error.status === 409 ? '状态已变化或实例标识已占用，请核对最新数据' : '操作结果未确认，请核对最新数据后再试');
    await Promise.all([tenant.refetch(), instances.refetch()]);
  };
  const register = useMutation({ mutationFn: (body: { id: string; baseUrl: string }) => api.request({ path: `${path}/engine-instances`, method: 'POST', body, schema: engineSchema }),
    onSuccess: async () => { setRegisterConfirm(false); setId(''); setBaseUrl(''); setNotice('实例已登记'); await instances.refetch(); }, onError: failed, onSettled: () => { lock.current = false; } });
  const changeStatus = useMutation({ mutationFn: (value: { id: string; status: string }) => api.request({ path: `${path}/engine-instances/${encodeURIComponent(value.id)}/status`, method: 'POST', body: { status: value.status }, schema: engineSchema }),
    onSuccess: async () => { setChange(null); setNotice('实例状态已更新'); await instances.refetch(); }, onError: failed, onSettled: () => { lock.current = false; } });
  function validRegistration() {
    if (!/^[a-z0-9][a-z0-9-]{1,62}$/.test(id) || baseUrl.length > 2048) return false;
    try { const url = new URL(baseUrl); return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password && !url.search && !url.hash && /^https?:\/\/[A-Za-z0-9.-]+(?::[0-9]{1,5})?(?:\/[^\s@]*)?$/.test(baseUrl); }
    catch { return false; }
  }
  function confirmRegister() {
    if (lock.current || !allowed || !registerConfirm || tenant.data?.status === 'closed' || !validRegistration()) return;
    lock.current = true; setNotice(''); register.mutate({ id, baseUrl });
  }
  function confirmChange() {
    const row = instances.data?.find((item) => item.id === change?.id);
    if (lock.current || !allowed || !change || !row || !(transitions[row.status] ?? []).includes(change.status)) return;
    lock.current = true; setNotice(''); changeStatus.mutate(change);
  }
  const forbidden = (error: unknown) => error instanceof ApiError && (error.status === 401 || error.status === 403);
  const visible = allowed && !forbidden(tenant.error) && !forbidden(instances.error);
  const busy = register.isPending || changeStatus.isPending;
  return <main className="auth-page"><Link to={`/operations/tenants/${encodeURIComponent(tenantId)}`}>返回租户详情</Link> <button type="button" onClick={() => void logout()}>退出登录</button>
    {capability.isPending || capability.isFetching ? <p>正在检查平台权限</p> : !visible ? <h1>需要平台运营权限</h1> : <>
      <h1>引擎实例管理</h1>
      {!validTenant || tenant.isError || instances.isError ? <div role="alert"><p>无法加载租户或引擎实例</p>{validTenant ? <button type="button" onClick={() => { void tenant.refetch(); void instances.refetch(); }}>重试</button> : null}</div> : null}
      {tenant.data && instances.data && !tenant.isError && !instances.isError && !tenant.isFetching && !instances.isFetching ? <>
        <p>租户：{tenant.data.name}（{tenant.data.code}）</p>
        {tenant.data.status === 'closed' ? <p>租户已关闭，不能登记新实例。</p> : <section><h2>登记实例</h2>
          <label>实例标识<input value={id} disabled={registerConfirm || busy} onChange={(e) => setId(e.target.value)} /></label>
          <label>引擎基础地址<input value={baseUrl} disabled={registerConfirm || busy} onChange={(e) => setBaseUrl(e.target.value)} /></label>
          <p>只填写基础地址，不要包含凭据或查询参数。</p>
          <button type="button" disabled={busy || change !== null} onClick={() => { if (validRegistration()) { setNotice(''); setRegisterConfirm(true); } else setNotice('请检查实例标识和不含凭据的 HTTP(S) 地址'); }}>检查登记内容</button>
          {registerConfirm ? <div><p>确认登记此租户的实例；系统不会在此步骤验证引擎连接。</p><button type="button" disabled={busy} onClick={confirmRegister}>确认登记实例</button><button type="button" disabled={busy} onClick={() => setRegisterConfirm(false)}>取消登记</button></div> : null}
        </section>}
        <h2>已登记实例</h2>{instances.data.length === 0 ? <p>尚无实例</p> : instances.data.map((row) => <section key={row.id} aria-label={`实例 ${row.id}`}><h3>{row.id}</h3><p>地址：{displayUrl(row.baseUrl)}</p><p>状态：{labels[row.status]}</p>
          {row.status === 'retired' ? <p>退役为终态，无法恢复</p> : null}
          {transitions[row.status].map((status) => <button key={status} type="button" disabled={busy || registerConfirm || change !== null} onClick={() => { setChange({ id: row.id, status }); setNotice(''); }}>{actions[status]}</button>)}
          {change?.id === row.id ? <div><p>{change.status === 'retired' ? '退役后无法恢复。' : '请确认修改实例状态。'}</p><button type="button" disabled={busy} onClick={confirmChange}>确认{actions[change.status]}</button><button type="button" disabled={busy} onClick={() => setChange(null)}>取消操作</button></div> : null}
        </section>)}
      </> : null}
      {notice ? <p role="status">{notice}</p> : null}
    </>}
  </main>;
}
