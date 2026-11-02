import { useQuery } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { z } from 'zod';
import { useAuth } from '../auth/AuthProvider';
import { usePlatformCapabilities } from './usePlatformCapabilities';

const schema = z.object({
  items: z.array(z.object({ id: z.string().uuid(), planCode: z.string(), version: z.number().int().positive(),
    capabilities: z.array(z.string()), quotas: z.record(z.string(), z.number().int().nonnegative()),
    exportWindowDays: z.number().int().nonnegative(), publishedAt: z.string().datetime({ offset: true }) })),
  nextCursor: z.string().nullable(),
});

export function PlanCatalogPage() {
  const { api, session, logout } = useAuth();
  const [params, setParams] = useSearchParams();
  const after = params.get('after') ?? '';
  const capability = usePlatformCapabilities();
  const allowed = !capability.isError && !capability.isFetching && capability.data?.managePlatform === true;
  const query = useQuery({ queryKey: ['platform-plan-catalog', session?.me.tenantId, session?.me.actorId, after],
    queryFn: ({ signal }) => api.request({ path: `/v1/platform/plan-catalog?after=${encodeURIComponent(after)}`, schema, signal }),
    enabled: allowed, staleTime: 0, retry: false });
  return <main className="auth-page"><Link to="/operations/tenants">返回平台租户</Link> <button type="button" onClick={() => void logout()}>退出登录</button>
    {capability.isPending || capability.isFetching ? <p>正在检查平台权限</p> : !allowed ? <h1>需要平台运营权限</h1> : <>
      <h1>套餐目录</h1>
      {query.isPending ? <p>正在加载套餐目录</p> : null}
      {query.isError ? <div role="alert"><p>无法加载套餐目录，请确认权限后重试</p><button type="button" onClick={() => void query.refetch()}>重试</button></div> : null}
      {query.data && !query.isFetching && !query.isError ? <>
        {query.data.items.length === 0 ? <p>没有更多套餐</p> : query.data.items.map((plan) => <section key={plan.id}>
          <h2>{plan.planCode}</h2><p>最新版本：{plan.version}</p><p>发布时间：{plan.publishedAt}</p>
          <h3>能力</h3>{plan.capabilities.length ? <ul>{plan.capabilities.map((code) => <li key={code}>{code}</li>)}</ul> : <p>无能力</p>}
          <h3>额度</h3>{Object.keys(plan.quotas).length ? <ul>{Object.entries(plan.quotas).map(([code, limit]) => <li key={code}>{code}: {limit}</li>)}</ul> : <p>无额度</p>}
          <p>导出窗口：<span>{plan.exportWindowDays} 天</span></p>
        </section>)}
        {after ? <button type="button" onClick={() => setParams({})}>返回首页</button> : null}
        {query.data.nextCursor ? <button type="button" onClick={() => setParams({ after: query.data.nextCursor! })}>下一页</button> : null}
      </> : null}
    </>}
  </main>;
}
