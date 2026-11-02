import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { z } from 'zod';
import { useAuth } from '../auth/AuthProvider';
import { ApiError } from '../../shared/api/errors';
import { usePlatformCapabilities } from './usePlatformCapabilities';

const planSchema = z.object({ id: z.string().uuid(), planCode: z.string(), version: z.number().int().positive(),
    capabilities: z.array(z.string()), quotas: z.record(z.string(), z.string().regex(/^(0|[1-9][0-9]*)$/)),
    exportWindowDays: z.number().int().nonnegative(), publishedAt: z.string().datetime({ offset: true }) });
const schema = z.object({
  items: z.array(planSchema),
  nextCursor: z.string().nullable(),
});
const historySchema = z.object({ items: z.array(planSchema), nextCursor: z.number().int().nullable() });
const publishedSchema = z.object({ id: z.string().uuid(), planCode: z.string(), version: z.number().int().positive() });
const onboardedSchema = z.object({ tenantId: z.string().uuid(), ownerActorId: z.string() });
const definitionsSchema = z.object({ capabilities: z.array(z.object({ code: z.string(), description: z.string() })), meters: z.array(z.object({ code: z.string(), description: z.string() })) });
type Plan = z.infer<typeof planSchema>;

function quotaInput(raw: string): Record<string, string> {
  const value: unknown = JSON.parse(raw);
  const quotas = z.record(z.string().regex(/^[a-z][a-z0-9_.]{1,63}$/), z.string().regex(/^(0|[1-9][0-9]*)$/)).parse(value);
  if (Object.values(quotas).some((entry) => entry.length > 19 || BigInt(entry) > 9223372036854775807n)) throw new Error('额度超出范围');
  return quotas;
}

export function PlanCatalogPage() {
  const { session } = useAuth();
  return <PlanCatalogContent key={`${session?.me.tenantId}:${session?.me.actorId}`} />;
}

function PlanCatalogContent() {
  const { api, session, logout } = useAuth();
  const [params, setParams] = useSearchParams();
  const after = params.get('after') ?? '';
  const capability = usePlatformCapabilities();
  const [denied, setDenied] = useState(false);
  const [historyCode, setHistoryCode] = useState<string | null>(null);
  const [historyAfter, setHistoryAfter] = useState(0);
  const [editing, setEditing] = useState<Plan | 'new' | null>(null);
  const [selected, setSelected] = useState<Plan | null>(null);
  const [code, setCode] = useState('');
  const [capabilities, setCapabilities] = useState('');
  const [quotas, setQuotas] = useState('{}');
  const [windowDays, setWindowDays] = useState('30');
  const [targetTenant, setTargetTenant] = useState('');
  const [owner, setOwner] = useState('');
  const [kind, setKind] = useState('TRIAL');
  const [days, setDays] = useState('30');
  const [confirmPublish, setConfirmPublish] = useState(false);
  const [confirmOnboard, setConfirmOnboard] = useState(false);
  const [notice, setNotice] = useState('');
  const lock = useRef(false);
  const queryClient = useQueryClient();
  const allowed = !denied && !capability.isError && !capability.isFetching && capability.data?.managePlatform === true;
  const query = useQuery({ queryKey: ['platform-plan-catalog', session?.me.tenantId, session?.me.actorId, after],
    queryFn: ({ signal }) => api.request({ path: `/v1/platform/plan-catalog?after=${encodeURIComponent(after)}`, schema, signal }),
    enabled: allowed, staleTime: 0, retry: false });
  const history = useQuery({ queryKey: ['platform-plan-history', session?.me.tenantId, session?.me.actorId, historyCode, historyAfter],
    queryFn: ({ signal }) => api.request({ path: `/v1/platform/plan-catalog/${encodeURIComponent(historyCode!)}/versions?after=${historyAfter}`, schema: historySchema, signal }),
    enabled: allowed && historyCode !== null, staleTime: 0, retry: false });
  const definitions = useQuery({ queryKey: ['platform-plan-definitions', session?.me.tenantId, session?.me.actorId],
    queryFn: ({ signal }) => api.request({ path: '/v1/platform/plan-catalog/definitions', schema: definitionsSchema, signal }),
    enabled: allowed && editing !== null, staleTime: 0, retry: false });
  const fail = (error: Error) => {
    setConfirmPublish(false); setConfirmOnboard(false); setEditing(null); setSelected(null);
    if (error instanceof ApiError && (error.status === 401 || error.status === 403)) { setDenied(true); return; }
    setNotice(error instanceof ApiError && error.status === 409 ? '数据已变化，请刷新后核对'
      : error instanceof ApiError && error.status === 422 ? '套餐席位不足或开通条件不满足，未开通'
      : error instanceof ApiError && error.status === 400 ? '提交内容无效，未修改' : '操作结果未确认，请刷新核对后再试');
    void queryClient.invalidateQueries({ queryKey: ['platform-plan-catalog'] });
    void queryClient.invalidateQueries({ queryKey: ['platform-plan-history'] });
  };
  const publish = useMutation({ mutationFn: (body: { planCode: string; capabilities: string[]; quotas: Record<string, string>; exportWindowDays: number; expectedVersion: number }) =>
    api.request({ path: '/v1/platform/plans', method: 'POST', body, schema: publishedSchema }),
    onSuccess: async () => { setEditing(null); setConfirmPublish(false); setNotice('套餐版本已发布'); await queryClient.invalidateQueries({ queryKey: ['platform-plan-catalog'] }); await queryClient.invalidateQueries({ queryKey: ['platform-plan-history'] }); },
    onError: fail, onSettled: () => { lock.current = false; } });
  const onboard = useMutation({ mutationFn: (body: { planVersionId: string; kind: string; days: number; ownerActorId: string }) =>
    api.request({ path: `/v1/platform/tenants/${encodeURIComponent(targetTenant)}/onboarding`, method: 'POST', body, schema: onboardedSchema }),
    onSuccess: () => { setSelected(null); setConfirmOnboard(false); setNotice('租户订阅与所有者已开通'); },
    onError: fail, onSettled: () => { lock.current = false; } });
  function edit(plan: Plan | 'new') {
    setEditing(plan); setCode(plan === 'new' ? '' : plan.planCode);
    setCapabilities(plan === 'new' ? '' : plan.capabilities.join(','));
    setQuotas(plan === 'new' ? '{}' : JSON.stringify(plan.quotas));
    setWindowDays(plan === 'new' ? '30' : String(plan.exportWindowDays));
    setNotice(''); setConfirmPublish(false);
  }
  function publishBody() {
    const planCode = code.trim();
    if (!/^[a-z][a-z0-9_.-]{1,63}$/.test(planCode) || !/^[0-9]+$/.test(windowDays) || Number(windowDays) > 2147483647) throw new Error('套餐编码或导出窗口无效');
    const codes = capabilities.split(',').map((item) => item.trim()).filter(Boolean);
    if (codes.some((item) => !/^[a-z][a-z0-9_.]{1,63}$/.test(item)) || new Set(codes).size !== codes.length) throw new Error('能力编码无效');
    return { planCode, capabilities: codes, quotas: quotaInput(quotas), exportWindowDays: Number(windowDays), expectedVersion: editing === 'new' ? 0 : editing?.version ?? 0 };
  }
  function submitPublish() {
    if (lock.current || !allowed || !editing) return;
    try { const body = publishBody(); lock.current = true; publish.mutate(body); }
    catch { setNotice('请检查套餐编码、能力、额度字符串和导出窗口'); setConfirmPublish(false); }
  }
  function submitOnboard() {
    if (lock.current || !allowed || !selected) return;
    if (!z.string().uuid().safeParse(targetTenant).success || !owner.trim() || !/^[0-9]+$/.test(days) || Number(days) < 1 || Number(days) > 3650) {
      setNotice('请检查租户 ID、所有者标识和有效天数'); setConfirmOnboard(false); return;
    }
    lock.current = true;
    onboard.mutate({ planVersionId: selected.id, kind, days: Number(days), ownerActorId: owner.trim() });
  }
  const forbidden = (error: unknown) => error instanceof ApiError && (error.status === 401 || error.status === 403);
  const visible = allowed && !forbidden(query.error) && !forbidden(history.error) && !forbidden(definitions.error);
  return <main className="auth-page"><Link to="/operations/tenants">返回平台租户</Link> <button type="button" onClick={() => void logout()}>退出登录</button>
    {capability.isPending || capability.isFetching ? <p>正在检查平台权限</p> : !visible ? <h1>需要平台运营权限</h1> : <>
      <h1>套餐目录</h1>
      <button type="button" disabled={confirmPublish || confirmOnboard || publish.isPending || onboard.isPending} onClick={() => edit('new')}>发布新套餐</button>
      {query.isPending ? <p>正在加载套餐目录</p> : null}
      {query.isError ? <div role="alert"><p>无法加载套餐目录，请确认权限后重试</p><button type="button" onClick={() => void query.refetch()}>重试</button></div> : null}
      {query.data && !query.isFetching && !query.isError ? <>
        {query.data.items.length === 0 ? <p>没有更多套餐</p> : query.data.items.map((plan) => <section key={plan.id}>
          <h2>{plan.planCode}</h2><p>最新版本：{plan.version}</p><p>发布时间：{plan.publishedAt}</p>
          <button type="button" disabled={confirmOnboard || publish.isPending || onboard.isPending} onClick={() => { setHistoryCode(plan.planCode); setHistoryAfter(0); }}>查看 {plan.planCode} 版本</button>
          <button type="button" disabled={confirmPublish || confirmOnboard || publish.isPending || onboard.isPending} onClick={() => edit(plan)}>发布 {plan.planCode} 新版本</button>
          <h3>能力</h3>{plan.capabilities.length ? <ul>{plan.capabilities.map((code) => <li key={code}>{code}</li>)}</ul> : <p>无能力</p>}
          <h3>额度</h3>{Object.keys(plan.quotas).length ? <ul>{Object.entries(plan.quotas).map(([code, limit]) => <li key={code}>{code}: {limit}</li>)}</ul> : <p>无额度</p>}
          <p>导出窗口：<span>{plan.exportWindowDays} 天</span></p>
        </section>)}
        {after ? <button type="button" onClick={() => setParams({})}>返回首页</button> : null}
        {query.data.nextCursor ? <button type="button" onClick={() => setParams({ after: query.data.nextCursor! })}>下一页</button> : null}
      </> : null}
      {historyCode ? <section><h2>{historyCode} 版本历史</h2>
        {history.isError ? <p role="alert">无法加载版本历史</p> : null}
        {history.data && !history.isError && !history.isFetching ? <>{history.data.items.map((version) => <div key={version.id}><p>版本 {version.version} · {version.publishedAt}</p>
          <p>能力：{version.capabilities.join('、') || '无'}</p><p>导出窗口：{version.exportWindowDays} 天</p>
          <ul>{Object.entries(version.quotas).map(([key, value]) => <li key={key}>{key}: {value}</li>)}</ul>
          <button type="button" disabled={confirmPublish || confirmOnboard || publish.isPending || onboard.isPending} onClick={() => { setSelected(version); setConfirmOnboard(false); setNotice(''); }}>选择版本 {version.version} 开通租户</button></div>)}
          {historyAfter ? <button type="button" onClick={() => setHistoryAfter(0)}>返回版本首页</button> : null}
          {history.data.nextCursor ? <button type="button" onClick={() => setHistoryAfter(history.data.nextCursor!)}>下一页版本</button> : null}</> : null}
      </section> : null}
      {editing ? <section><h2>发布套餐版本</h2><label>套餐编码<input value={code} disabled={editing !== 'new' || confirmPublish} onChange={(e) => setCode(e.target.value)} /></label>
        {definitions.isError ? <p role="alert">无法加载可用能力和计量，请核对权限后重试</p> : null}
        {definitions.data && !definitions.isError ? <details><summary>可用能力和计量</summary><h3>能力</h3><ul>{definitions.data.capabilities.map((item) => <li key={item.code}>{item.code}：{item.description}</li>)}</ul><h3>计量</h3><ul>{definitions.data.meters.map((item) => <li key={item.code}>{item.code}：{item.description}</li>)}</ul></details> : null}
        <label>能力编码（逗号分隔）<input value={capabilities} disabled={confirmPublish} onChange={(e) => setCapabilities(e.target.value)} /></label>
        <label>额度 JSON<textarea value={quotas} disabled={confirmPublish} onChange={(e) => setQuotas(e.target.value)} /></label><p>额度值使用十进制字符串。</p>
        <label>导出窗口天数<input value={windowDays} disabled={confirmPublish} onChange={(e) => setWindowDays(e.target.value)} /></label>
        <button type="button" disabled={publish.isPending} onClick={() => { try { publishBody(); setConfirmPublish(true); setNotice(''); } catch { setNotice('请检查套餐编码、能力、额度字符串和导出窗口'); } }}>检查发布内容</button>
        {confirmPublish ? <div><p>发布不可修改的套餐版本。</p><button type="button" disabled={publish.isPending} onClick={submitPublish}>确认发布版本</button><button type="button" onClick={() => setConfirmPublish(false)}>取消发布</button></div> : null}
      </section> : null}
      {selected ? <section><h2>开通租户</h2><p>{selected.planCode} · 版本 {selected.version}</p>
        <label>租户 ID<input value={targetTenant} disabled={confirmOnboard} onChange={(e) => setTargetTenant(e.target.value)} /></label>
        <label>所有者标识<input value={owner} disabled={confirmOnboard} onChange={(e) => setOwner(e.target.value)} /></label>
        <label>订阅类型<select value={kind} disabled={confirmOnboard} onChange={(e) => setKind(e.target.value)}><option value="TRIAL">试用</option><option value="PAID">付费</option></select></label>
        <label>有效天数<input value={days} disabled={confirmOnboard} onChange={(e) => setDays(e.target.value)} /></label>
        <button type="button" disabled={confirmPublish || onboard.isPending || publish.isPending} onClick={() => setConfirmOnboard(true)}>检查开通内容</button>
        {confirmOnboard ? <div><p>将为指定租户创建订阅和所有者，请核对版本及租户。</p><button type="button" disabled={onboard.isPending} onClick={submitOnboard}>确认开通租户</button><button type="button" onClick={() => setConfirmOnboard(false)}>取消开通</button></div> : null}
      </section> : null}
      {notice ? <p role="status">{notice}</p> : null}
    </>}
  </main>;
}
