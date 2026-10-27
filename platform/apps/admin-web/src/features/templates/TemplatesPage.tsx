import { useQuery } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { useAuth } from '../auth/AuthProvider';
import {
  listManagedTemplates, listPendingTemplates, listPlatformTemplates, listTemplates,
  platformTemplateListQueryKey, templateListQueryKey, templateManagedQueryKey, templatePendingQueryKey,
  type TemplateScopeFilter, type TemplateView,
} from '../../shared/api/templates';
import { PlatformTemplateImport } from './PlatformTemplateImport';
import './templates.css';

export const templateStatusLabel: Record<TemplateView['status'], string> = {
  draft: '草稿', pending: '待审核', published: '已发布', rejected: '已驳回', unpublished: '已下架',
};
const scopeLabel: Record<TemplateView['scope'], string> = { tenant: '租户模板', platform: '平台模板' };
type ViewFilter = 'all' | TemplateView['status'];
type ListMode = 'library' | 'managed' | 'pending' | 'platform';

export function TemplatesPage() {
  const { api, session } = useAuth();
  const tenantId = session?.me.tenantId ?? '';
  const [params, setParams] = useSearchParams();
  const modeParam = params.get('mode');
  const mode: ListMode = modeParam === 'managed' || modeParam === 'pending' || modeParam === 'platform'
    ? modeParam : 'library';
  const isOperator = session?.me.roles.includes('platform_operator') === true;
  const scopeValue = params.get('scope');
  const scope: TemplateScopeFilter = scopeValue === 'tenant' || scopeValue === 'platform' ? scopeValue : 'all';
  const viewValue = params.get('view');
  const view: ViewFilter = viewValue && viewValue in templateStatusLabel ? viewValue as ViewFilter : 'all';
  const query = useQuery({
    queryKey: mode === 'managed' ? templateManagedQueryKey(tenantId)
      : mode === 'pending' ? templatePendingQueryKey(tenantId)
      : mode === 'platform' ? platformTemplateListQueryKey(tenantId)
      : templateListQueryKey(tenantId, scope),
    queryFn: ({ signal }) => mode === 'managed' ? listManagedTemplates(api, signal)
      : mode === 'pending' ? listPendingTemplates(api, signal)
      : mode === 'platform' ? listPlatformTemplates(api, signal)
      : listTemplates(api, scope, signal),
    enabled: Boolean(tenantId && (mode !== 'platform' || isOperator)), retry: false,
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
    <nav aria-label="模板视图" className="template-modes">
      <Link to="/templates" aria-current={mode === 'library' ? 'page' : undefined}>可用模板</Link>
      <Link to="/templates?mode=managed" aria-current={mode === 'managed' ? 'page' : undefined}>我的管理模板</Link>
      <Link to="/templates?mode=pending" aria-current={mode === 'pending' ? 'page' : undefined}>待审核</Link>
      {isOperator ? <Link to="/templates?mode=platform" aria-current={mode === 'platform' ? 'page' : undefined}>平台模板管理</Link> : null}
    </nav>
    {mode === 'platform' && !isOperator ? <p role="alert">无权查看平台模板管理</p> : null}
    {mode !== 'platform' || isOperator ? <>
    <div className="templates-filters">
      {mode === 'library' ? <label>模板范围 <select aria-label="模板范围" value={scope} onChange={(event) => setFilter('scope', event.target.value)}>
        <option value="all">全部</option><option value="tenant">租户模板</option><option value="platform">平台模板</option>
      </select></label> : null}
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
      <td><Link to={`/templates/${item.id}${mode === 'library' ? '' : `?mode=${mode}`}`}>{item.name}</Link><small>{item.description}</small></td>
      <td>{scopeLabel[item.scope]}</td><td>{templateStatusLabel[item.status]}</td>
      <td>{item.publishedVersion ? `已发布 ${item.publishedVersion}` : `当前 ${item.currentVersion}`}</td>
      <td><time dateTime={item.updatedAt}>{new Date(item.updatedAt).toLocaleDateString('zh-CN')}</time></td>
    </tr>)}</tbody></table></div> : null}
    {mode === 'platform' && isOperator ? <PlatformTemplateImport api={api} tenantId={tenantId} /> : null}
    </> : null}
  </main>;
}
