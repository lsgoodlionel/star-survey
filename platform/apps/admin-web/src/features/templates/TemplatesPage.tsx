import { useQuery } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { useAuth } from '../auth/AuthProvider';
import { listTemplates, templateListQueryKey, type TemplateScopeFilter, type TemplateView } from '../../shared/api/templates';
import './templates.css';

export const templateStatusLabel: Record<TemplateView['status'], string> = {
  draft: '草稿', pending: '待审核', published: '已发布', rejected: '已驳回', unpublished: '已下架',
};
const scopeLabel: Record<TemplateView['scope'], string> = { tenant: '租户模板', platform: '平台模板' };
type ViewFilter = 'all' | TemplateView['status'];

export function TemplatesPage() {
  const { api, session } = useAuth();
  const tenantId = session?.me.tenantId ?? '';
  const [params, setParams] = useSearchParams();
  const scopeValue = params.get('scope');
  const scope: TemplateScopeFilter = scopeValue === 'tenant' || scopeValue === 'platform' ? scopeValue : 'all';
  const viewValue = params.get('view');
  const view: ViewFilter = viewValue && viewValue in templateStatusLabel ? viewValue as ViewFilter : 'all';
  const query = useQuery({
    queryKey: templateListQueryKey(tenantId, scope),
    queryFn: ({ signal }) => listTemplates(api, scope, signal),
    enabled: Boolean(tenantId), retry: false,
  });
  const templates = (query.data ?? []).filter((item) => view === 'all' || item.status === view);

  function setFilter(key: string, value: string) {
    setParams((current) => {
      const next = new URLSearchParams(current);
      if (value === 'all') next.delete(key);
      else next.set(key, value);
      return next;
    });
  }

  return <main className="templates-page">
    <header><h1>模板中心</h1><p>选择已发布模板，创建独立的问卷草稿。</p></header>
    <div className="templates-filters">
      <label>模板范围 <select aria-label="模板范围" value={scope} onChange={(event) => setFilter('scope', event.target.value)}>
        <option value="all">全部</option><option value="tenant">租户模板</option><option value="platform">平台模板</option>
      </select></label>
      <label>模板状态 <select aria-label="模板状态" value={view} onChange={(event) => setFilter('view', event.target.value)}>
        <option value="all">全部状态</option>{Object.entries(templateStatusLabel).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select></label>
    </div>
    {query.isPending ? <p role="status">正在加载模板</p> : null}
    {query.isError ? <div role="alert"><p>{'status' in query.error && query.error.status === 403 ? '无权查看模板' : '模板加载失败，请稍后重试'}</p><button type="button" onClick={() => void query.refetch()}>重试</button></div> : null}
    {query.isSuccess && templates.length === 0 ? <p>暂无模板</p> : null}
    {query.isSuccess && templates.length > 0 ? <div className="templates-table-wrap"><table aria-label="模板列表"><thead><tr>
      <th scope="col">名称</th><th scope="col">范围</th><th scope="col">状态</th><th scope="col">版本</th><th scope="col">最近更新</th>
    </tr></thead><tbody>{templates.map((item) => <tr key={item.id}>
      <td><Link to={`/templates/${item.id}`}>{item.name}</Link><small>{item.description}</small></td>
      <td>{scopeLabel[item.scope]}</td><td>{templateStatusLabel[item.status]}</td>
      <td>{item.publishedVersion ? `已发布 ${item.publishedVersion}` : `当前 ${item.currentVersion}`}</td>
      <td><time dateTime={item.updatedAt}>{new Date(item.updatedAt).toLocaleDateString('zh-CN')}</time></td>
    </tr>)}</tbody></table></div> : null}
  </main>;
}
