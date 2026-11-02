import { useQuery } from '@tanstack/react-query';
import { z } from 'zod';
import { useAuth } from '../auth/AuthProvider';

const subscriptionSchema = z.object({ planVersionId: z.string().uuid(), planCode: z.string(), planVersion: z.number().int(), status: z.enum(['TRIAL', 'ACTIVE', 'EXPIRED', 'CANCELLED']), startsAt: z.string().datetime({ offset: true }), endsAt: z.string().datetime({ offset: true }), capabilities: z.array(z.string()), quotas: z.record(z.string(), z.number()), exportWindowDays: z.number().int() });
const labels = { TRIAL: '试用', ACTIVE: '有效', EXPIRED: '已到期', CANCELLED: '已取消' };
const quotaLabels: Record<string, string> = { 'member.seats': '成员席位', 'response.valid_completed': '有效答卷' };

export function useSubscription() {
  const { api, session } = useAuth();
  return useQuery({ queryKey: ['subscription', session?.me.tenantId, session?.me.actorId], queryFn: ({ signal }) => api.request({ path: '/v1/subscription', schema: subscriptionSchema, signal }), enabled: Boolean(session), retry: false, staleTime: 0 });
}

export function SubscriptionPage() {
  const query = useSubscription();
  return <main className="settings-page"><h1>当前订阅</h1>
    {query.isPending ? <p>正在加载订阅</p> : null}
    {query.isError ? <div role="alert"><p>无法查看订阅，请确认权限或联系管理员</p><button type="button" onClick={() => void query.refetch()}>重试</button></div> : null}
    {query.data && !query.isError && !query.isFetching ? <>
      <dl><dt>套餐</dt><dd>{query.data.planCode}</dd><dt>版本</dt><dd>{query.data.planVersion}</dd><dt>状态</dt><dd>{labels[query.data.status]}</dd><dt>开始时间</dt><dd>{query.data.startsAt}</dd><dt>结束时间</dt><dd>{query.data.endsAt}</dd></dl>
      <h2>额度</h2><dl>{Object.entries(query.data.quotas).map(([key, value]) => <div key={key}><dt>{quotaLabels[key] ?? key}</dt><dd>{value}</dd></div>)}</dl>
      <h2>包含的能力</h2><ul>{query.data.capabilities.map((capability) => <li key={capability}>{capability}</li>)}</ul>
      <p>到期后导出窗口：{query.data.exportWindowDays} 天</p>
    </> : null}
  </main>;
}
