import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useAuth } from '../auth/AuthProvider';
import { dashboardQueryKey } from '../../shared/api/dashboard';
import { getResourceCapabilities, listResources } from '../../shared/api/resources';
import type { ResourceView } from '../../shared/api/resources';
import { copyTemplate, getTemplate, templateDetailQueryKey } from '../../shared/api/templates';
import { templateStatusLabel } from './TemplatesPage';
import './templates.css';

const eventLabel: Record<string, string> = {
  created: '创建模板', version_added: '新增版本', submitted: '提交审核', approved: '审核通过',
  rejected: '审核驳回', unpublished: '下架模板', copied: '复制问卷', published: '发布模板',
};

export function TemplateDetailPage() {
  const { api, session } = useAuth();
  const tenantId = session?.me.tenantId ?? '';
  const { templateId = '' } = useParams();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const submitLock = useRef(false);
  const [parentId, setParentId] = useState('');
  const [folderTrail, setFolderTrail] = useState<ResourceView[]>([]);
  const [title, setTitle] = useState('');
  const detail = useQuery({ queryKey: templateDetailQueryKey(tenantId, templateId),
    queryFn: ({ signal }) => getTemplate(api, templateId, signal), enabled: Boolean(tenantId && templateId), retry: false });
  const projects = useQuery({ queryKey: ['template-destinations', tenantId, 'projects'],
    queryFn: ({ signal }) => listResources(api, { kind: 'project', archived: 'active', signal }),
    enabled: Boolean(tenantId && detail.data?.template.status === 'published'), retry: false });
  const folders = useQuery({ queryKey: ['template-destinations', tenantId, parentId, 'folders'],
    queryFn: ({ signal }) => listResources(api, { parentId, kind: 'folder', archived: 'active', signal }),
    enabled: Boolean(tenantId && parentId && detail.data?.template.status === 'published'), retry: false });
  const capabilities = useQuery({ queryKey: ['resource-capabilities', tenantId, parentId],
    queryFn: ({ signal }) => getResourceCapabilities(api, parentId, signal),
    enabled: Boolean(tenantId && parentId), retry: false });
  const copy = useMutation({ mutationFn: async () => copyTemplate(api, templateId, parentId, title.trim() || undefined),
    onSuccess: async (survey) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['resources', tenantId] }),
        queryClient.invalidateQueries({ queryKey: dashboardQueryKey(tenantId) }),
        queryClient.invalidateQueries({ queryKey: ['templates', tenantId] }),
      ]);
      void navigate(`/surveys/${survey.id}/edit?from=${encodeURIComponent(`/templates/${templateId}`)}`);
    },
    onSettled: () => { submitLock.current = false; },
  });
  const template = detail.data?.template;
  const canCopy = template?.status === 'published' && template.publishedVersion !== null;

  return <main className="templates-page">
    <Link to="/templates">返回模板中心</Link>
    {detail.isPending ? <p role="status">正在加载模板详情</p> : null}
    {detail.isError ? <div role="alert"><p>{'status' in detail.error && detail.error.status === 403 ? '无权查看模板' : '模板详情加载失败'}</p><button type="button" onClick={() => void detail.refetch()}>重试</button></div> : null}
    {template ? <>
      <header><h1>{template.name}</h1><p>{template.description}</p></header>
      <dl className="template-facts"><div><dt>状态</dt><dd>{templateStatusLabel[template.status]}</dd></div>
        <div><dt>范围</dt><dd>{template.scope === 'platform' ? '平台模板' : '租户模板'}</dd></div>
        <div><dt>可用版本</dt><dd>{template.publishedVersion ?? '暂无'}</dd></div></dl>
      <section><h2>版本记录</h2>{detail.data?.versions.length ? <ul>{detail.data.versions.map((version) =>
        <li key={version.versionNo}>版本 {version.versionNo} · {version.createdBy} · <time dateTime={version.createdAt}>{new Date(version.createdAt).toLocaleString('zh-CN')}</time>{version.sourceSurveyId ? ` · 来源问卷 ${version.sourceSurveyId}` : ''}</li>)}</ul> : <p>暂无版本</p>}</section>
      <section><h2>操作历史</h2>{detail.data?.history.length ? <ul>{detail.data.history.map((event, index) =>
        <li key={`${event.occurredAt}-${index}`}>{eventLabel[event.event] ?? '模板操作'} · {event.actor} · <time dateTime={event.occurredAt}>{new Date(event.occurredAt).toLocaleString('zh-CN')}</time>{event.reason ? ` · ${event.reason}` : ''}</li>)}</ul> : <p>暂无历史</p>}</section>
      {canCopy ? <section><h2>从模板创建问卷</h2><p>复制后问卷与模板各自独立。</p>
        <label>目标项目或文件夹 <select aria-label="目标项目或文件夹" value={parentId} onChange={(event) => {
          const nextId = event.target.value;
          const project = projects.data?.items.find((item) => item.id === nextId);
          if (project || !nextId) setFolderTrail([]);
          else {
            const oldIndex = folderTrail.findIndex((item) => item.id === nextId);
            if (oldIndex >= 0) setFolderTrail(folderTrail.slice(0, oldIndex + 1));
            else {
              const folder = folders.data?.items.find((item) => item.id === nextId);
              if (folder) setFolderTrail([...folderTrail, folder]);
            }
          }
          setParentId(nextId);
        }}>
          <option value="">请选择目标</option>
          {projects.data?.items.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
          {folderTrail.map((folder) => <option key={folder.id} value={folder.id}>{folder.name}</option>)}
          {folders.data?.items.map((folder) => <option key={folder.id} value={folder.id}>{folder.name}</option>)}
        </select></label>
        {projects.isError || folders.isError || capabilities.isError ? <p role="alert">目标位置加载失败，请重试</p> : null}
        <label>新问卷标题 <input aria-label="新问卷标题" value={title} onChange={(event) => setTitle(event.target.value)} maxLength={500} /></label>
        <button type="button" disabled={!parentId || capabilities.data?.canCreateChildren !== true || copy.isPending}
          onClick={() => { if (submitLock.current) return; submitLock.current = true; copy.mutate(); }}>从模板创建问卷</button>
        {copy.isError ? <p role="alert">创建失败，请检查目标位置后重试</p> : null}
      </section> : <p>当前状态不可复制；请使用已发布的模板。</p>}
    </> : null}
  </main>;
}
