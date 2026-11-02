import { useQuery } from '@tanstack/react-query';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import type { ReactNode } from 'react';
import { z } from 'zod';
import { useAuth } from '../auth/AuthProvider';

const tenantSchema = z.object({
  id: z.string().uuid(), code: z.string(), name: z.string(), status: z.string(),
  createdAt: z.string().datetime({ offset: true }),
});
const directorySchema = z.object({ items: z.array(tenantSchema), nextCursor: z.string().nullable() });
const statusLabels: Record<string, string> = { provisioning: '开通中', active: '启用', suspended: '暂停', closed: '关闭' };

function OperationsLayout({ children }: { children: ReactNode }) {
  const { logout } = useAuth();
  return <main className="auth-page"><Link to="/templates">模板中心</Link> <button type="button" onClick={() => void logout()}>退出登录</button>{children}</main>;
}

export function TenantDirectoryPage() {
  const { api, session } = useAuth();
  const [params, setParams] = useSearchParams();
  const after = params.get('after') ?? '';
  const allowed = session?.me.roles.includes('platform_operator') === true;
  const query = useQuery({
    queryKey: ['platform-tenant-directory', session?.me.tenantId, session?.me.actorId, after],
    queryFn: ({ signal }) => api.request({ path: `/v1/platform/tenants/directory?after=${encodeURIComponent(after)}`, schema: directorySchema, signal }),
    enabled: allowed, retry: false, staleTime: 0,
  });
  if (!allowed) return <main><h1>需要平台运营权限</h1></main>;
  return <OperationsLayout><h1>平台租户</h1>
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
  const { api, session } = useAuth();
  const allowed = session?.me.roles.includes('platform_operator') === true;
  const query = useQuery({
    queryKey: ['platform-tenant', session?.me.tenantId, session?.me.actorId, tenantId],
    queryFn: ({ signal }) => api.request({ path: `/v1/platform/tenants/${encodeURIComponent(tenantId)}`, schema: tenantSchema, signal }),
    enabled: allowed, retry: false, staleTime: 0,
  });
  if (!allowed) return <main><h1>需要平台运营权限</h1></main>;
  return <OperationsLayout><Link to="/operations/tenants">返回租户列表</Link>
    {query.isPending ? <p>正在加载租户</p> : null}
    {query.isError ? <div role="alert"><p>租户不存在或无法访问</p><button type="button" onClick={() => void query.refetch()}>重试</button></div> : null}
    {query.data && !query.isError && !query.isFetching ? <><h1>{query.data.name}</h1><dl><dt>编码</dt><dd>{query.data.code}</dd><dt>状态</dt><dd>{statusLabels[query.data.status] ?? query.data.status}</dd><dt>创建时间</dt><dd>{query.data.createdAt}</dd></dl></> : null}
  </OperationsLayout>;
}
