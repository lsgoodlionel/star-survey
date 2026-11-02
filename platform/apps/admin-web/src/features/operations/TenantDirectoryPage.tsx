import { useMutation, useQuery } from '@tanstack/react-query';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { useRef, useState, type ReactNode } from 'react';
import { z } from 'zod';
import { useAuth } from '../auth/AuthProvider';
import { ApiError } from '../../shared/api/errors';
import { usePlatformCapabilities } from './usePlatformCapabilities';

const tenantSchema = z.object({
  id: z.string().uuid(), code: z.string(), name: z.string(), status: z.string(),
  createdAt: z.string().datetime({ offset: true }),
});
const directorySchema = z.object({ items: z.array(tenantSchema), nextCursor: z.string().nullable() });
const statusLabels: Record<string, string> = { provisioning: '开通中', active: '启用', suspended: '暂停', closed: '关闭' };
const transitions: Record<string, string[]> = { provisioning: ['active', 'closed'], active: ['suspended', 'closed'], suspended: ['active', 'closed'], closed: [] };
const actionLabels: Record<string, string> = { active: '启用租户', suspended: '暂停租户', closed: '关闭租户' };

function OperationsLayout({ children }: { children: ReactNode }) {
  const { logout } = useAuth();
  return <main className="auth-page"><Link to="/templates">模板中心</Link> <button type="button" onClick={() => void logout()}>退出登录</button>{children}</main>;
}

export function TenantDirectoryPage() {
  const { api, session } = useAuth();
  const [params, setParams] = useSearchParams();
  const after = params.get('after') ?? '';
  const capability = usePlatformCapabilities();
  const allowed = !capability.isError && !capability.isFetching && capability.data?.managePlatform === true;
  const query = useQuery({
    queryKey: ['platform-tenant-directory', session?.me.tenantId, session?.me.actorId, after],
    queryFn: ({ signal }) => api.request({ path: `/v1/platform/tenants/directory?after=${encodeURIComponent(after)}`, schema: directorySchema, signal }),
    enabled: allowed, retry: false, staleTime: 0,
  });
  if (capability.isPending || capability.isFetching) return <OperationsLayout><p>正在检查平台权限</p></OperationsLayout>;
  if (!allowed) return <OperationsLayout><h1>需要平台运营权限</h1></OperationsLayout>;
  return <OperationsLayout><h1>平台租户</h1><Link to="/operations/plans">套餐目录</Link>
    {query.isPending ? <p>正在加载租户</p> : null}
    {query.isError ? <div role="alert"><p>无法加载租户，请确认权限后重试</p><button type="button" onClick={() => void query.refetch()}>重试</button></div> : null}
    {query.data && !query.isError && !query.isFetching ? <>
      {query.data.items.length === 0 ? <p>没有更多租户</p> : <table aria-label="租户列表"><thead><tr><th>名称</th><th>编码</th><th>状态</th></tr></thead><tbody>
        {query.data.items.map((tenant) => <tr key={tenant.id}><td><Link to={`/operations/tenants/${tenant.id}`}>{tenant.name}</Link></td><td>{tenant.code}</td><td>{statusLabels[tenant.status] ?? tenant.status}</td></tr>)}
      </tbody></table>}
      {after ? <button type="button" onClick={() => setParams({})}>返回首页</button> : null}
      {query.data.nextCursor ? <button type="button" onClick={() => setParams({ after: query.data.nextCursor! })}>下一页</button> : null}
    </> : null}
  </OperationsLayout>;
}

export function TenantDetailPage() {
  const { tenantId = '' } = useParams();
  const { session } = useAuth();
  return <TenantDetailContent key={`${session?.me.tenantId}:${session?.me.actorId}:${tenantId}`} tenantId={tenantId} />;
}

function TenantDetailContent({ tenantId }: { tenantId: string }) {
  const { api, session } = useAuth();
  const capability = usePlatformCapabilities();
  const [denied, setDenied] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const lock = useRef(false);
  const allowed = !denied && !capability.isError && !capability.isFetching && capability.data?.managePlatform === true;
  const query = useQuery({
    queryKey: ['platform-tenant', session?.me.tenantId, session?.me.actorId, tenantId],
    queryFn: ({ signal }) => api.request({ path: `/v1/platform/tenants/${encodeURIComponent(tenantId)}`, schema: tenantSchema, signal }),
    enabled: allowed, retry: false, staleTime: 0,
  });
  const change = useMutation({
    mutationFn: (status: string) => api.request({ path: `/v1/platform/tenants/${encodeURIComponent(tenantId)}/status`, method: 'POST', body: { status }, schema: tenantSchema }),
    onSuccess: async () => { setSelected(null); setNotice('租户状态已更新'); await query.refetch(); },
    onError: async (error) => {
      setSelected(null);
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) { setDenied(true); return; }
      setNotice(error instanceof ApiError && error.status === 409 ? '状态已变化，请核对最新状态' : '操作结果未确认，请核对最新状态后再试');
      await query.refetch();
    },
    onSettled: () => { lock.current = false; },
  });
  function confirm() {
    if (!selected || lock.current || !allowed || !query.data || !(transitions[query.data.status] ?? []).includes(selected)) return;
    lock.current = true;
    setNotice(null);
    change.mutate(selected);
  }
  if (capability.isPending || capability.isFetching) return <OperationsLayout><p>正在检查平台权限</p></OperationsLayout>;
  if (!allowed) return <OperationsLayout><h1>需要平台运营权限</h1></OperationsLayout>;
  return <OperationsLayout><Link to="/operations/tenants">返回租户列表</Link>
    {query.isPending ? <p>正在加载租户</p> : null}
    {query.isError ? <div role="alert"><p>租户不存在或无法访问</p><button type="button" onClick={() => void query.refetch()}>重试</button></div> : null}
    {query.data && !query.isError && !query.isFetching ? <><h1>{query.data.name}</h1><dl><dt>编码</dt><dd>{query.data.code}</dd><dt>状态</dt><dd>{statusLabels[query.data.status] ?? query.data.status}</dd><dt>创建时间</dt><dd>{query.data.createdAt}</dd></dl><Link to={`/operations/tenants/${encodeURIComponent(tenantId)}/engines`}>管理引擎实例</Link>
      <section aria-label="租户状态管理"><h2>租户状态管理</h2>
        {query.data.status === 'closed' ? <p>关闭为终态，无法重新启用</p> : null}
        {(transitions[query.data.status] ?? []).map((target) => <button key={target} type="button" disabled={change.isPending} onClick={() => setSelected(target)}>{actionLabels[target]}</button>)}
        {selected ? <div><p>{selected === 'closed' ? '关闭为终态，无法重新启用。' : '请确认变更租户状态。'}</p><button type="button" disabled={change.isPending} onClick={confirm}>确认{actionLabels[selected]}</button><button type="button" disabled={change.isPending} onClick={() => setSelected(null)}>取消</button></div> : null}
        {notice ? <p role="status">{notice}</p> : null}
      </section></> : null}
  </OperationsLayout>;
}
